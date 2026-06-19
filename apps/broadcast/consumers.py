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

from apps.broadcast.models import Broadcast, SpeakerRequest
from apps.broadcast.serializers import BroadcastSerializer, SpeakerRequestSerializer
from apps.broadcast.services import BroadcastParticipantService
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit

logger = logging.getLogger(__name__)


class BroadcastConsumer(CreateModelMixin, ListModelMixin, PatchModelMixin, RetrieveModelMixin, DeleteModelMixin,
                      GenericAsyncAPIConsumer):
    queryset = Broadcast.objects.all()
    serializer_class = BroadcastSerializer
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Real-time Observer ======================
    @model_observer(Broadcast)
    async def broadcast_activity(self, message, **kwargs):
        await self.send_json(message)

    @broadcast_activity.groups_for_signal
    def broadcast_activity_groups(self, instance: Broadcast, **kwargs):
        yield f'broadcast__{instance.pk}'

    @broadcast_activity.groups_for_consumer
    def broadcast_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'broadcast__{pk}'

    @broadcast_activity.serializer
    def broadcast_activity_serializer(self, instance: Broadcast, _action, **kwargs):
        return {
            'data': {} if _action == 'delete' else BroadcastSerializer(instance,
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
        yield f'broadcast__{instance.broadcast.pk}'

    @speaker_request_activity.groups_for_consumer
    def speaker_request_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'broadcast__{pk}'

    @speaker_request_activity.serializer
    def speaker_request_activity_serializer(self, instance: SpeakerRequest, _action, **kwargs):
        return {
            'data': {} if _action == 'delete' else SpeakerRequestSerializer(instance, context={
                "scope": {"user": instance.broadcast.host}}).data,
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
                    BroadcastParticipantService.cleanup_user_from_all_broadcasts
                )(user_id)
        except Exception as e:
            logger.error(f"Error during disconnect cleanup: {e}", exc_info=True)

        try:
            await self.broadcast_activity.unsubscribe()
            await self.speaker_request_activity.unsubscribe()
        except Exception as e:
            logger.warning(f"Error unsubscribing observer: {e}")
        await super().websocket_disconnect(message)

    # ====================== Filter with Permissions ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        previous_broadcasts = kwargs.get('previous_broadcasts')
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

        if previous_broadcasts:
            queryset = queryset.exclude(id__in=previous_broadcasts)

        if action == 'list':
            queryset = queryset.filter(type=Broadcast.Type.MEETING)
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
                queryset = queryset.filter(is_active=is_active)

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

        elif action == 'user_broadcasts':
            return queryset.filter(host=kwargs.get('user'))

        # === Permission Checks for Sensitive Actions ===
        elif action in ['patch', 'delete']:
            # Only the host can patch or delete a broadcast
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

        serializer = BroadcastSerializer(
            page_obj.object_list,
            many=True,
            context={'scope': self.scope}
        )

        return {
            'results': serializer.data,
            'previous_broadcasts': kwargs.get('previous_broadcasts'),
            'has_next': page_obj.has_next()
        }

    @action()
    @rate_limit(limit=40, period=60)
    async def user_broadcasts(self, request_id: str, page_size=None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size or self.page_size, **kwargs)
        return data, 200

    # ====================== Join / Leave ======================
    @action()
    @interaction_rate_limit
    async def subscribe(self, pk: int, request_id: str, is_muted: bool = False, **kwargs):
        await self.broadcast_activity.subscribe(pk=pk, request_id=request_id)
        await self.speaker_request_activity.subscribe(pk=pk, request_id=request_id)
        if is_muted:
            await database_sync_to_async(
                BroadcastParticipantService.set_mute_status
            )(broadcast_id=pk, user_id=self.scope['user'].id, is_muted=is_muted)
        result = await self.add_participant(pk=pk)
        return result, 200

    @database_sync_to_async
    def add_participant(self, pk: int):
        BroadcastParticipantService.user_joined(pk, self.scope['user'].id)
        broadcast = Broadcast.objects.select_related('county', 'constituency', 'ward').get(pk=pk)
        BroadcastParticipantService.signal_broadcast(broadcast)
        return BroadcastSerializer(broadcast, context={'scope': self.scope}).data

    @action()
    @interaction_rate_limit
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await database_sync_to_async(BroadcastParticipantService.user_left)(pk, self.scope['user'].id)
        await self.broadcast_activity.unsubscribe(pk=pk, request_id=request_id)
        await self.speaker_request_activity.unsubscribe(pk=pk, request_id=request_id)
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)
        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)
        return {'pk': pk}, 200

    # ====================== Permission-Protected Actions ======================
    @action()
    @interaction_rate_limit
    async def patch(self, pk: int, **kwargs):  # Override to add explicit check
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)
        if broadcast.host_id != self.scope['user'].id:
            raise PermissionDenied("Only the host can update this broadcast.")
        return await super().patch(**kwargs)

    @action()
    @interaction_rate_limit
    async def delete(self, pk: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)
        if broadcast.host_id != self.scope['user'].id:
            raise PermissionDenied("Only the host can delete this broadcast.")
        await database_sync_to_async(BroadcastParticipantService.cleanup_broadcast)(pk)
        response, status = await super().delete(**kwargs)
        return response, status

    # ====================== MUTE ======================

    @action()
    @interaction_rate_limit
    async def mute(self, pk: int, data: dict, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_is_speaker(broadcast):
            raise PermissionDenied("You are not a speaker.")

        await database_sync_to_async(
            BroadcastParticipantService.set_mute_status
        )(broadcast_id=pk, user_id=self.scope['user'].id, is_muted=data['is_muted'])
        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)

        return {"is_muted": data['is_muted']}, 200

    @database_sync_to_async
    def _user_is_speaker(self, broadcast: Broadcast):
        user = self.scope['user']
        return broadcast.host_id == user.id or broadcast.co_hosts.contains(user) or broadcast.speakers.contains(user)

    @action()
    @interaction_rate_limit
    async def mute_speaker(self, pk: int, data: dict, **kwargs):
        """Host/Co-host mutes a participant"""
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only hosts can mute speakers.")

        target_user_id = data.get('user_id')
        if not target_user_id:
            return await self.reply(action='mute_speaker', errors=['user_id is required'], status=400)

        await database_sync_to_async(
            BroadcastParticipantService.set_mute_status
        )(broadcast_id=pk, user_id=target_user_id, is_muted=True)
        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)

        return {"user_id": target_user_id, "is_muted": True}, 200

    @action()
    @interaction_rate_limit
    async def mute_everyone(self, pk: int, **kwargs):
        """Host/Co-host mutes every speaker in broadcast"""
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only hosts can mute speakers.")

        await database_sync_to_async(BroadcastParticipantService.mute_everyone)(broadcast=broadcast,
                                                                              user_id=self.scope['user'].id)
        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)

        return {}, 200

    # ====================== SPEAKER REQUESTS ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def speaker_requests(self, pk: int, page_size=20, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)
        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only the host can view speaker requests.")

        data = await self.get_requests(broadcast=broadcast, page_size=page_size, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_requests(self, broadcast: Broadcast, page_size: int, **kwargs):
        queryset = broadcast.speaker_requests.filter(is_approved=None).exclude(id__in=kwargs.get('previous_requests', []))
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
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not self._user_can_access_broadcast(broadcast):
            raise PermissionDenied("Not authorized to speak in this broadcast.")

        data = await self._request_to_speak(user=user, broadcast=broadcast)

        return data, 200

    def _user_can_access_broadcast(self, broadcast: Broadcast) -> bool:
        user = self.scope['user']
        if not broadcast.county:
            return True
        if broadcast.county != user.county:
            return False
        if broadcast.constituency and broadcast.constituency != user.constituency:
            return False
        if broadcast.ward and broadcast.ward != user.ward:
            return False
        return True

    @database_sync_to_async
    def _request_to_speak(self, user, broadcast: Broadcast):
        request, created = SpeakerRequest.objects.get_or_create(
            broadcast=broadcast,
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

        if not await self._user_can_manage_speakers(broadcast=request.broadcast):
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
            request.broadcast.speakers.add(request.user)
        else:
            request.broadcast.speakers.remove(request.user)
        request.is_approved = is_approved
        request.save()
        return {"user_id": request.user.id, "is_approved": is_approved, "decided_by": self.scope['user'].id}

    @action()
    @interaction_rate_limit
    async def manage_co_host(self, pk: int, user_id: int, **kwargs):
        """Host adds and removes co-hosts"""
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not broadcast.host_id == self.scope['user'].id:
            raise PermissionDenied("Only the host can manage co-hosts.")

        if broadcast.host_id == user_id:
            raise ValidationError("Cannot add host to co-hosts.")

        data = await self._manage_co_host(pk=pk, user_id=user_id)
        return data, 200

    @database_sync_to_async
    def _manage_co_host(self, pk: int, user_id: int):
        broadcast: Broadcast = self.get_object(pk=pk)

        if broadcast.co_hosts.filter(pk=user_id).exists():
            broadcast.co_hosts.remove(user_id)
            is_co_host = False
        else:
            if not broadcast.speakers.filter(pk=user_id).exists():
                BroadcastParticipantService.set_mute_status(broadcast_id=pk, user_id=user_id, is_muted=True)
            broadcast.co_hosts.add(user_id)
            broadcast.speakers.remove(user_id)
            is_co_host = True
        BroadcastParticipantService.signal_broadcast(broadcast)
        return {'pk': pk, 'is_co_host': is_co_host}

    @action()
    @interaction_rate_limit
    async def manage_speaker(self, pk: int, user_id: int, **kwargs):
        """Host and co-hosts add and remove speakers"""
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only hosts can manage speaker requests.")

        if broadcast.host_id == user_id:
            raise ValidationError("Cannot add host to speakers.")

        data = await self._manage_speaker(pk=pk, user_id=user_id)
        return data, 200

    @database_sync_to_async
    def _manage_speaker(self, pk: int, user_id: int):
        broadcast: Broadcast = self.get_object(pk=pk)

        if broadcast.speakers.filter(pk=user_id).exists():
            broadcast.speakers.remove(user_id)
            is_speaker = False
        else:
            if not broadcast.co_hosts.filter(pk=user_id).exists():
                BroadcastParticipantService.set_mute_status(broadcast_id=pk, user_id=user_id, is_muted=True)
            broadcast.speakers.add(user_id)
            broadcast.co_hosts.remove(user_id)
            is_speaker = True
        BroadcastParticipantService.signal_broadcast(broadcast)
        return {'pk': pk, 'is_speaker': is_speaker}

    @database_sync_to_async
    def _user_can_manage_speakers(self, broadcast: Broadcast):
        user = self.scope['user']
        return (
                broadcast.host_id == user.id or
                broadcast.co_hosts.filter(id=user.id).exists()
        )

    @database_sync_to_async
    def delete_all_user_requests(self):
        return SpeakerRequest.objects.filter(user=self.scope['user']).delete()
