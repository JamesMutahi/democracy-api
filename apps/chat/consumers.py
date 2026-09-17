from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.db.models.signals import post_save
from django.utils import timezone
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.chat.models import Chat, Message, ChatParticipant
from apps.chat.querysets import annotate_chat_metrics, search_chats_by_user
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
    lookup_field = "pk"
    page_size = 20

    def get_queryset(self, **kwargs) -> QuerySet:
        return annotate_chat_metrics(
            Chat.objects.filter(users=self.scope["user"]),
            user=self.scope["user"]
        )

    async def connect(self):
        if self.scope["user"].is_authenticated:
            await self.accept()
            await self.chat_activity.subscribe()
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
        for user_id in instance.users.values_list('id', flat=True):
            yield f"user_chats__{user_id}"

    @chat_activity.groups_for_consumer
    def chat_activity_consumer_groups(self, consumer, pk=None, **kwargs):
        user = consumer.scope.get("user")
        if user and user.is_authenticated:
            yield f"user_chats__{user.id}"

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
    def message_activity_serializer(self, instance: Message, _action, **kwargs):
        return {
            "data": {
                "pk": instance.pk,
                "chat_id": instance.chat_id,
            },
            "action": f"message_{_action.value}",
            "pk": instance.pk,
            "response_status": 201 if _action.value == "create" else 204 if _action.value == "delete" else 200,
        }

    async def disconnect(self, code):
        await self.chat_activity.unsubscribe()
        await self.message_activity.unsubscribe()
        await super().disconnect(code)

    # ==================== Filtering ====================

    def filter_queryset(self, queryset, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        user = self.scope["user"]
        search_term = kwargs.get("search_term")
        _action = kwargs.get("action")

        if _action == "inbox":
            # Inbox = chats where the current user is NOT the one with a
            # pending request (i.e. they have accepted, OR they initiated).
            # Exclude chats where this user's participant row is PENDING
            # *and* they didn't send the latest message themselves.
            pending_chats = ChatParticipant.objects.filter(
                user=user,
                status=ChatParticipant.Status.PENDING,
            ).values_list("chat_id", flat=True)
            queryset = queryset.exclude(
                pk__in=pending_chats,
            ).distinct()

        if _action == "requests":
            # Requests = chats where the current user's participant row is PENDING
            pending_chats = ChatParticipant.objects.filter(
                user=user,
                status=ChatParticipant.Status.PENDING,
            ).values_list("chat_id", flat=True)
            queryset = queryset.filter(pk__in=pending_chats).distinct()

        if search_term:
            queryset = search_chats_by_user(queryset, user, search_term)

        return queryset

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

        if not chat.users.filter(pk=user.pk).exists():
            return None

        return chat

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

    # ==================== Retrieve ====================

    @action()
    @rate_limit(limit=40, period=60)
    async def retrieve(self, request_id: str, pk: int = None, **kwargs):
        if not pk:
            raise ValidationError("pk is required.")

        chat = await self.get_accessible_chat(pk)

        if not chat:
            raise NotFound("Chat not found")

        data = await self.get_chat_serializer_data(pk=pk)

        await self.message_activity.subscribe(chat=pk, request_id=request_id)

        return data, 200

    # ==================== Chat + Message Creation ====================

    @database_sync_to_async
    def get_or_create_chat_for(self, target_user_id):
        user = self.scope["user"]
        try:
            target_user = User.objects.get(pk=target_user_id)
        except (User.DoesNotExist, ValueError, TypeError):
            return None, None

        if target_user.pk != user.pk and is_blocked_pair(user, target_user):
            return None, None

        chat, will_create_request = get_or_create_direct_chat(user, target_user)
        return chat, will_create_request

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
        target_user_id = data.get("user")

        if not target_user_id:
            user_ids = data.get("user_ids")
            if isinstance(user_ids, list) and user_ids:
                target_user_id = user_ids[0]

        if not target_user_id:
            raise ValidationError("user is required.")

        chat, will_create_request = await self.get_or_create_chat_for(target_user_id)
        if not chat:
            raise ValidationError("Failed to create chat.")

        data = await self.get_chat_serializer_data(chat.pk)

        # Include request metadata in response
        data["will_create_request"] = will_create_request

        return data, 201

    # ==================== Chat List ====================

    @action()
    @rate_limit(limit=40, period=60)
    async def inbox(self, request_id: str, last_chat: int = None, page_size=None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(), **kwargs)
        data = await self.list_chats(
            queryset=queryset,
            page_size=page_size or self.page_size,
            last_chat=last_chat,
            **kwargs,
        )
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def requests(self, request_id: str, last_chat: int = None, page_size=None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(), **kwargs)
        data = await self.list_chats(
            queryset=queryset,
            page_size=page_size or self.page_size,
            last_chat=last_chat,
            **kwargs,
        )
        return data, 200

    @database_sync_to_async
    def list_chats(self, queryset: QuerySet, page_size: int, last_chat: int = None, **kwargs):

        if last_chat:
            try:
                cursor_chat = Chat.objects.get(pk=last_chat)
                latest_msg = cursor_chat.messages.order_by("-created_at", "-id").first()
                if latest_msg:
                    queryset = queryset.filter(latest_message_id__lt=latest_msg.id)
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
            "has_next": page_obj.has_next(),
        }

    # ==================== Message Requests ====================

    @action()
    @interaction_rate_limit
    async def accept_request(self, request_id: str, chat_id: int = None, **kwargs):
        if not chat_id:
            raise ValidationError("chat_id is required.")

        await self._accept_request(chat_id)
        return chat_id, 200

    @database_sync_to_async
    def _accept_request(self, chat_id: int):
        try:
            participant_entry = ChatParticipant.objects.select_related('chat').get(
                chat_id=chat_id,
                user=self.scope["user"],
            )
        except Chat.DoesNotExist:
            raise NotFound("Chat not found")

        # Perform the soft-delete update
        participant_entry.status = ChatParticipant.Status.ACCEPTED
        participant_entry.hidden_at = None
        participant_entry.save()

        post_save.send(sender=Chat, instance=participant_entry.chat, created=False)

        return chat_id

    @action()
    @interaction_rate_limit
    async def decline_request(self, request_id: str, chat_id: int = None, **kwargs):
        if not chat_id:
            raise ValidationError("chat_id is required.")

        await self._decline_request(chat_id)
        return chat_id, 200

    @database_sync_to_async
    def _decline_request(self, chat_id: int):
        try:
            participant_entry = ChatParticipant.objects.select_related('chat').get(
                chat_id=chat_id,
                user=self.scope["user"],
            )
        except Chat.DoesNotExist:
            raise NotFound("Chat not found")

        # Perform the soft-delete update
        participant_entry.status = ChatParticipant.Status.DECLINED
        participant_entry.hidden_at = timezone.now()
        participant_entry.save()

        post_save.send(sender=Chat, instance=participant_entry.chat, created=False)

        return chat_id

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
            raise ValidationError("chat_id is required.")

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
            raise NotFound("Chat not found")

        if not chat.users.filter(pk=user.pk).exists():
            raise PermissionDenied("You cannot access this chat.")

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

    # ==================== Read State ====================

    @action()
    @interaction_rate_limit
    async def mark_as_read(self, pk: int, **kwargs):
        await self.mark_as_read_(pk)
        return {}, 200

    @database_sync_to_async
    def mark_as_read_(self, pk: int):
        user = self.scope["user"]
        try:
            chat = Chat.objects.get(pk=pk)
        except Chat.DoesNotExist:
            raise NotFound("Chat not found")
        if not can_user_access_chat(user, chat):
            raise PermissionDenied("You cannot access this chat.")

        # Block read marking while the user still has a PENDING request
        if ChatParticipant.objects.filter(
                chat=chat,
                user=user,
                status=ChatParticipant.Status.PENDING,
        ).exists():
            raise ValidationError("Pending request.")

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
        await self.message_activity.unsubscribe(chat=pk, request_id=request_id)
        return {"pk": pk}, 200
