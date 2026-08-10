import logging

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin

from apps.notification.models import Notification, Preferences
from apps.notification.serializers import NotificationSerializer, PreferencesSerializer
from apps.notification.tasks import send_notification_update
from apps.utils.throttles import interaction_rate_limit, rate_limit

logger = logging.getLogger(__name__)


class NotificationConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    lookup_field = "pk"

    async def connect(self):
        user = self.scope.get("user")

        if user and user.is_authenticated:
            await self.accept()

            # Join personal notification group — Celery will send here
            self.notification_group = f"notifications_{user.id}"
            await self.channel_layer.group_add(
                self.notification_group,
                self.channel_name,
            )

            logger.info("User %s joined notification group", user.id)
        else:
            await self.close()

    async def disconnect(self, code):
        group = getattr(self, "notification_group", None)
        if group:
            await self.channel_layer.group_discard(
                group,
                self.channel_name,
            )

        await super().disconnect(code)

    # ====================== UNIFIED NOTIFICATION HANDLER ======================

    async def notification_activity(self, event):
        """
        Handles create / update / delete / mark_all_read events from Celery or consumer actions.
        """
        action_name = event.get("action")

        if action_name == "delete":
            await self.send_json({
                "action": "delete",
                "pk": event.get("pk"),
                "response_status": 204,
            })
        elif action_name == "mark_all_read":
            await self.send_json({
                "action": "mark_all_read",
                "data": event.get("data", {}),
                "response_status": 200,
            })
        else:
            # create and update actions
            await self.send_json(event)

    # ====================== QUERYSET ======================

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            return queryset.none()

        return queryset.filter(
            recipient=user
        ).select_related(
            "post__author",
            "ballot",
            "survey",
            "petition__author",
            "broadcast__host",
            "chat",
            "message__author",
        ).prefetch_related(
            "users",
        ).order_by("-id")

    # ====================== ACTIONS ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id=None, page_size=None, **kwargs):
        return await super().list(request_id=request_id, **kwargs)

    @action()
    @interaction_rate_limit
    async def mark_as_read(self, pk: int, request_id=None, **kwargs):
        data = await self._mark_as_read(pk=pk)

        if data is None:
            return await self.reply(
                action="update",
                request_id=request_id,
                status=404,
            )

        return await self.reply(
            data=data,
            action="update",
            request_id=request_id,
            status=200,
        )

    @database_sync_to_async
    def _mark_as_read(self, pk):
        user = self.scope.get("user")

        notification = Notification.objects.filter(
            pk=pk,
            recipient=user,
        ).first()

        if not notification:
            return None

        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read"])

            # Notify other active connections for the same user too.
            send_notification_update(notification)

        serializer = NotificationSerializer(notification, context={"scope": self.scope})
        return serializer.data

    @action()
    @interaction_rate_limit
    async def mark_all_as_read(self, request_id=None, **kwargs):
        updated = await self._mark_all_as_read()

        group = getattr(self, "notification_group", None)
        channel_layer = get_channel_layer()

        if group and channel_layer:
            try:
                await channel_layer.group_send(
                    group,
                    {
                        "type": "notification_activity",
                        "action": "mark_all_read",
                        "data": {"updated": updated},
                        "response_status": 200,
                    },
                )
            except Exception:
                logger.exception("Failed to send mark_all_read event")

        return await self.reply(
            data={"updated": updated},
            action="update",
            request_id=request_id,
            status=200,
        )

    @database_sync_to_async
    def _mark_all_as_read(self):
        user = self.scope.get("user")
        return Notification.objects.filter(
            recipient=user,
            is_read=False,
        ).update(is_read=True)

    @action()
    @rate_limit(limit=40, period=60)
    async def preferences(self, request_id=None, **kwargs):
        data = await self._preferences()
        return await self.reply(
            data=data,
            request_id=request_id,
            status=200,
        )

    @database_sync_to_async
    def _preferences(self):
        user = self.scope.get("user")
        preferences, _ = Preferences.objects.get_or_create(user=user)
        serializer = PreferencesSerializer(preferences, context={"scope": self.scope})
        return serializer.data

    @action()
    @rate_limit(limit=40, period=60)
    async def update_preferences(self, request_id=None, data=None, **kwargs):
        payload = await self._update_preferences(data or {})
        return await self.reply(
            data=payload,
            request_id=request_id,
            status=200,
        )

    @database_sync_to_async
    def _update_preferences(self, data):
        user = self.scope.get("user")
        preferences, _ = Preferences.objects.get_or_create(user=user)

        serializer = PreferencesSerializer(
            preferences,
            data=data,
            partial=True,
            context={"scope": self.scope},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return serializer.data
