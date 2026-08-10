import uuid

import botocore
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models.signals import post_save
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Asset, Chat, Message
from apps.chat.serializers import (
    AssetSerializer,
    ChatSerializer,
    MessageSerializer,
    build_asset_upload_data,
    can_user_access_chat,
    get_or_create_direct_chat,
    is_blocked_pair,
)
from apps.utils.presigned_url import s3_client

User = get_user_model()


class ChatAccessPermission(permissions.BasePermission):
    """
    Permission for chat/message endpoints.

    For message creation, validates that the request user belongs to the chat
    and is not blocked.

    For object-level access, validates chat membership.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.method == "POST" and isinstance(request.data, dict):
            chat_id = request.data.get("chat")

            if chat_id:
                try:
                    chat = Chat.objects.get(pk=chat_id)
                except (Chat.DoesNotExist, DjangoValidationError, ValueError):
                    raise ValidationError({"chat": "Invalid chat."})

                if not can_user_access_chat(request.user, chat):
                    raise PermissionDenied("You cannot access this chat.")

        return True

    def has_object_permission(self, request, view, obj):
        chat = obj.chat if isinstance(obj, Message) else obj
        return can_user_access_chat(request.user, chat)


class MessageAccessPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj: Message):
        if not can_user_access_chat(request.user, obj.chat):
            return False

        if request.method in permissions.SAFE_METHODS:
            return True

        return obj.author_id == request.user.id


# Backward-compatible alias.
NotBlockedPermission = ChatAccessPermission


class MessageCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated, ChatAccessPermission]
    serializer_class = MessageSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["scope"] = {"user": self.request.user}
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = serializer.save()

        upload_data = build_asset_upload_data(message.assets.all())

        return Response(
            {
                "message": serializer.data,
                "uploads": upload_data,
            },
            status=status.HTTP_201_CREATED,
        )


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def generate_upload_urls(request):
    message_id = request.data.get("message_id") if isinstance(request.data, dict) else None

    if not message_id:
        return Response(
            {"message_id": "This field is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        message = Message.objects.select_related("chat").get(
            pk=message_id,
            author=request.user,
        )
    except (Message.DoesNotExist, DjangoValidationError, ValueError):
        return Response(
            {"error": "Message does not exist."},
            status=status.HTTP_404_NOT_FOUND,
        )

    assets = message.assets.filter(is_completed=False)
    upload_data = build_asset_upload_data(assets)

    return Response(upload_data, status=status.HTTP_200_OK)


class AssetUploadCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        raw_asset_ids = request.data.get("asset_id_list", []) if isinstance(request.data, dict) else []

        if not raw_asset_ids or not isinstance(raw_asset_ids, list):
            return Response(
                {"asset_id_list": "This field is required and must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Preserve order while removing duplicates.
        asset_id_list = []
        for asset_id in raw_asset_ids:
            if asset_id not in asset_id_list:
                asset_id_list.append(asset_id)

        try:
            assets = list(
                Asset.objects.filter(
                    id__in=asset_id_list,
                    message__author=request.user,
                ).select_related("message", "message__chat")
            )
        except (DjangoValidationError, ValueError):
            return Response(
                {"error": "Invalid asset id list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        found_asset_ids = {str(asset.id) for asset in assets}
        missing_asset_ids = [
            str(asset_id)
            for asset_id in asset_id_list
            if str(asset_id) not in found_asset_ids
        ]

        if missing_asset_ids:
            return Response(
                {
                    "error": "Asset not found.",
                    "assets": missing_asset_ids,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Verify all uploads exist in S3 before marking anything completed.
        for asset in assets:
            try:
                s3_client.head_object(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Key=asset.file_key,
                )
            except botocore.exceptions.ClientError:
                return Response(
                    {
                        "error": "File not found in S3.",
                        "asset_id": str(asset.id),
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

        with transaction.atomic():
            for asset in assets:
                asset.is_completed = True
                asset.save(update_fields=["is_completed", "updated_at"])

            messages = {asset.message for asset in assets}
            chats = {message.chat for message in messages}

            for message in messages:
                transaction.on_commit(
                    lambda m=message: post_save.send(sender=Message, instance=m, created=False)
                )

            for chat in chats:
                transaction.on_commit(
                    lambda c=chat: post_save.send(sender=Chat, instance=c, created=False)
                )

        context = {"scope": {"user": request.user}}
        serializer = AssetSerializer(assets, many=True, context=context)

        return Response(serializer.data, status=status.HTTP_200_OK)


class MessageDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, MessageAccessPermission]
    serializer_class = MessageSerializer
    queryset = (
        Message.objects.select_related("chat", "author")
        .prefetch_related("assets")
    )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["scope"] = {"user": self.request.user}
        return context

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)

        transaction.on_commit(
            lambda: post_save.send(sender=Chat, instance=instance.chat, created=False)
        )

        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def direct_message(request):
    data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)

    raw_user_ids = data.pop("user_ids", [])

    if not raw_user_ids or not isinstance(raw_user_ids, list):
        return Response(
            {"user_ids": "This field is required and must be a list."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove duplicates while preserving order.
    user_ids = []
    for user_id in raw_user_ids:
        if user_id not in user_ids:
            user_ids.append(user_id)

    if len(user_ids) > 5:
        return Response(
            {"user_ids": "Only 5 users allowed at a time."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        target_users = list(User.objects.filter(pk__in=user_ids))
    except (DjangoValidationError, ValueError):
        return Response(
            {"user_ids": "Invalid user id list."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    found_user_ids = {str(user.pk) for user in target_users}
    missing_user_ids = [
        str(user_id)
        for user_id in user_ids
        if str(user_id) not in found_user_ids
    ]

    if missing_user_ids:
        return Response(
            {
                "error": "Some users do not exist.",
                "user_ids": missing_user_ids,
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    user = request.user

    for target_user in target_users:
        if target_user.pk != user.pk and is_blocked_pair(user, target_user):
            return Response(
                {"error": "You cannot message this user."},
                status=status.HTTP_403_FORBIDDEN,
            )

    context = {"scope": {"user": user}}

    created_chats = []
    upload_data = []

    with transaction.atomic():
        for target_user in target_users:
            chat = get_or_create_direct_chat(user, target_user)

            message_data = data.copy()
            message_data["chat"] = chat.id
            message_data["uuid"] = uuid.uuid4()

            serializer = MessageSerializer(data=message_data, context=context)
            serializer.is_valid(raise_exception=True)

            message = serializer.save()

            created_chats.append(chat)
            upload_data.extend(build_asset_upload_data(message.assets.all()))

        chat_serializer = ChatSerializer(created_chats, many=True, context=context)

        return Response(
            {
                "chats": chat_serializer.data,
                "uploads": upload_data,
            },
            status=status.HTTP_201_CREATED,
        )