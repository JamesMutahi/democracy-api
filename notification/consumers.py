from channels.db import database_sync_to_async
from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer

from notification.models import Notification, Preferences
from notification.serializers import NotificationSerializer, PreferencesSerializer


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
        pk = message['data']
        notification = await database_sync_to_async(self.get_object)(pk=pk)
        if await self.check_notification_is_for_user(notification=notification):
            if message['action'] != 'delete':
                message['data'] = await self.get_notification_serializer_data(notification=notification)
            await self.send_json(message)

    @database_sync_to_async
    def check_notification_is_for_user(self, notification: Notification):
        return self.scope['user'].id == notification.user.id

    @database_sync_to_async
    def get_notification_serializer_data(self, notification: Notification):
        serializer = NotificationSerializer(instance=notification, context={'scope': self.scope})
        return serializer.data

    @notification_activity.serializer
    def notification_activity(self, instance: Notification, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            # TODO: Too many database hits. Pass more fields to data in dict
            data=instance.pk,
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

    @action()
    def preferences(self, **kwargs):
        preferences, created = Preferences.objects.get_or_create(user=self.scope['user'])
        serializer = PreferencesSerializer(preferences, context={'scope': self.scope})
        return serializer.data, 200

    @action()
    def update_preferences(self, **kwargs):
        preferences = Preferences.objects.get(user=self.scope['user'])
        serializer = PreferencesSerializer(preferences, data=kwargs['data'], partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data, 200

    def mute_post(self, pk: int, **kwargs):
        # Disable notifications for the post
        pass
