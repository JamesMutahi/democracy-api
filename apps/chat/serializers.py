import logging
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.db.models.signals import post_save
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.ballot.models import Ballot
from apps.ballot.serializers import BallotSerializer
from apps.broadcast.models import Broadcast
from apps.broadcast.serializers import BroadcastSerializer
from apps.chat.models import Asset, Chat, Message
from apps.constitution.models import Section
from apps.constitution.serializers import SectionSerializer
from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.posts.models import Post
from apps.posts.serializers import PostSerializer
from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer
from apps.utils.link_extractor import extract_linked_object
from apps.utils.presigned_url import generate_presigned_url, s3_client
from apps.utils.serializer_user import get_current_user

User = get_user_model()
logger = logging.getLogger(__name__)

LINK_FIELDS = {
    Post: "post",
    Ballot: "ballot",
    Survey: "survey",
    Petition: "petition",
    Broadcast: "broadcast",
    Section: "section",
}

MAX_ASSETS_PER_MESSAGE = getattr(settings, "CHAT_MAX_ASSETS_PER_MESSAGE", 10)
MAX_UPLOAD_SIZE = getattr(settings, "CHAT_MAX_UPLOAD_SIZE", 25 * 1024 * 1024)
ALLOWED_CONTENT_TYPES = getattr(settings, "CHAT_ALLOWED_CONTENT_TYPES", None)

CONTENT_TYPE_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


def is_blocked_pair(user, other):
    """
    Returns True if either user blocked the other.

    This is defensive because custom user models may not always expose
    the same blocked relationship.
    """
    if not user or not other:
        return False

    if user.pk == other.pk:
        return False

    blocked_manager = getattr(other, "blocked", None)
    if blocked_manager is not None and blocked_manager.filter(pk=user.pk).exists():
        return True

    blocked_manager = getattr(user, "blocked", None)
    if blocked_manager is not None and blocked_manager.filter(pk=other.pk).exists():
        return True

    return False


def can_user_access_chat(user, chat):
    if not user or not chat:
        return False

    if not chat.users.filter(pk=user.pk).exists():
        return False

    for participant in chat.users.exclude(pk=user.pk):
        if is_blocked_pair(user, participant):
            return False

    return True


def ensure_can_access_chat(user, chat):
    if not can_user_access_chat(user, chat):
        raise PermissionDenied("You cannot access this chat.")


def get_file_extension(name, content_type):
    name = name or ""

    if "." in name:
        extension = name.rsplit(".", 1)[-1].lower().strip()
        if extension and len(extension) <= 10:
            return f".{extension}"

    return CONTENT_TYPE_EXTENSION_MAP.get((content_type or "").lower(), "")


def build_asset_upload_data(assets):
    """
    Builds presigned upload URLs for assets that are not completed yet.
    """
    upload_data = []

    for asset in assets:
        if asset.is_completed:
            continue

        try:
            upload_url = generate_presigned_url(asset.file_key, asset.content_type)
        except Exception:
            logger.exception("Failed to generate presigned upload URL for asset %s", asset.id)
            upload_url = None

        upload_data.append(
            {
                "asset_id": str(asset.id),
                "name": asset.name,
                "url": upload_url,
            }
        )

    return upload_data


def get_or_create_direct_chat(user1, user2):
    """
    Returns or creates a Chat for 1:1 or self-chat.

    Self-chat:
        chat contains only one user.

    Normal DM:
        chat contains exactly two users.
    """
    if not user1 or not user2:
        raise ValueError("Both users are required.")

    num_users = 1 if user1.pk == user2.pk else 2
    user_ids = sorted({user1.pk, user2.pk})

    with transaction.atomic():
        # Lock involved user rows to reduce duplicate chat creation races.
        locked_users = list(User.objects.select_for_update().filter(pk__in=user_ids))
        if len(locked_users) != len(user_ids):
            raise ValueError("One or both users do not exist.")

        chat = (
            Chat.objects.annotate(num_users=Count("users", distinct=True))
            .filter(users=user1)
            .filter(users=user2)
            .filter(num_users=num_users)
            .first()
        )

        if chat:
            return chat

        chat = Chat.objects.create()

        if user1.pk == user2.pk:
            chat.users.add(user1)
        else:
            chat.users.add(user1, user2)

        return chat


class AssetSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "name",
            "file_key",
            "file_size",
            "content_type",
            "url",
            "is_completed",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "file_key",
            "is_completed",
            "created_at",
        ]

    def validate_file_size(self, value):
        if value is None:
            return value

        if value <= 0:
            raise ValidationError("File size must be greater than zero.")

        if value > MAX_UPLOAD_SIZE:
            raise ValidationError(
                f"File size exceeds maximum allowed size of {MAX_UPLOAD_SIZE} bytes."
            )

        return value

    def validate_content_type(self, value):
        if not value:
            raise ValidationError("Content type is required.")

        if ALLOWED_CONTENT_TYPES and value not in ALLOWED_CONTENT_TYPES:
            raise ValidationError("This content type is not allowed.")

        return value

    @staticmethod
    def get_url(obj):
        """
        Returns a temporary GET URL only after upload is completed.
        """
        if not obj.file_key or not obj.is_completed:
            return None

        try:
            return s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                    "Key": obj.file_key,
                },
                ExpiresIn=3600,
            )
        except Exception:
            logger.exception("Failed to generate presigned GET URL for asset %s", obj.id)
            return None


class MessageSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)

    post = PostSerializer(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    broadcast = BroadcastSerializer(read_only=True)
    section = SectionSerializer(read_only=True)

    post_id = serializers.PrimaryKeyRelatedField(
        queryset=Post.objects.all(),
        source="post",
        write_only=True,
        required=False,
        allow_null=True,
    )
    ballot_id = serializers.PrimaryKeyRelatedField(
        queryset=Ballot.objects.all(),
        source="ballot",
        write_only=True,
        required=False,
        allow_null=True,
    )
    survey_id = serializers.PrimaryKeyRelatedField(
        queryset=Survey.objects.all(),
        source="survey",
        write_only=True,
        required=False,
        allow_null=True,
    )
    petition_id = serializers.PrimaryKeyRelatedField(
        queryset=Petition.objects.all(),
        source="petition",
        write_only=True,
        required=False,
        allow_null=True,
    )
    broadcast_id = serializers.PrimaryKeyRelatedField(
        queryset=Broadcast.objects.all(),
        source="broadcast",
        write_only=True,
        required=False,
        allow_null=True,
    )
    section_id = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.all(),
        source="section",
        write_only=True,
        required=False,
        allow_null=True,
    )

    # Explicitly declared so DRF does not add UniqueValidator.
    # Duplicate UUIDs are handled as idempotent retries in create().
    uuid = serializers.UUIDField(required=False)

    assets = AssetSerializer(many=True, required=False, allow_empty=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "chat",
            "uuid",
            "author",
            "text",
            "post",
            "ballot",
            "survey",
            "petition",
            "broadcast",
            "section",
            "post_id",
            "ballot_id",
            "survey_id",
            "petition_id",
            "broadcast_id",
            "section_id",
            "location",
            "assets",
            "is_read",
            "is_edited",
            "is_deleted",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "author",
            "is_read",
            "is_edited",
            "is_deleted",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "chat": {"required": True},
        }

    def validate(self, attrs):
        user = get_current_user(self.context)

        if self.instance:
            if self.instance.is_deleted:
                raise ValidationError("Cannot modify a deleted message.")

            # Chat and uuid should never be changed after creation.
            attrs.pop("chat", None)
            attrs.pop("uuid", None)
        else:
            if not attrs.get("uuid"):
                attrs["uuid"] = uuid.uuid4()

            chat = attrs.get("chat")
            if chat and user:
                ensure_can_access_chat(user, chat)

        # Count linked objects after applying partial update values.
        final_linked_fields = {}

        for field_name in LINK_FIELDS.values():
            if field_name in attrs:
                value = attrs.get(field_name)
            else:
                value = getattr(self.instance, field_name, None) if self.instance else None

            if value:
                final_linked_fields[field_name] = value

        if len(final_linked_fields) > 1:
            raise ValidationError("Only one linked object can be attached to a message.")

        assets = attrs.get("assets")
        if assets is not None:
            if len(assets) > MAX_ASSETS_PER_MESSAGE:
                raise ValidationError(
                    f"Cannot attach more than {MAX_ASSETS_PER_MESSAGE} assets to one message."
                )

            if self.instance and assets:
                raise ValidationError("Assets can only be added during message creation.")

        text = attrs.get("text", getattr(self.instance, "text", "") if self.instance else "")

        if not self.instance:
            has_content = bool((text or "").strip()) or bool(assets) or bool(final_linked_fields)
            if not has_content:
                raise ValidationError(
                    "Message must contain text, assets, or a linked object."
                )

        return attrs

    def create(self, validated_data):
        user = get_current_user(self.context)

        validated_data["author"] = user

        text = validated_data.get("text") or ""

        linked_object = extract_linked_object(text=text) if text else None
        if linked_object:
            for model_class, field_name in LINK_FIELDS.items():
                if isinstance(linked_object, model_class) and not validated_data.get(field_name):
                    validated_data[field_name] = linked_object
                    break

        assets = validated_data.pop("assets", []) or []
        message_uuid = validated_data.get("uuid")

        # Idempotency: if client retries with same UUID, return the original message.
        if message_uuid:
            existing = Message.objects.filter(uuid=message_uuid).first()
            if existing:
                expected_chat = validated_data.get("chat")

                if existing.author_id == user.pk and (
                        not expected_chat or existing.chat_id == expected_chat.id
                ):
                    return existing

                raise ValidationError({"uuid": "Message with this uuid already exists."})

        try:
            with transaction.atomic():
                message = super().create(validated_data)

                for asset in assets:
                    name = asset.get("name") or "file"
                    content_type = asset.get("content_type") or "application/octet-stream"
                    file_size = asset.get("file_size") or 0

                    extension = get_file_extension(name, content_type)
                    file_key = (
                        f"uploads/{message.author_id}/messages/"
                        f"{message.uuid}/{uuid.uuid4().hex}{extension}"
                    )

                    Asset.objects.create(
                        message=message,
                        file_key=file_key,
                        name=name,
                        file_size=file_size,
                        content_type=content_type,
                    )

                # Notify chat list subscribers after the transaction commits.
                transaction.on_commit(
                    lambda: post_save.send(sender=Chat, instance=message.chat, created=False)
                )

        except IntegrityError:
            # Very small race window for duplicate UUIDs.
            if message_uuid:
                existing = Message.objects.filter(uuid=message_uuid).first()
                if existing:
                    return existing
            raise

        return message

    def update(self, instance, validated_data):
        # Assets are upload-time objects. Do not allow replacement via PATCH.
        validated_data.pop("assets", None)
        validated_data.pop("chat", None)
        validated_data.pop("uuid", None)

        editable_fields = {"text", *LINK_FIELDS.values()}

        if any(field in validated_data for field in editable_fields):
            validated_data["is_edited"] = True

        with transaction.atomic():
            instance = super().update(instance, validated_data)

            transaction.on_commit(
                lambda: post_save.send(sender=Chat, instance=instance.chat, created=False)
            )

        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if instance.is_deleted:
            data["text"] = ""
            data["assets"] = []

            for field_name in LINK_FIELDS.values():
                data[field_name] = None

        return data


class ChatSerializer(serializers.ModelSerializer):
    users = UserSerializer(many=True, read_only=True)

    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    last_message = serializers.SerializerMethodField(read_only=True)
    unread_messages = serializers.SerializerMethodField(read_only=True)
    is_self_chat = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Chat
        fields = [
            "id",
            "users",
            "last_message",
            "unread_messages",
            "user",
            "is_self_chat",
        ]
        read_only_fields = [
            "last_message",
            "unread_messages",
            "is_self_chat",
        ]

    def validate_user(self, value):
        current_user = get_current_user(self.context)

        if current_user and value and value.pk != current_user.pk:
            if is_blocked_pair(current_user, value):
                raise PermissionDenied("You cannot start a chat with this user.")

        return value

    def get_last_message(self, obj: Chat):
        queryset = (
            Message.objects.filter(chat=obj, is_deleted=False)
            .select_related("author", "chat")
            .prefetch_related("assets")
        )

        latest_message_id = getattr(obj, "latest_message_id", None)

        message = None

        if latest_message_id:
            message = queryset.filter(pk=latest_message_id).first()

        if message is None:
            message = queryset.order_by("-created_at", "-id").first()

        if not message:
            return None

        return MessageSerializer(message, context=self.context).data

    def get_unread_messages(self, obj: Chat):
        if hasattr(obj, "unread_messages_count"):
            return obj.unread_messages_count

        user = get_current_user(self.context)

        if not user:
            return 0

        return obj.messages.filter(is_read=False, is_deleted=False).exclude(author=user).count()

    @staticmethod
    def get_is_self_chat(obj: Chat):
        user_count = getattr(obj, "user_count", None)

        if user_count is not None:
            return user_count == 1

        return obj.users.count() == 1

    def create(self, validated_data):
        current_user = get_current_user(self.context)
        target_user = validated_data.get("user")

        if not current_user:
            raise PermissionDenied("Authenticated user is required.")

        if not target_user:
            raise ValidationError({"user": "This field is required."})

        if is_blocked_pair(current_user, target_user):
            raise PermissionDenied("You cannot start a chat with this user.")

        return get_or_create_direct_chat(current_user, target_user)
