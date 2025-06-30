from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action
from rest_framework import serializers

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
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.join_chat(pk=pk, request_id=request_id)
        return response, status

    @action()
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
        await self.create_message_(chat=chat, text=text), 201

    @database_sync_to_async
    def create_message_(self, chat, text):
        obj = Chat.objects.get(pk=chat)
        message = Message.objects.create(chat=obj, user=self.scope['user'], text=text)
        return message

    @action()
    async def delete_message(self, pk, **kwargs):
        await self.delete_message_(pk=pk)

    @database_sync_to_async
    def delete_message_(self, pk):
        message_qs = Message.objects.filter(pk=pk)
        if not message_qs.exists():
            raise serializers.ValidationError('Message does not exist', code=404)
        message = message_qs.first()
        if self.scope['user'] == message.user:
            message.text = 'Deleted'
            message.is_deleted = True
            message.save()
        return message

    @action()
    async def edit_message(self, pk, text, **kwargs):
        await self.edit_message_(pk=pk, text=text)

    @database_sync_to_async
    def edit_message_(self, pk, text):
        message_qs = Message.objects.filter(pk=pk)
        if not message_qs.exists():
            raise serializers.ValidationError('Message does not exist', code=404)
        message = message_qs.first()
        if self.scope['user'] != message.user:
            raise serializers.ValidationError(detail='Permission denied', code=403)
        if message.is_deleted:
            raise serializers.ValidationError(detail='Message was deleted', code=404)
        message.text = text
        message.is_edited = True
        message.save()
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
        obj = None
        for chat in chats:
            if chat.users.all().contains(user):
                obj = chat
                obj.save()
        return obj

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
