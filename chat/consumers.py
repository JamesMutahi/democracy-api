from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action

from .models import Message, Room
from .serializers import MessageSerializer, RoomSerializer

User = get_user_model()


class RoomConsumer(CreateModelMixin, ObserverModelInstanceMixin, GenericAsyncAPIConsumer):
    serializer_class = RoomSerializer
    queryset = Room.objects.all()
    lookup_field = "pk"

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
        return await self.create_message_(room=room,  message=message), 200

    @database_sync_to_async
    def create_message_(self, room, message):
        obj = Room.objects.get(pk=room)
        Message.objects.create(room=obj, user=self.scope['user'], text=message)
        serializer = RoomSerializer(obj, context={'scope': self.scope})
        return serializer.data

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
