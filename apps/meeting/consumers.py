import logging

from channels.db import database_sync_to_async
from django.db import transaction
from django.db.models import QuerySet, Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, ListModelMixin, PatchModelMixin, RetrieveModelMixin, \
    DeleteModelMixin
from djangochannelsrestframework.observer import model_observer
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404

from apps.meeting.models import Meeting, SpeakerRequest
from apps.meeting.serializers import MeetingSerializer, SpeakerRequestSerializer
from apps.meeting.services import MeetingParticipantService
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit

logger = logging.getLogger(__name__)


class MeetingConsumer(CreateModelMixin, ListModelMixin, PatchModelMixin, RetrieveModelMixin, DeleteModelMixin,
                      GenericAsyncAPIConsumer):
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
    @model_observer(Meeting)
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
    def meeting_activity_serializer(self, instance: Meeting, _action, **kwargs):
        return {
            'data': {} if _action == 'delete' else MeetingSerializer(instance,
                                                                     context={"scope": {"user": instance.host}}).data,
            'action': _action.value,
            'pk': instance.pk,
            'response_status': 200
        }

    @model_observer(SpeakerRequest)
    async def speaker_request_activity(self, message, **kwargs):
        await self.send_json(message)

    @speaker_request_activity.groups_for_signal
    def speaker_request_activity_groups(self, instance: SpeakerRequest, **kwargs):
        yield f'meeting__{instance.meeting.pk}'

    @speaker_request_activity.groups_for_consumer
    def speaker_request_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'meeting__{pk}'

    @speaker_request_activity.serializer
    def speaker_request_activity_serializer(self, instance: SpeakerRequest, _action, **kwargs):
        return {
            'data': {} if _action == 'delete' else SpeakerRequestSerializer(instance, context={
                "scope": {"user": instance.meeting.host}}).data,
            'action': 'speaker_request_' + _action.value,
            'pk': instance.pk,
            'response_status': 200
        }

    async def websocket_disconnect(self, message):
        # Overriding [disconnect] method does not catch all disconnection scenarios
        logger.info(f"🔌 Disconnect called with message: {message} for user {self.scope.get('user')}")
        try:
            if self.scope['user'].is_authenticated:
                user_id = self.scope['user'].id
                await self.delete_all_user_requests()
                await database_sync_to_async(
                    MeetingParticipantService.cleanup_user_from_all_meetings
                )(user_id)
        except Exception as e:
            logger.error(f"Error during disconnect cleanup: {e}", exc_info=True)

        try:
            await self.meeting_activity.unsubscribe()
            await self.speaker_request_activity.unsubscribe()
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
                queryset = queryset.filter(Q(start_time__lte=end_date) & Q(end_time__gte=start_date))

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
    async def list(self, request_id: str, page_size=20, **kwargs):
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_user_regions()
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size or self.page_size, **kwargs)
        return data, 200

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
    async def subscribe(self, pk: int, request_id: str, is_muted: bool = False, **kwargs):
        await self.meeting_activity.subscribe(pk=pk, request_id=request_id)
        await self.speaker_request_activity.subscribe(pk=pk, request_id=request_id)
        if is_muted:
            await database_sync_to_async(
                MeetingParticipantService.set_mute_status
            )(meeting_id=pk, user_id=self.scope['user'].id, is_muted=is_muted)
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
        await self.speaker_request_activity.unsubscribe(pk=pk, request_id=request_id)
        meeting = await database_sync_to_async(self.get_object)(pk=pk)
        await database_sync_to_async(MeetingParticipantService.signal_meeting)(meeting=meeting)
        return {'pk': pk}, 200

    # ====================== Permission-Protected Actions ======================
    @action()
    @interaction_rate_limit
    async def patch(self, pk: int, **kwargs):  # Override to add explicit check
        meeting = await database_sync_to_async(self.get_object)(pk=pk)
        if meeting.host_id != self.scope['user'].id:
            raise PermissionDenied("Only the host can update this meeting.")
        return await super().patch(**kwargs)

    @action()
    @interaction_rate_limit
    async def delete(self, pk: int, **kwargs):
        meeting = await database_sync_to_async(self.get_object)(pk=pk)
        if meeting.host_id != self.scope['user'].id:
            raise PermissionDenied("Only the host can delete this meeting.")
        await database_sync_to_async(MeetingParticipantService.cleanup_meeting)(pk)
        response, status = await super().delete(**kwargs)
        return response, status

    # ====================== MUTE ======================

    @action()
    @interaction_rate_limit
    async def mute(self, pk: int, data: dict, **kwargs):
        meeting = await database_sync_to_async(self.get_object)(pk=pk)

        if not self._user_is_speaker(meeting):
            raise PermissionDenied("You are not a speaker.")

        await database_sync_to_async(
            MeetingParticipantService.set_mute_status
        )(meeting_id=pk, user_id=self.scope['user'].id, is_muted=data['is_muted'])
        await database_sync_to_async(MeetingParticipantService.signal_meeting)(meeting=meeting)

        return {"is_muted": data['is_muted']}, 200

    @database_sync_to_async
    def _user_is_speaker(self, meeting: Meeting):
        user = self.scope['user']
        return meeting.host_id == user.id or meeting.co_hosts.contains(user) or meeting.speakers.contains(user)

    @action()
    @interaction_rate_limit
    async def mute_speaker(self, pk: int, data: dict, **kwargs):
        """Host/Co-host mutes a participant"""
        meeting = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(meeting=meeting):
            raise PermissionDenied("Only hosts can mute speakers.")

        target_user_id = data.get('user_id')
        if not target_user_id:
            return await self.reply(action='mute_speaker', errors=['user_id is required'], status=400)

        await database_sync_to_async(
            MeetingParticipantService.set_mute_status
        )(meeting_id=pk, user_id=target_user_id, is_muted=True)
        await database_sync_to_async(MeetingParticipantService.signal_meeting)(meeting=meeting)

        return {"user_id": target_user_id, "is_muted": True}, 200

    @action()
    @interaction_rate_limit
    async def mute_everyone(self, pk: int, **kwargs):
        """Host/Co-host mutes every speaker in meeting"""
        meeting = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(meeting=meeting):
            raise PermissionDenied("Only hosts can mute speakers.")

        await database_sync_to_async(MeetingParticipantService.mute_everyone)(meeting=meeting,
                                                                              user_id=self.scope['user'].id)
        await database_sync_to_async(MeetingParticipantService.signal_meeting)(meeting=meeting)

        return {}, 200

    # ====================== SPEAKER REQUESTS ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def speaker_requests(self, pk: int, page_size=20, **kwargs):
        meeting = await database_sync_to_async(self.get_object)(pk=pk)
        if not await self._user_can_manage_speakers(meeting=meeting):
            raise PermissionDenied("Only the host can view speaker requests.")

        data = await self.get_requests(meeting=meeting, page_size=page_size, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_requests(self, meeting: Meeting, page_size: int, **kwargs):
        queryset = meeting.speaker_requests.filter(is_approved=None).exclude(id__in=kwargs.get('previous_requests', []))
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)
        serializer = SpeakerRequestSerializer(
            page_obj.object_list,
            many=True,
            context={'scope': self.scope}
        )

        return {
            'results': serializer.data,
            'previous_requests': kwargs.get('previous_requests'),
            'has_next': page_obj.has_next()
        }

    @action()
    @interaction_rate_limit
    async def request_to_speak(self, pk: int, **kwargs):
        """User requests to speak"""
        user = self.scope['user']
        meeting = await database_sync_to_async(self.get_object)(pk=pk)

        if not self._user_can_access_meeting(meeting):
            raise PermissionDenied("Not authorized to speak in this meeting.")

        data = await self._request_to_speak(user=user, meeting=meeting)

        return data, 200

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

    @database_sync_to_async
    def _request_to_speak(self, user, meeting: Meeting):
        request, created = SpeakerRequest.objects.get_or_create(
            meeting=meeting,
            user=user,
            defaults={'is_approved': None}
        )
        if not created:
            if request.is_approved is not None:
                request.is_approved = None
                request.save()
        return {}

    @action()
    @interaction_rate_limit
    async def handle_speaker_request(self, pk: int, data: dict, **kwargs):
        """Host approves or rejects a speaker request"""
        request = await self.get_speaker_request(pk=pk)

        if not await self._user_can_manage_speakers(meeting=request.meeting):
            raise PermissionDenied("Only hosts can manage speaker requests.")

        data = await self._handle_speaker_request(request=request, is_approved=data['is_approved'])

        return data, 200

    @staticmethod
    @database_sync_to_async
    def get_speaker_request(pk: int):
        return get_object_or_404(SpeakerRequest.objects.all(), pk=pk)

    @database_sync_to_async
    @transaction.atomic
    def _handle_speaker_request(self, request: SpeakerRequest, is_approved: bool, **kwargs):
        if is_approved:
            request.meeting.speakers.add(request.user)
        else:
            request.meeting.speakers.remove(request.user)
        request.is_approved = is_approved
        request.save()
        return {"user_id": request.user.id, "is_approved": is_approved, "decided_by": self.scope['user'].id}

    @action()
    @interaction_rate_limit
    async def manage_co_host(self, pk: int, user_id: int, **kwargs):
        """Host adds and removes co-hosts"""
        meeting = await database_sync_to_async(self.get_object)(pk=pk)

        if not meeting.host_id == self.scope['user'].id:
            raise PermissionDenied("Only the host can manage co-hosts.")

        if meeting.host_id == user_id:
            raise ValidationError("Cannot add host to co-hosts.")

        data = await self._manage_co_host(pk=pk, user_id=user_id)
        return data, 200

    @database_sync_to_async
    def _manage_co_host(self, pk: int, user_id: int):
        meeting: Meeting = self.get_object(pk=pk)

        if meeting.co_hosts.filter(pk=user_id).exists():
            meeting.co_hosts.remove(user_id)
            is_co_host = False
        else:
            if not meeting.speakers.filter(pk=user_id).exists():
                MeetingParticipantService.set_mute_status(meeting_id=pk, user_id=user_id, is_muted=True)
            meeting.co_hosts.add(user_id)
            meeting.speakers.remove(user_id)
            is_co_host = True
        MeetingParticipantService.signal_meeting(meeting)
        return {'pk': pk, 'is_co_host': is_co_host}

    @action()
    @interaction_rate_limit
    async def manage_speaker(self, pk: int, user_id: int, **kwargs):
        """Host and co-hosts add and remove speakers"""
        meeting = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(meeting=meeting):
            raise PermissionDenied("Only hosts can manage speaker requests.")

        if meeting.host_id == user_id:
            raise ValidationError("Cannot add host to speakers.")

        data = await self._manage_speaker(pk=pk, user_id=user_id)
        return data, 200

    @database_sync_to_async
    def _manage_speaker(self, pk: int, user_id: int):
        meeting: Meeting = self.get_object(pk=pk)

        if meeting.speakers.filter(pk=user_id).exists():
            meeting.speakers.remove(user_id)
            is_speaker = False
        else:
            if not meeting.co_hosts.filter(pk=user_id).exists():
                MeetingParticipantService.set_mute_status(meeting_id=pk, user_id=user_id, is_muted=True)
            meeting.speakers.add(user_id)
            meeting.co_hosts.remove(user_id)
            is_speaker = True
        MeetingParticipantService.signal_meeting(meeting)
        return {'pk': pk, 'is_speaker': is_speaker}

    @database_sync_to_async
    def _user_can_manage_speakers(self, meeting: Meeting):
        user = self.scope['user']
        return (
                meeting.host_id == user.id or
                meeting.co_hosts.filter(id=user.id).exists()
        )

    @database_sync_to_async
    def delete_all_user_requests(self):
        return SpeakerRequest.objects.filter(user=self.scope['user']).delete()
