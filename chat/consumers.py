from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Q
from django.db.models.signals import post_save
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action

from users.serializers import UserSerializer
from .models import Message, Chat
from .serializers import MessageSerializer, ChatSerializer

User = get_user_model()


class ChatConsumer(ListModelMixin, CreateModelMixin, ObserverModelInstanceMixin, GenericAsyncAPIConsumer):
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
            await self.join_chat(pk=pk, request_id='1')

    @model_observer(Message)
    async def message_activity(
            self,
            message,
            observer=None,
            subscribing_request_ids=[],
            **kwargs
    ):
        """
        This is evaluated once for each subscribed consumer.
        The result of `@message_activity.serializer` is provided here as the message.
        """
        if message['action'] != 'delete':
            message['data'] = await self.get_message_serializer_data(pk=message['pk'])
        # Since we provide the request_id when subscribing, we can just loop over them here.
        for request_id in subscribing_request_ids:
            message_body = dict(request_id=request_id)
            message_body.update(message)
            await self.send_json(message_body)

    @database_sync_to_async
    def get_message_serializer_data(self, pk):
        message = Message.objects.get(pk=pk)
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
            # data is overridden in model_observer
            action=action.value,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    async def disconnect(self, code):
        chat_pks = await self.get_chat_pks()
        for pk in chat_pks:
            await self.unsubscribe_instance(pk=pk)
        await self.message_activity.unsubscribe()
        await super().disconnect(code)

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.join_chat(pk=pk, request_id=request_id)
        return response, status

    async def join_chat(self, pk, request_id, **kwargs):
        await self.subscribe_instance(pk=pk, request_id=request_id)
        await self.message_activity.subscribe(chat=pk, request_id=request_id)

    @database_sync_to_async
    def get_chat_pks(self):
        chat_pks = list(self.scope['user'].chats.all().values_list('pk', flat=True))
        return chat_pks

    @action()
    async def create_message(self, data, **kwargs):
        await self.create_message_(data=data)

    @database_sync_to_async
    def create_message_(self, data: dict):
        serializer = MessageSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        return serializer.save()

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
                message.text = 'Deleted'
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
        return messages

    @action()
    async def block_user(self, user: int, **kwargs):
        await self.block_user_(pk=user), 200

    @database_sync_to_async
    def block_user_(self, pk: int):
        user = User.objects.get(pk=pk)
        if user in self.scope['user'].blocked.all():
            self.scope['user'].blocked.remove(user)
        else:
            self.scope['user'].blocked.add(user)
        chats = self.scope['user'].chats.all()
        chat = None
        for c in chats:
            if c.users.all().contains(user):
                chat = c
                post_save.send(sender=Chat, instance=chat, created=False)
        return chat

    @action()
    def search_users(self, search_term: str, **kwargs):
        if search_term == '':
            # TODO: Customize to have user's following and chats
            users = User.objects.all().order_by('first_name')
            serializer = UserSerializer(users, many=True, context={'scope': self.scope})
            return serializer.data, 200
        else:
            users = User.objects.filter(
                Q(username__icontains=search_term) |
                Q(display_name__icontains=search_term) |
                Q(first_name__icontains=search_term) |
                Q(last_name__icontains=search_term)
            ).distinct()
            serializer = UserSerializer(users, many=True, context={'scope': self.scope})
            return serializer.data, 200

    @action()
    async def direct_message(self, user_pks: list, data, request_id, **kwargs):
        for pk in user_pks:
            chat = await self.get_or_create_chat(pk)
            await self.join_chat(pk=chat, request_id=request_id)
            data['chat'] = chat
            await self.create_message_(data)
        return {}, 200

    @database_sync_to_async
    def get_or_create_chat(self, pk):
        serializer = ChatSerializer(data={'user': pk}, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data['id']
