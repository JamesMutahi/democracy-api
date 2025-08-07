from channels.db import database_sync_to_async
from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.observer import model_observer

from chat.utils.list_paginator import list_paginator
from notification.models import Notification
from notification.serializers import NotificationSerializer


class NotificationConsumer(GenericAsyncAPIConsumer):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    lookup_field = "pk"
    page_size = 12

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
        instance: Notification = message.pop('data')
        if await self.check_notification_is_for_user(instance):
            if message['action'] != 'delete':
                message['data'] = await self.get_notification_serializer_data(notification=instance)
            await self.send_json(message)

    @database_sync_to_async
    def check_notification_is_for_user(self, instance: Notification):
        return self.scope['user'].id == instance.user.id

    @database_sync_to_async
    def get_notification_serializer_data(self, notification: Notification):
        serializer = NotificationSerializer(instance=notification, context={'scope': self.scope})
        return serializer.data

    @notification_activity.serializer
    def notification_activity(self, instance: Notification, action, **kwargs):
        return dict(
            data=instance,
            action=action.value,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @action()
    async def list(self, request_id: str, last_notification: int = None, page_size=page_size, **kwargs):
        notifications = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.notifications_paginator(notifications=notifications, page_size=page_size,
                                                  last_notification=last_notification, **kwargs)
        return data, 200

    @database_sync_to_async
    def notifications_paginator(self, notifications, page_size, last_notification: int = None, **kwargs):
        if last_notification:
            notification = Notification.objects.get(pk=last_notification)
            notifications = notifications.filter(id__lt=notification.id)
        page_obj = list_paginator(queryset=notifications, page=1, page_size=page_size)
        serializer = NotificationSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, last_notification=last_notification, has_next=page_obj.has_next())

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
