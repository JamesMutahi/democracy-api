from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action
from rest_framework import serializers

from .models import Message, Room
from .serializers import MessageSerializer, RoomSerializer

User = get_user_model()


class RoomConsumer(ListModelMixin, CreateModelMixin, ObserverModelInstanceMixin, GenericAsyncAPIConsumer):
    serializer_class = RoomSerializer
    queryset = Room.objects.all()
    lookup_field = "pk"

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        return queryset.filter(users=self.scope['user'])

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        room_pk = response["pk"]
        await self.subscribe_instance(request_id=request_id, pk=room_pk)
        return response, status

    @action()
    async def join_room(self, pk, request_id, **kwargs):
        room = await database_sync_to_async(self.get_object)(pk=pk)
        await self.subscribe_instance(request_id=request_id, pk=room.pk)
        await self.message_activity.subscribe(room=pk, request_id=request_id)
        await self.add_user_to_room(room)

    @action()
    async def leave_room(self, pk, **kwargs):
        room = await database_sync_to_async(self.get_object)(pk=pk)
        await self.unsubscribe_instance(pk=room.pk)
        await self.message_activity.unsubscribe(room=room.pk)
        await self.remove_user_from_room(room)

    @database_sync_to_async
    def add_user_to_room(self, room: Room):
        user: User = self.scope["user"]
        room.users.add(user)

    @database_sync_to_async
    def remove_user_from_room(self, room: Room):
        user: User = self.scope["user"]
        room.users.remove(user)

    @action()
    async def create_message(self, message, room, **kwargs):
        return await self.create_message_(room=room, message=message), 201

    @database_sync_to_async
    def create_message_(self, room, message):
        obj = Room.objects.get(pk=room)
        Message.objects.create(room=obj, user=self.scope['user'], text=message)
        serializer = RoomSerializer(obj, context={'scope': self.scope})
        return serializer.data

    @action()
    async def delete_message(self, pk, **kwargs):
        return await self.delete_message_(pk=pk)

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
            return {"room": RoomSerializer(Room.objects.get(pk=message.room.id), context={'scope': self.scope}).data,
                    "message": MessageSerializer(message, context={'scope': self.scope}).data}, 204
        raise serializers.ValidationError(detail='Permission denied', code=403)

    @action()
    async def edit_message(self, pk, text, **kwargs):
        return await self.edit_message_(pk=pk, text=text)

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
        return {"room": RoomSerializer(Room.objects.get(pk=message.room.id), context={'scope': self.scope}).data,
                "message": MessageSerializer(message, context={'scope': self.scope}).data}, 200

    @action()
    async def mark_as_read(self, pk, **kwargs):
        return await self.mark_as_read_(pk=pk)

    @database_sync_to_async
    def mark_as_read_(self, pk):
        room = Room.objects.get(pk=pk)
        for message in room.messages.filter(is_read=False):
            message.is_read = True
            message.save()
        return RoomSerializer(Room.objects.get(pk=pk), context={'scope': self.scope}).data, 200

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
        yield f'room__{instance.room.id}'

    @message_activity.groups_for_consumer
    def message_activity(self, room=None, **kwargs):
        if room is not None:
            yield f'room__{room}'

    @message_activity.serializer
    def message_activity(self, instance: Message, action, **kwargs):
        """
        This is evaluated before the update is sent
        out to all the subscribing consumers.
        """
        return dict(
            data=MessageSerializer(instance).data,
            action=action.value,
            pk=instance.pk
        )
