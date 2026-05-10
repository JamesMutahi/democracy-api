import logging

from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, ListModelMixin, PatchModelMixin, RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer

from apps.meeting.models import Meeting
from apps.meeting.serializers import MeetingSerializer
from apps.meeting.services import MeetingParticipantService
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit

logger = logging.getLogger(__name__)


class MeetingConsumer(CreateModelMixin, ListModelMixin, PatchModelMixin, RetrieveModelMixin, GenericAsyncAPIConsumer):
    queryset = Meeting.objects.all()
    serializer_class = MeetingSerializer
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Real-time Observer ======================
    @model_observer(Meeting, many_to_many=True)
    async def meeting_activity(self, message, **kwargs):
        await self.send_json(message)

    @meeting_activity.groups_for_signal
    def meeting_activity_groups(self, instance: Meeting, **kwargs):
        yield f'meeting__{instance.pk}'

    @meeting_activity.groups_for_consumer
    def meeting_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'meeting__{pk}'

    @meeting_activity.serializer
    def meeting_activity_serializer(self, instance: Meeting, action, **kwargs):
        if action == 'delete':
            return {}
        return {
            'data': MeetingSerializer(instance, context={"scope": {"user": instance.host}}).data,
            'action': action.value,
            'pk': instance.pk,
            'response_status': 201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        }

    async def websocket_disconnect(self, message):
        # Overriding [disconnect] method does not catch all disconnection scenarios
        logger.info(f"🔌 Disconnect called with message: {message} for user {self.scope.get('user')}")
        try:
            if self.scope['user'].is_authenticated:
                user_id = self.scope['user'].id
                await database_sync_to_async(
                    MeetingParticipantService.cleanup_user_from_all_meetings
                )(user_id)
        except Exception as e:
            logger.error(f"Error during disconnect cleanup: {e}", exc_info=True)

        try:
            await self.meeting_activity.unsubscribe()
        except Exception as e:
            logger.warning(f"Error unsubscribing observer: {e}")
        await super().websocket_disconnect(message)

    # ====================== Filter with Permissions ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        previous_meetings = kwargs.get('previous_meetings')
        action = kwargs.get('action')
        search_term = kwargs.get('search_term')
        is_active = kwargs.get('is_active', True)
        filter_by_region = kwargs.get('filter_by_region', True)
        sort_by = kwargs.get('sort_by', 'recent')
        start_date = kwargs.get('start_date')
        end_date = kwargs.get('end_date')
        county = kwargs.get('county')
        constituency = kwargs.get('constituency')
        ward = kwargs.get('ward')

        if previous_meetings:
            queryset = queryset.exclude(id__in=previous_meetings)

        if action == 'list':
            queryset = queryset.filter(is_live_stream=False)
            # Search
            if search_term:
                queryset = queryset.filter(
                    Q(title__icontains=search_term) |
                    Q(description__icontains=search_term) |
                    Q(host__name__icontains=search_term) |
                    Q(county__name__icontains=search_term) |
                    Q(constituency__name__icontains=search_term) |
                    Q(ward__name__icontains=search_term)
                ).distinct()

            # Active status
            if is_active is not None:
                queryset = queryset.filter(is_active=bool(is_active))

            # Regional filtering
            if filter_by_region and (county or constituency or ward):
                region_q = Q()
                if county:
                    region_q &= Q(county__isnull=True) | Q(county=county)
                if constituency:
                    region_q &= Q(constituency__isnull=True) | Q(constituency=constituency)
                if ward:
                    region_q &= Q(ward__isnull=True) | Q(ward=ward)
                queryset = queryset.filter(region_q)

            # Date range
            if start_date and end_date:
                queryset = queryset.filter(start_time__range=(start_date, end_date))

            # Sorting
            if sort_by == 'recent':
                queryset = queryset.order_by('-start_time')
            elif sort_by == 'oldest':
                queryset = queryset.order_by('start_time')

            return queryset

        elif action == 'user_meetings':
            return queryset.filter(host=kwargs.get('user'))

        # === Permission Checks for Sensitive Actions ===
        elif action in ['patch', 'delete']:
            # Only the host can patch or delete a meeting
            host_filter = Q(host=self.scope['user'])
            return queryset.filter(host_filter)

        return queryset

    # ====================== List & Create ======================
    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id: str, page_size=None, **kwargs):
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_user_regions()

        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size or self.page_size, **kwargs)

        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def get_user_regions(self):
        user = self.scope['user']
        return user.county, user.constituency, user.ward

    @database_sync_to_async
    def list_(self, queryset, page_size: int, **kwargs):
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = MeetingSerializer(
            page_obj.object_list,
            many=True,
            context={'scope': self.scope}
        )

        return {
            'results': serializer.data,
            'previous_meetings': kwargs.get('previous_meetings'),
            'has_next': page_obj.has_next()
        }

    @action()
    @rate_limit(limit=40, period=60)
    async def user_meetings(self, request_id: str, page_size=None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size or self.page_size, **kwargs)
        return data, 200

    # ====================== Join / Leave ======================
    @action()
    @interaction_rate_limit
    async def subscribe(self, pk: int, request_id: str, **kwargs):
        await self.meeting_activity.subscribe(pk=pk, request_id=request_id)
        result = await self.add_participant(pk=pk)
        return result, 200

    @database_sync_to_async
    def add_participant(self, pk: int):
        MeetingParticipantService.user_joined(pk, self.scope['user'].id)
        meeting = Meeting.objects.select_related('county', 'constituency', 'ward').get(pk=pk)
        MeetingParticipantService.signal_meeting(meeting)
        return MeetingSerializer(meeting, context={'scope': self.scope}).data

    @action()
    @interaction_rate_limit
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await database_sync_to_async(MeetingParticipantService.user_left)(pk, self.scope['user'].id)
        await self.meeting_activity.unsubscribe(pk=pk, request_id=request_id)
        meeting = await database_sync_to_async(self.get_object)(pk=pk)
        await database_sync_to_async(MeetingParticipantService.signal_meeting)(meeting=meeting)
        return {'pk': pk}, 200

    def _user_can_access_meeting(self, meeting: Meeting) -> bool:
        user = self.scope['user']
        if not meeting.county:
            return True
        if meeting.county != user.county:
            return False
        if meeting.constituency and meeting.constituency != user.constituency:
            return False
        if meeting.ward and meeting.ward != user.ward:
            return False
        return True

    # ====================== Permission-Protected Actions ======================
    @action()
    @interaction_rate_limit
    async def patch(self, pk: int, **kwargs):  # Override to add explicit check
        meeting = await database_sync_to_async(self.get_object)(pk=pk)
        if meeting.host_id != self.scope['user'].id:
            return await self.reply(
                action='patch',
                errors=['Only the host can update this meeting'],
                status=403
            )
        return await super().patch(**kwargs)

    @action()
    @interaction_rate_limit
    async def delete(self, pk: int, **kwargs):
        meeting = await database_sync_to_async(self.get_object)(pk=pk)
        if meeting.host_id != self.scope['user'].id:
            return await self.reply(
                action='delete',
                errors=['Only the host can delete this meeting'],
                status=403
            )
        await database_sync_to_async(MeetingParticipantService.cleanup_meeting)(pk)
        response, status = await super().delete(**kwargs)
        return response, status
