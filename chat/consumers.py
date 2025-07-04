from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action

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

    @action()
    async def create(self, data: dict, request_id: int, **kwargs):
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.join_chat(pk=pk, request_id=request_id)
        return response, status

    async def join_chat(self, pk, request_id, **kwargs):
        await self.subscribe_instance(pk=pk, request_id=request_id)
        await self.message_activity.subscribe(chat=pk, request_id=request_id)

    @action()
    async def join_chats(self, request_id, **kwargs):
        chat_pks = await self.get_chat_pks()
        for pk in chat_pks:
            await self.join_chat(pk=pk, request_id=request_id)

    @database_sync_to_async
    def get_chat_pks(self):
        chat_pks = list(self.scope['user'].chats.all().values_list('pk', flat=True))
        return chat_pks

    @action()
    async def create_message(self, text, chat, **kwargs):
        chat: Chat = await database_sync_to_async(self.get_object)(pk=chat)
        await database_sync_to_async(Message.objects.create)(chat=chat, user=self.scope["user"], text=text)

    @action()
    async def delete_message(self, request_id, pk, **kwargs):
        message = await self.get_message(pk=pk)
        if message is None:
            return self.reply(errors=['Not found'], action='delete', request_id=request_id, status=404)
        is_author = await self.check_author(message=message)
        if not is_author:
            return self.reply(errors=['Forbidden'], action='delete', request_id=request_id, status=403)
        await self.delete_message_(message=message)

    @database_sync_to_async
    def delete_message_(self, message):
        if self.scope['user'] == message.user:
            message.text = 'Deleted'
            message.is_deleted = True
            message.save()
        return message

    @action()
    async def edit_message(self, request_id, pk, text, **kwargs):
        message = await self.get_message(pk=pk)
        if message is None:
            return self.reply(errors=['Not found'], action='update', request_id=request_id, status=404)
        is_author = await self.check_author(message=message)
        if not is_author:
            return self.reply(errors=['Forbidden'], action='update', request_id=request_id, status=403)
        is_deleted = await self.check_is_deleted(message=message)
        if is_deleted:
            return self.reply(errors=['Message was deleted'], action='update', request_id=request_id, status=404)
        await self.edit_message_(message=message, text=text)

    @database_sync_to_async
    def edit_message_(self, message, text):
        message.text = text
        message.is_edited = True
        message.save()
        return message

    @database_sync_to_async
    def get_message(self, pk):
        message = None
        message_qs = Message.objects.filter(pk=pk)
        if message_qs.exists():
            message = message_qs.first()
        return message

    @database_sync_to_async
    def check_author(self, message):
        return self.scope['user'] == message.user

    @database_sync_to_async
    def check_is_deleted(self, message):
        return message.is_deleted

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
                chat.save()
        return chat

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
        # Since we provide the request_id when subscribing, we can just loop over them here.
        for request_id in subscribing_request_ids:
            message_body = dict(request_id=request_id)
            message_body.update(message)
            await self.send_json(message_body)

    @message_activity.groups_for_signal
    def message_activity(self, instance: Message, **kwargs):
        yield f'chat__{instance.chat.id}'

    @message_activity.groups_for_consumer
    def message_activity(self, chat=None, **kwargs):
        if chat is not None:
            yield f'chat__{chat}'

    @message_activity.serializer
    def message_activity(self, instance: Message, action, **kwargs):
        """
        This is evaluated before the update is sent
        out to all the subscribing consumers.
        """
        return dict(
            data=MessageSerializer(instance).data,
            action=action.value,
            request_id=2,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 200
        )
