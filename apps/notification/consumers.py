import logging

from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin

from apps.notification.models import Notification, Preferences
from apps.notification.serializers import NotificationSerializer, PreferencesSerializer

logger = logging.getLogger(__name__)


class NotificationConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    lookup_field = "pk"

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()

            # Join personal notification group — Celery will send here
            self.notification_group = f"notifications_{self.scope['user'].id}"
            await self.channel_layer.group_add(
                self.notification_group,
                self.channel_name
            )

            logger.info(f"User {self.scope['user'].id} joined notification group")
        else:
            await self.close()

    async def disconnect(self, code):
        if hasattr(self, 'notification_group'):
            await self.channel_layer.group_discard(
                self.notification_group,
                self.channel_name
            )
        await super().disconnect(code)

    # ====================== UNIFIED NOTIFICATION HANDLER ======================
    async def notification_activity(self, event):
        """
        Handles both create and delete events from Celery.
        """
        action = event.get("action")

        if action == "delete":
            # Delete payload
            await self.send_json({
                "action": "delete",
                "pk": event.get("pk"),
                "response_status": 204,
            })
        else:
            # create (and future update actions)
            await self.send_json(event)

    # ====================== FILTER & ACTIONS ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        return queryset.filter(recipient=self.scope['user'])

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
