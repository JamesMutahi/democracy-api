import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.db.models.signals import post_save
from rest_framework import serializers

from apps.ballot.models import Ballot
from apps.ballot.serializers import BallotSerializer
from apps.chat.models import Message, Chat, Asset
from apps.constitution.models import Section
from apps.constitution.serializers import SectionSerializer
from apps.broadcast.models import Broadcast
from apps.broadcast.serializers import BroadcastSerializer
from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.posts.models import Post
from apps.posts.serializers import PostSerializer
from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer
from apps.utils.link_extractor import extract_linked_object
from apps.utils.presigned_url import s3_client
from apps.utils.serializer_user import get_current_user

User = get_user_model()


class AssetSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            'id',
            'name',
            'file_key',
            'file_size',
            'content_type',
            'url',  # External S3 URL for the frontend
            'is_completed',  # Status of the upload
            'created_at',
        ]
        # Prevents the frontend from trying to overwrite the S3 path
        read_only_fields = ['id', 'file_key', 'is_completed', 'created_at']

    @staticmethod
    def get_url(obj):
        if not obj.file_key:
            return None

        # Generates a temporary GET link
        return s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': obj.file_key
            },
            ExpiresIn=3600
        )


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
        source='post',
        write_only=True,
        required=False,
        allow_null=True
    )
    ballot_id = serializers.PrimaryKeyRelatedField(
        queryset=Ballot.objects.all(),
        source='ballot',
        write_only=True,
        required=False,
        allow_null=True
    )
    survey_id = serializers.PrimaryKeyRelatedField(
        queryset=Survey.objects.all(),
        source='survey',
        write_only=True,
        required=False,
        allow_null=True
    )
    petition_id = serializers.PrimaryKeyRelatedField(
        queryset=Petition.objects.all(),
        source='petition',
        write_only=True,
        required=False,
        allow_null=True
    )
    broadcast_id = serializers.PrimaryKeyRelatedField(
        queryset=Broadcast.objects.all(),
        source='broadcast',
        write_only=True,
        required=False,
        allow_null=True
    )
    section_id = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.all(),
        source='section',
        write_only=True,
        required=False,
        allow_null=True
    )
    assets = AssetSerializer(many=True, default=[])

    class Meta:
        model = Message
        fields = [
            'id',
            'chat',
            'uuid',
            'author',
            'text',
            'post',
            'ballot',
            'survey',
            'petition',
            'broadcast',
            'section',
            'post_id',
            'ballot_id',
            'survey_id',
            'petition_id',
            'broadcast_id',
            'section_id',
            'location',
            'assets',
            'is_read',
            'is_edited',
            'is_deleted',
            'created_at',
            'updated_at'
        ]

    def create(self, validated_data):
        validated_data['author'] = get_current_user(self.context)

        # Extract object if link is present in message text
        linked_object = extract_linked_object(text=validated_data['text'])
        if linked_object:
            if isinstance(linked_object, Post) and not validated_data.get('post_id'):
                validated_data['post_id'] = linked_object.pk
            if isinstance(linked_object, Ballot) and not validated_data.get('ballot_id'):
                validated_data['ballot_id'] = linked_object.pk
            if isinstance(linked_object, Survey) and not validated_data.get('survey_id'):
                validated_data['survey_id'] = linked_object.pk
            if isinstance(linked_object, Petition) and not validated_data.get('petition_id'):
                validated_data['petition_id'] = linked_object.pk
            if isinstance(linked_object, Broadcast) and not validated_data.get('broadcast_id'):
                validated_data['broadcast_id'] = linked_object.pk
            if isinstance(linked_object, Section) and not validated_data.get('section_id'):
                validated_data['section_id'] = linked_object.pk

        # Calling create method with new validated data
        assets = validated_data.pop('assets')
        message = super().create(validated_data)
        for asset in assets:
            # Create a unique key for S3 to avoid collisions
            file_extension = asset['name'].split('.')[-1]
            unique_key = f"uploads/{message.author.id}/messages/{uuid.uuid4()}.{file_extension}"
            Asset.objects.create(message=message, file_key=unique_key, **asset)
        post_save.send(sender=Chat, instance=message.chat, created=False)
        return message

    def update(self, instance, validated_data):
        instance.is_edited = True
        return super().update(instance, validated_data)


class ChatSerializer(serializers.ModelSerializer):
    users = UserSerializer(many=True, read_only=True)
    user = serializers.IntegerField(write_only=True)
    last_message = serializers.SerializerMethodField(read_only=True)
    unread_messages = serializers.SerializerMethodField(read_only=True)
    is_self_chat = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Chat
        fields = ['id', 'users', 'last_message', 'unread_messages', 'user', 'is_self_chat']
        read_only_fields = ['last_message']

    def get_last_message(self, obj: Chat):
        if obj.messages.exists():
            serializer = MessageSerializer(obj.messages.order_by('created_at').last(), context=self.context)
            return serializer.data
        else:
            return None

    def get_unread_messages(self, instance: Chat):
        user = get_current_user(self.context)
        return instance.messages.filter(is_read=False).exclude(author=user).count()

    @staticmethod
    def get_is_self_chat(obj: Chat):
        return obj.users.count() == 1

    def create(self, validated_data):
        current_user = get_current_user(self.context)
        user = User.objects.get(id=validated_data.pop('user'))
        validated_data['users'] = [current_user, user]
        chat = get_or_create_direct_chat(current_user, user)
        return chat


def get_or_create_direct_chat(user1, user2):
    """
    Returns (or creates) a Chat for 1:1 or self-chat.
    For self-chat: chat contains only 1 user.
    For normal DM: chat contains exactly 2 users.
    """
    # Efficient query: find chat containing both users with correct count
    num_users = 1 if user1.id == user2.id else 2

    chat = Chat.objects.annotate(
        num_users=Count('users', distinct=True)
    ).filter(
        users=user1
    ).filter(
        users=user2
    ).filter(
        num_users=num_users
    ).first()

    if not chat:
        chat = Chat.objects.create()
        if user1.id == user2.id:
            chat.users.add(user1)  # Self-chat: only one user
        else:
            chat.users.add(user1, user2)  # Normal 1:1 chat

    return chat
