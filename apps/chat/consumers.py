import uuid

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import F
from django.db.models.signals import post_save
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import action
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.chat.models import Chat, Message
from apps.chat.serializers import (
    ChatSerializer,
    MessageSerializer,
    build_asset_upload_data,
    can_user_access_chat,
    get_or_create_direct_chat,
    is_blocked_pair,
)
from apps.notification.tasks import delete_notification_on_marked_as_read
from apps.utils.throttles import interaction_rate_limit, rate_limit

User = get_user_model()


class ChatConsumer(GenericAsyncAPIConsumer):
    serializer_class = ChatSerializer
    queryset = Chat.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope["user"].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ==================== Chat Observer ====================

    @model_observer(Chat)
    async def chat_activity(self, message, **kwargs):
        if message.get("action") != "delete":
            message["data"] = await self.get_chat_serializer_data(pk=message["data"])

        await self.send_json(message)

    @chat_activity.groups_for_signal
    def chat_activity_signal_groups(self, instance: Chat, **kwargs):
        yield f"chat__{instance.pk}"

    @chat_activity.groups_for_consumer
    def chat_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"chat__{pk}"

    @chat_activity.serializer
    def chat_activity_serializer(self, instance: Chat, action, **kwargs):
        return {
            "data": instance.pk,
            "action": action.value,
            "pk": instance.pk,
            "response_status": 201 if action.value == "create" else 204 if action.value == "delete" else 200,
        }

    # ==================== Message Observer ====================

    @model_observer(Message)
    async def message_activity(self, message, **kwargs):
        action_name = str(message.get("action", ""))

        if not action_name.endswith("delete"):
            message["data"] = await self.get_message_serializer_data(
                pk=message["data"]["pk"]
            )

        await self.send_json(message)

    @message_activity.groups_for_signal
    def message_activity_signal_groups(self, instance: Message, **kwargs):
        yield f"chat__{instance.chat_id}"

    @message_activity.groups_for_consumer
    def message_activity_consumer_groups(self, chat=None, **kwargs):
        if chat is not None:
            yield f"chat__{chat}"

    @message_activity.serializer
    def message_activity_serializer(self, instance: Message, action, **kwargs):
        return {
            "data": {
                "pk": instance.pk,
                "chat_id": instance.chat_id,
            },
            "action": f"message_{action.value}",
            "pk": instance.pk,
            "response_status": 201 if action.value == "create" else 204 if action.value == "delete" else 200,
        }

    async def disconnect(self, code):
        await self.chat_activity.unsubscribe()
        await self.message_activity.unsubscribe()
        await super().disconnect(code)

    # ==================== Subscription Helpers ====================

    @database_sync_to_async
    def get_accessible_chat(self, pk):
        user = self.scope.get("user")

        if not user or not user.is_authenticated:
            return None

        try:
            chat = Chat.objects.prefetch_related("users").get(pk=pk)
        except Chat.DoesNotExist:
            return None

        if not can_user_access_chat(user, chat):
            return None

        return chat

    async def subscribe_to_chat(self, pk, request_id):
        chat = await self.get_accessible_chat(pk)

        if not chat:
            return False

        await self.chat_activity.subscribe(pk=pk, request_id=request_id)
        await self.message_activity.subscribe(chat=pk, request_id=request_id)

        return True

    async def unsubscribe_from_chat(self, pk, request_id):
        await self.chat_activity.unsubscribe(pk=pk, request_id=request_id)
        await self.message_activity.unsubscribe(chat=pk, request_id=request_id)

    # ==================== Serializer Helpers ====================

    @database_sync_to_async
    def get_chat_serializer_data(self, pk: int):
        queryset = self.filter_queryset(self.get_queryset()).filter(pk=pk)
        chat = queryset.first()

        if not chat:
            return {"id": pk}

        serializer = ChatSerializer(instance=chat, context={"scope": self.scope})
        return serializer.data

    @database_sync_to_async
    def get_message_serializer_data(self, pk: int):
        try:
            message = (
                Message.objects.select_related("chat", "author")
                .prefetch_related("assets")
                .get(pk=pk)
            )
        except Message.DoesNotExist:
            return {"id": pk}

        serializer = MessageSerializer(instance=message, context={"scope": self.scope})
        return serializer.data

    # ==================== Chat + Message Creation ====================

    @database_sync_to_async
    def get_or_create_chat_for(self, target_user_id):
        user = self.scope["user"]

        try:
            target_user = User.objects.get(pk=target_user_id)
        except (User.DoesNotExist, ValueError, TypeError):
            return None

        if target_user.pk != user.pk and is_blocked_pair(user, target_user):
            return None

        return get_or_create_direct_chat(user, target_user)

    @database_sync_to_async
    def create_message(self, data):
        context = {"scope": self.scope}

        serializer = MessageSerializer(data=data, context=context)
        serializer.is_valid(raise_exception=True)

        message = serializer.save()

        return {
            "chat": ChatSerializer(message.chat, context=context).data,
            "message": MessageSerializer(message, context=context).data,
            "uploads": build_asset_upload_data(message.assets.all()),
        }

    @action()
    @interaction_rate_limit
    async def create(self, data: dict, request_id: str, **kwargs):
        """
        Creates a direct/self chat if needed, then creates the first message.
        """
        target_user_id = data.get("user")

        if not target_user_id:
            user_ids = data.get("user_ids")
            if isinstance(user_ids, list) and user_ids:
                target_user_id = user_ids[0]

        if not target_user_id:
            return {"error": "user is required."}, 400

        chat = await self.get_or_create_chat_for(target_user_id)

        if not chat:
            return {"error": "Failed to create chat."}, 400

        message_data = data.copy()
        message_data.pop("user", None)
        message_data.pop("user_ids", None)

        message_data["chat"] = chat.id
        message_data.setdefault("uuid", str(uuid.uuid4()))

        try:
            response = await self.create_message(message_data)
        except ValidationError as exc:
            return {"errors": exc.detail}, 400
        except PermissionDenied as exc:
            return {"error": str(exc.detail)}, 403

        await self.subscribe_to_chat(chat.id, request_id)

        return response, 201

    # ==================== Chat List ====================

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id: str, last_chat: int = None, page_size=None, **kwargs):
        data = await self.list_chats(
            page_size=page_size or self.page_size,
            last_chat=last_chat,
            **kwargs,
        )

        await self.reply(action="list", data=data, request_id=request_id)

    @database_sync_to_async
    def list_chats(self, page_size: int, last_chat: int = None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(), **kwargs)

        if last_chat:
            try:
                cursor_chat = Chat.objects.with_latest_message().get(pk=last_chat)
                if cursor_chat.latest_message_id:
                    queryset = queryset.filter(
                        latest_message_id__lt=cursor_chat.latest_message_id
                    )
            except Chat.DoesNotExist:
                pass

        from apps.utils.list_paginator import list_paginator

        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = ChatSerializer(
            page_obj.object_list,
            many=True,
            context={"scope": self.scope},
        )

        results = serializer.data

        return {
            "results": results,
            "last_chat": last_chat,
            "next_last_chat": results[-1]["id"] if results else None,
            "has_next": page_obj.has_next(),
        }

    # ==================== Messages ====================

    @action()
    @rate_limit(limit=40, period=60)
    async def messages(
        self,
        request_id: str,
        chat_id: int = None,
        oldest_message: int = None,
        newest_message: int = None,
        page_size=20,
        **kwargs,
    ):
        if not chat_id:
            return {"error": "chat_id is required."}, 400

        response, response_status = await self.get_messages(
            chat_id=chat_id,
            oldest_message=oldest_message,
            newest_message=newest_message,
            page_size=page_size,
        )

        return response, response_status

    @database_sync_to_async
    def get_messages(
        self,
        chat_id: int,
        oldest_message: int = None,
        newest_message: int = None,
        page_size: int = 20,
    ):
        user = self.scope["user"]

        try:
            chat = Chat.objects.get(pk=chat_id)
        except Chat.DoesNotExist:
            return {"error": "Chat not found."}, 404

        if not can_user_access_chat(user, chat):
            return {"error": "You cannot access this chat."}, 403

        queryset = (
            chat.messages.filter(is_deleted=False)
            .select_related("author", "chat")
            .prefetch_related("assets")
            .order_by("-created_at", "-id")
        )

        if oldest_message:
            queryset = queryset.filter(id__lt=oldest_message)
        elif newest_message:
            queryset = queryset.filter(id__gt=newest_message)

        from apps.utils.list_paginator import list_paginator

        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = MessageSerializer(
            page_obj.object_list,
            many=True,
            context={"scope": self.scope},
        )

        return {
            "results": serializer.data,
            "chat_id": chat_id,
            "oldest_message": oldest_message,
            "newest_message": newest_message,
            "has_next": page_obj.has_next(),
        }, 200

    # ==================== Retrieve ====================

    @action()
    @rate_limit(limit=40, period=60)
    async def retrieve(self, request_id: str, pk: int = None, **kwargs):
        if not pk:
            return {"error": "pk is required."}, 400

        chat = await self.get_accessible_chat(pk)

        if not chat:
            return {"error": "Chat not found."}, 404

        data = await self.get_chat_serializer_data(pk=pk)

        await self.subscribe_to_chat(pk, request_id)

        return data, 200

    # ==================== Read State ====================

    @action()
    @interaction_rate_limit
    async def mark_as_read(self, pk: int, **kwargs):
        result = await self.mark_as_read_(pk)

        if result is None:
            return {"error": "Chat not found."}, 404

        if result is False:
            return {"error": "You cannot access this chat."}, 403

        return {}, 200

    @database_sync_to_async
    def mark_as_read_(self, pk: int):
        user = self.scope["user"]

        try:
            chat = Chat.objects.get(pk=pk)
        except Chat.DoesNotExist:
            return None

        if not can_user_access_chat(user, chat):
            return False

        updated = (
            chat.messages.filter(is_read=False, is_deleted=False)
            .exclude(author=user)
            .update(is_read=True)
        )

        if updated:
            delete_notification_on_marked_as_read.delay_on_commit(pk, user.id)
            post_save.send(sender=Chat, instance=chat, created=False)

        return True

    # ==================== Unsubscribe ====================

    @action()
    @interaction_rate_limit
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.unsubscribe_from_chat(pk, request_id)
        return {"pk": pk}, 200

    # ==================== Filtering ====================

    def filter_queryset(self, queryset, **kwargs):
        user = self.scope["user"]
        search_term = kwargs.get("search_term")

        queryset = (
            queryset.for_user(user)
            .with_latest_message()
            .with_user_count()
            .with_unread_count_for_user(user)
            .prefetch_related("users")
        )

        if search_term:
            queryset = queryset.search_by_other_user(user, search_term)

        return queryset.order_by(F("latest_message_id").desc(nulls_last=True))