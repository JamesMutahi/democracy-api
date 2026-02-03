from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Count, Max
from django.db.models.signals import post_save
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import action

from notification.models import Notification
from .models import Message, Chat
from .serializers import MessageSerializer, ChatSerializer
from .utils.list_paginator import list_paginator

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

    @model_observer(Chat)
    async def chat_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_chat_serializer_data(chat=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_chat_serializer_data(self, chat: Chat):
        serializer = ChatSerializer(instance=chat, context={'scope': self.scope})
        return serializer.data

    @chat_activity.groups_for_signal
    def chat_activity(self, instance: Chat, **kwargs):
        yield f'chat__{instance.pk}'

    @chat_activity.groups_for_consumer
    def chat_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'chat__{pk}'

    @chat_activity.serializer
    def chat_activity(self, instance: Chat, action, **kwargs):
        return dict(
            # data is overridden in model_observer to pass scope required for UserSerializer
            data=instance,
            action=action.value,
            request_id='chats',
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Message)
    async def message_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_message_serializer_data(message=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_message_serializer_data(self, message: Message):
        serializer = MessageSerializer(instance=message, context={'scope': self.scope})
        return serializer.data

    @message_activity.groups_for_signal
    def message_activity(self, instance: Message, **kwargs):
        yield f'chat__{instance.chat.id}'

    @message_activity.groups_for_consumer
    def message_activity(self, chat=None, **kwargs):
        if chat is not None:
            yield f'chat__{chat}'

    @message_activity.serializer
    def message_activity(self, instance: Message, action, **kwargs):
        return dict(
            # data is overridden in model_observer to pass scope required for UserSerializer
            data=instance,
            action=action.value,
            request_id='messages',
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    async def disconnect(self, code):
        await self.unsubscribe()
        await super().disconnect(code)

    async def subscribe(self, pk, request_id, **kwargs):
        await self.chat_activity.subscribe(pk=pk, request_id=request_id)
        await self.message_activity.subscribe(chat=pk, request_id=request_id)

    async def unsubscribe(self):
        await self.chat_activity.unsubscribe()
        await self.message_activity.unsubscribe()

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        search_term = kwargs.get('search_term', None)
        if search_term:
            search_term = search_term.lower()
            queryset = queryset.annotate(latest_message_id=Max('messages__id')).filter(
                users=self.scope['user']).order_by(
                '-latest_message_id').prefetch_related('users')
            users_in_chats = []
            for chat in queryset:
                for user in chat.users.all():
                    if user != self.scope['user']:
                        if search_term in user.username.lower() or search_term in user.name.lower():
                            users_in_chats.append(user)
            return queryset.filter(users__in=users_in_chats)
        return queryset.annotate(latest_message_id=Max('messages__id')).filter(users=self.scope['user']).order_by(
            '-latest_message_id')

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        chat_data = await self.get_chat_data(data)
        if chat_data:
            return chat_data, 201
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.subscribe(pk=pk, request_id=request_id)
        return response, status

    @database_sync_to_async
    def get_chat_data(self, data: dict):
        chat_data = None
        user = User.objects.get(id=data['user'])
        if self.scope['user'].id == user.id:
            chat_qs = Chat.objects.annotate(num_users=Count('users')).filter(users=user, num_users=1)
            if chat_qs.exists():
                serializer = ChatSerializer(instance=chat_qs.first(), context={'scope': self.scope})
                chat_data = serializer.data
        else:
            chats = self.scope['user'].chats.prefetch_related('users')
            for chat in chats:
                if chat.users.contains(user):
                    serializer = ChatSerializer(instance=chat, context={'scope': self.scope})
                    chat_data = serializer.data
        return chat_data

    @action()
    async def join_chat(self, pk: int, request_id: str, **kwargs):
        await database_sync_to_async(self.get_object)(pk=pk)
        await self.subscribe(pk=pk, request_id=request_id)

    @action()
    async def list(self, request_id, last_chat: int = None, page_size=page_size, **kwargs):
        if not last_chat:
            await self.unsubscribe()
        data = await self.list_(page_size=page_size, last_chat=last_chat, **kwargs)
        for chat in data['results']:
            pk = chat["id"]
            await self.subscribe(pk=pk, request_id=request_id)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def list_(self, page_size, last_chat, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        if last_chat:
            chat = self.get_object(pk=last_chat)
            last_message = chat.messages.order_by('created_at').last()
            queryset = queryset.filter(latest_message_id__lt=last_message.id)
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)
        serializer = ChatSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, last_chat=last_chat, has_next=page_obj.has_next())

    @action()
    def messages(self, chat: int, last_message: int = None, page_size=20, **kwargs):
        chat = self.get_object(pk=chat)
        if last_message:
            queryset = chat.messages.filter(id__lt=last_message)
        else:
            queryset = chat.messages.all()
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)
        serializer = MessageSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        data = dict(results=serializer.data, last_message=last_message, has_next=page_obj.has_next())
        return data, 200

    @action()
    async def create_message(self, data, **kwargs):
        await self.create_message_(data=data)

    @database_sync_to_async
    def create_message_(self, data: dict):
        serializer = MessageSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data

    @action()
    async def delete_message(self, request_id, pk, **kwargs):
        message = await self.get_message(pk=pk)
        if message is None:
            return self.reply(errors=['Not found'], action='delete', request_id=request_id, status=404)
        await self.delete_message_(message=message)

    @database_sync_to_async
    def delete_message_(self, message):
        if self.scope['user'] == message.user:
            if message.is_read:
                message.text = ''
                message.post = None
                message.ballot = None
                message.survey = None
                message.petition = None
                message.is_deleted = True
                message.save()
            else:
                message.delete()
        self.signal_chat(message.chat)
        return message

    @action()
    async def edit_message(self, request_id, pk, **kwargs):
        message = await self.get_message(pk=pk)
        if message is None:
            return self.reply(errors=['Not found'], action='update', request_id=request_id, status=404)
        await self.edit_message_(message=message, text=kwargs['data']['text'])

    @database_sync_to_async
    def edit_message_(self, message, text):
        message.text = text
        message.is_edited = True
        message.save()
        self.signal_chat(message.chat)
        return message

    @database_sync_to_async
    def get_message(self, pk):
        message = None
        message_qs = Message.objects.filter(pk=pk, user=self.scope["user"], is_deleted=False)
        if message_qs.exists():
            message = message_qs.first()
        return message

    @action()
    async def mark_as_read(self, pk, **kwargs):
        await self.mark_as_read_(pk=pk), 200

    @database_sync_to_async
    def mark_as_read_(self, pk):
        chat = Chat.objects.get(pk=pk)
        messages = chat.messages.filter(is_read=False)
        for message in messages:
            message.is_read = True
            message.save()
            notifications = Notification.objects.filter(message=message)
            for notification in notifications:
                notification.is_read = True
                notification.save()
        self.signal_chat(chat)
        return messages

    @action()
    async def direct_message(self, user_pks: list, data, request_id, **kwargs):
        chats = []
        for pk in user_pks:
            chat_data = await self.get_chat_data(dict(user=pk))
            if not chat_data:
                chat_data, status = await super().create(dict(user=pk), **kwargs)
                await self.join_chat(pk=chat_data["id"], request_id=request_id)
            data['chat'] = chat_data['id']
            await self.create_message_(data)
            chats.append(chat_data)
        return chats, 200

    @staticmethod
    def signal_chat(chat: Chat):
        return post_save.send(sender=Chat, instance=chat, created=False)

    @action()
    async def resubscribe(self, pks: list, request_id: str, **kwargs):
        for pk in pks:
            await self.subscribe(pk=pk, request_id=request_id)
        return {}, 200
