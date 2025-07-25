from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Count
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import action

from notification.models import Notification
from .models import Message, Chat
from .serializers import MessageSerializer, ChatSerializer

User = get_user_model()


class ChatConsumer(ListModelMixin, CreateModelMixin, GenericAsyncAPIConsumer):
    serializer_class = ChatSerializer
    queryset = Chat.objects.all()
    lookup_field = "pk"

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        return queryset.filter(users=self.scope['user'])

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        chat_pks = await self.get_chat_pks()
        for pk in chat_pks:
            await self.join_chat(pk=pk, request_id='chats')

    @database_sync_to_async
    def get_chat_pks(self):
        chat_pks = list(self.scope['user'].chats.all().values_list('pk', flat=True))
        return chat_pks

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
        await self.chat_activity.unsubscribe()
        await self.message_activity.unsubscribe()
        await super().disconnect(code)

    async def join_chat(self, pk, request_id, **kwargs):
        await self.chat_activity.subscribe(pk=pk, request_id=request_id)
        await self.message_activity.subscribe(chat=pk, request_id=request_id)

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        chat_data = await self.get_chat_data_if_exists(data)
        if chat_data:
            return chat_data, 201
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.join_chat(pk=pk, request_id=request_id)
        return response, status

    @database_sync_to_async
    def get_chat_data_if_exists(self, data: dict):
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
    async def subscribe(self, pk: int, request_id: str, **kwargs):
        await database_sync_to_async(self.get_object)(pk=pk)
        await self.join_chat(pk=pk, request_id=request_id)

    @action()
    def messages(self, pk: int, **kwargs):
        chat = self.get_object(pk=pk)
        serializer = MessageSerializer(chat.messages.all(), many=True, context={'scope': self.scope})
        return serializer.data, 200

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
                message.poll = None
                message.survey = None
                message.is_deleted = True
                message.save()
            else:
                message.delete()
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
        return messages

    @action()
    async def direct_message(self, user_pks: list, data, request_id, **kwargs):
        for pk in user_pks:
            chat_data = await self.get_chat_data_if_exists(dict(user=pk))
            if not chat_data:
                chat_data, status = await super().create(dict(user=pk), **kwargs)
                await self.join_chat(pk=chat_data["id"], request_id=request_id)
            data['chat'] = chat_data['id']
            await self.create_message_(data)
        return {}, 200
