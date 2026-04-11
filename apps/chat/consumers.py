from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import Max
from django.db.models.signals import post_save
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import action

from apps.chat.models import Message, Chat
from apps.chat.serializers import MessageSerializer, ChatSerializer
from apps.chat.views import get_or_create_direct_chat
from apps.notification.tasks import delete_notification_on_marked_as_read

User = get_user_model()


class ChatConsumer(CreateModelMixin, RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = ChatSerializer
    queryset = Chat.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ==================== Chat Observer ====================
    @model_observer(Chat)
    async def chat_activity(self, message, **kwargs):
        if message['action'] != 'delete':
            message['data'] = await self.get_chat_serializer_data(pk=message['data'])
        await self.send_json(message)

    @database_sync_to_async
    def get_chat_serializer_data(self, pk: int):
        chat = Chat.objects.get(pk=pk)
        serializer = ChatSerializer(instance=chat, context={'scope': self.scope})
        return serializer.data

    @chat_activity.groups_for_signal
    def chat_activity_groups(self, instance: Chat, **kwargs):
        yield f'chat__{instance.pk}'

    @chat_activity.groups_for_consumer
    def chat_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'chat__{pk}'

    @chat_activity.serializer
    def chat_activity_serializer(self, instance: Chat, action, **kwargs):
        return {
            'data': instance.pk,
            'action': action.value,
            'request_id': 'chats',
            'pk': instance.pk,
            'response_status': 201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        }

    # ==================== Message Observer ====================
    @model_observer(Message)
    async def message_activity(self, message, **kwargs):
        if message['action'] != 'delete':
            message['data'] = await self.get_message_serializer_data(pk=message['data']['pk'])
        await self.send_json(message)

    @database_sync_to_async
    def get_message_serializer_data(self, pk: int):
        message = Message.objects.select_related('chat', 'author').get(pk=pk)
        serializer = MessageSerializer(instance=message, context={'scope': self.scope})
        return serializer.data

    @message_activity.groups_for_signal
    def message_activity_groups(self, instance: Message, **kwargs):
        yield f'chat__{instance.chat.id}'

    @message_activity.groups_for_consumer
    def message_activity_groups(self, chat=None, **kwargs):
        if chat is not None:
            yield f'chat__{chat}'

    @message_activity.serializer
    def message_activity_serializer(self, instance: Message, action, **kwargs):
        return {
            'data': {'pk': instance.pk, 'chat_id': instance.chat.id},
            'action': action.value,
            'request_id': 'messages',
            'pk': instance.pk,
            'response_status': 201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        }

    async def disconnect(self, code):
        await self.chat_activity.unsubscribe()
        await self.message_activity.unsubscribe()
        await super().disconnect(code)

    # ==================== Subscription Helpers ====================
    async def subscribe(self, pk: int, request_id: str):
        await self.chat_activity.subscribe(pk=pk, request_id=request_id)
        await self.message_activity.subscribe(chat=pk, request_id=request_id)

    # ==================== Custom Chat Creation (with self-chat support) ====================
    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        """Create a new chat + first message (supports self-chat)"""
        chat = await self.get_or_create_chat(data)
        if not chat:
            return {"error": "Failed to create chat"}, 400

        # Add chat to message data and create message
        message_data = data.copy()
        message_data['chat'] = chat.id

        # Use CreateModelMixin's create but override to use our chat
        response, status = await super().create(message_data, **kwargs)

        pk = response.get("id") if isinstance(response, dict) else None
        if pk:
            await self.subscribe(pk=pk, request_id=request_id)

        return response, status

    @database_sync_to_async
    def get_or_create_chat(self, data: dict):
        """Get or create direct/self chat"""
        user_id = data.get('user') or data.get('user_ids', [None])[0]
        if not user_id:
            return None

        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

        return get_or_create_direct_chat(self.scope['user'], target_user)

    # ==================== List & Messages ====================
    @action()
    async def list(self, request_id: str, last_chat: int = None, page_size=None, **kwargs):
        data = await self.list_chats(page_size=page_size or self.page_size, last_chat=last_chat, **kwargs)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def list_chats(self, page_size: int, last_chat: int = None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(), **kwargs)

        if last_chat:
            try:
                last_chat_obj = Chat.objects.get(pk=last_chat)
                last_msg_id = last_chat_obj.messages.aggregate(Max('id'))['id__max']
                if last_msg_id:
                    queryset = queryset.filter(latest_message_id__lt=last_msg_id)
            except Chat.DoesNotExist:
                pass

        from apps.utils.list_paginator import list_paginator
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = ChatSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return {
            'results': serializer.data,
            'last_chat': last_chat,
            'has_next': page_obj.has_next()
        }

    @action()
    def messages(self, chat_id: int = None, oldest_message: int = None,
                 newest_message: int = None, page_size=20, **kwargs):
        chat = self.get_object(pk=chat_id)
        queryset = chat.messages.all()

        if oldest_message:
            queryset = queryset.filter(id__lt=oldest_message)
        elif newest_message:
            queryset = queryset.filter(id__gt=newest_message)

        from apps.utils.list_paginator import list_paginator
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = MessageSerializer(page_obj.object_list, many=True, context={'scope': self.scope})

        return {
            'results': serializer.data,
            'oldest_message': oldest_message,
            'newest_message': newest_message,
            'has_next': page_obj.has_next(),
            'chat_id': chat_id
        }, 200

    # ==================== Other Actions ====================
    @action()
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        pk = response.get("id")
        if pk:
            await self.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def delete_message(self, request_id: str, pk: int, **kwargs):
        message = await self.get_message(pk=pk)
        if not message:
            return self.reply(errors=['Not found'], action='delete', request_id=request_id, status=404)

        await self.delete_message_(message)
        return {}, 204

    @database_sync_to_async
    def delete_message_(self, message: Message):
        if self.scope['user'] == message.author:
            if message.is_read:
                message.text = ''
                message.post = message.ballot = message.survey = message.petition = None
                message.is_deleted = True
                message.save()
            else:
                message.delete()
        self.signal_chat(message.chat)

    @action()
    async def edit_message(self, request_id: str, pk: int, **kwargs):
        message = await self.get_message(pk=pk)
        if not message:
            return self.reply(errors=['Not found'], action='update', request_id=request_id, status=404)

        text = kwargs.get('data', {}).get('text')
        if text is not None:
            await self.edit_message_(message, text)
        return {}, 200

    @database_sync_to_async
    def edit_message_(self, message: Message, text: str):
        message.text = text
        message.is_edited = True
        message.save()
        self.signal_chat(message.chat)

    @database_sync_to_async
    def get_message(self, pk: int):
        return Message.objects.filter(
            pk=pk,
            author=self.scope["user"],
            is_deleted=False
        ).first()

    @action()
    async def mark_as_read(self, pk: int, **kwargs):
        await self.mark_as_read_(pk)
        return {}, 200

    @database_sync_to_async
    def mark_as_read_(self, pk: int):
        chat = Chat.objects.get(pk=pk)
        chat.messages.filter(is_read=False).exclude(author=self.scope["user"]).update(is_read=True)
        delete_notification_on_marked_as_read.delay_on_commit(pk, self.scope["user"].id)
        self.signal_chat(chat)

    @staticmethod
    def signal_chat(chat: Chat):
        post_save.send(sender=Chat, instance=chat, created=False)

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.chat_activity.unsubscribe(pk=pk, request_id=request_id)
        await self.message_activity.unsubscribe(chat=pk, request_id=request_id)
        return {"pk": pk}, 200

    # ==================== Filter ====================
    def filter_queryset(self, queryset, **kwargs):
        user = self.scope['user']
        search_term = kwargs.get('search_term')

        queryset = queryset.for_user(user).with_latest_message()

        if search_term:
            queryset = queryset.search_by_other_user(user, search_term)

        return queryset.order_by('-latest_message_id')
