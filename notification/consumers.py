from channels.db import database_sync_to_async
from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer

from notification.models import Notification
from notification.serializers import NotificationSerializer


class NotificationConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    lookup_field = "pk"

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        return queryset.filter(user=self.scope['user'])

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        await self.notification_activity.subscribe()

    @model_observer(Notification)
    async def notification_activity(self, message, observer=None, action=None, **kwargs):
        if await self.check_notification_is_for_user(pk=message['data']['user']):
            if message['action'] != 'delete':
                message['data'] = await self.get_notification_serializer_data(pk=message['pk'])
            await self.send_json(message)

    @database_sync_to_async
    def check_notification_is_for_user(self, pk: int):
        return self.scope['user'].id == pk

    @database_sync_to_async
    def get_notification_serializer_data(self, pk):
        notification = Notification.objects.get(pk=pk)
        serializer = NotificationSerializer(instance=notification, context={'scope': self.scope})
        return serializer.data

    @notification_activity.serializer
    def notification_activity(self, instance: Notification, action, **kwargs):
        return dict(
            data={'user': instance.user.pk},
            action=action.value,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @action()
    async def mark_as_read(self, pk: int, request_id, **kwargs):
        data = await self.mark_as_read_(pk=pk)
        return await self.reply(data=data, action='update', request_id=request_id, status=200)

    @action()
    def mark_as_read_(self, pk, **kwargs):
        notification = Notification.objects.get(pk=pk)
        notification.is_read = True
        notification.save()
        serializer = NotificationSerializer(notification, context={'scope': self.scope})
        return serializer.data
