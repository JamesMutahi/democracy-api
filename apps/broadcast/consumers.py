import logging
import uuid

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db import transaction, DatabaseError
from django.db.models import Q, QuerySet
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    PatchModelMixin,
    RetrieveModelMixin,
)
from djangochannelsrestframework.observer import model_observer
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound
from rest_framework.generics import get_object_or_404

from apps.broadcast.models import Broadcast, SpeakerRequest, SpeakerInvite, Comment
from apps.broadcast.querysets import annotate_broadcast_metrics
from apps.broadcast.serializers import BroadcastSerializer, SpeakerRequestSerializer, CommentSerializer
from apps.broadcast.services import BroadcastParticipantService
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import interaction_rate_limit, rate_limit

logger = logging.getLogger(__name__)
User = get_user_model()

MAX_PAGE_SIZE = getattr(settings, "BROADCAST_MAX_PAGE_SIZE", 100)
PREVIOUS_IDS_LIMIT = 200
SEARCH_TERM_LIMIT = 200


class BroadcastConsumer(
    CreateModelMixin,
    ListModelMixin,
    PatchModelMixin,
    RetrieveModelMixin,
    GenericAsyncAPIConsumer,
):
    serializer_class = BroadcastSerializer
    lookup_field = "pk"
    page_size = 20

    def get_queryset(self, **kwargs) -> QuerySet:
        return annotate_broadcast_metrics(
            Broadcast.objects.filter(is_active=True),
        )

    async def connect(self):
        user = self.scope.get("user")

        if user and user.is_authenticated:
            self.connection_id = getattr(self, "channel_name", None) or f"conn-{uuid.uuid4().hex}"

            await database_sync_to_async(BroadcastParticipantService.register_connection)(
                user.id,
                self.connection_id,
            )

            await self.accept()
        else:
            await self.close()

    # ====================== REALTIME OBSERVERS ======================

    @model_observer(Broadcast)
    async def broadcast_activity(self, message, **kwargs):
        await self.send_json(message)

    @broadcast_activity.groups_for_signal
    def broadcast_activity_signal_groups(self, instance: Broadcast, **kwargs):
        yield f"broadcast__{instance.pk}"

    @broadcast_activity.groups_for_consumer
    def broadcast_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"broadcast__{pk}"

    @broadcast_activity.serializer
    def broadcast_activity_serializer(self, instance: Broadcast, _action, **kwargs):
        return {
            "data": {} if _action == "delete" else get_activity_data(instance),
            "action": _action.value,
            "pk": instance.pk,
            "response_status": 200,
        }

    @model_observer(SpeakerInvite)
    async def speaker_invite_activity(self, message, **kwargs):
        await self.send_json(message)

    @speaker_invite_activity.groups_for_signal
    def speaker_invite_activity_signal_groups(self, instance: SpeakerRequest, **kwargs):
        yield f"broadcast__{instance.broadcast.pk}"

    @speaker_invite_activity.groups_for_consumer
    def speaker_invite_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"broadcast__{pk}"

    @speaker_invite_activity.serializer
    def speaker_invite_activity_serializer(self, instance: SpeakerInvite, _action, **kwargs):
        return {
            "data": get_activity_data(instance.broadcast),
            "action": "update",
            "pk": instance.broadcast_id,
            "response_status": 200,
        }

    @model_observer(SpeakerRequest)
    async def speaker_request_activity(self, message, **kwargs):
        await self.send_json(message)

    @speaker_request_activity.groups_for_signal
    def speaker_request_activity_signal_groups(self, instance: SpeakerRequest, **kwargs):
        yield f"broadcast_requests__{instance.broadcast.pk}"

    @speaker_request_activity.groups_for_consumer
    def speaker_request_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"broadcast_requests__{pk}"

    @speaker_request_activity.serializer
    def speaker_request_activity_serializer(self, instance: SpeakerRequest, _action, **kwargs):
        return {
            "data": {} if _action == "delete" else SpeakerRequestSerializer(
                instance,
                context={"scope": getattr(self, "scope", {})},
            ).data,
            "action": f"speaker_request_{_action.value}",
            "pk": instance.pk,
            "response_status": 200,
        }

    @model_observer(Comment)
    async def comment_activity(self, message, **kwargs):
        await self.send_json(message)

    @comment_activity.groups_for_signal
    def comment_activity_signal_groups(self, instance: Comment, **kwargs):
        yield f"broadcast_comments__{instance.broadcast.pk}"

    @comment_activity.groups_for_consumer
    def comment_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"broadcast_comments__{pk}"

    @comment_activity.serializer
    def comment_activity_serializer(self, instance: Comment, _action, **kwargs):
        return {
            "data": {} if _action == "delete" else CommentSerializer(
                instance,
                context={"scope": getattr(self, "scope", {})},
            ).data,
            "action": f"comment_{_action.value}",
            "pk": instance.pk,
            "response_status": 200,
        }

    async def websocket_disconnect(self, message):
        logger.info(f"Disconnect called for user {self.scope.get('user')}")

        try:
            user = self.scope.get("user")

            if user and user.is_authenticated:
                connection_id = getattr(self, "connection_id", None) or getattr(self, "channel_name", None)

                remaining_connections = await database_sync_to_async(
                    BroadcastParticipantService.cleanup_connection
                )(user.id, connection_id)

                # Only delete pending speaker requests when the user has no active connections.
                if remaining_connections == 0:
                    await self.delete_pending_user_requests()
        except Exception as e:
            logger.error(f"Error during disconnect cleanup: {e}", exc_info=True)

        try:
            await self.broadcast_activity.unsubscribe()
            await self.speaker_invite_activity.unsubscribe()
            await self.speaker_request_activity.unsubscribe()
            await self.comment_activity.unsubscribe()
        except Exception as e:
            logger.warning(f"Error unsubscribing observer: {e}")

        await super().websocket_disconnect(message)

    # ====================== Advanced Search ======================

    @staticmethod
    def _apply_advanced_search(queryset: QuerySet, search_term: str):
        """
        Apply advanced full-text search with ranking and fuzzy matching.
        Supports multilingual content (English, Swahili, etc.)
        """
        try:
            # Use 'simple' config for multilingual support
            search_query = SearchQuery(
                search_term,
                config="simple",
                search_type="websearch",
            )
        except DatabaseError:
            # Fallback to plain text search if websearch syntax is invalid
            search_query = SearchQuery(search_term, config="simple")

        queryset = queryset.annotate(
            rank=SearchRank("search_vector", search_query),
            title_sim=TrigramSimilarity("title", search_term),
            description_sim=TrigramSimilarity("description", search_term),
        ).filter(
            Q(search_vector=search_query)
            | Q(title_sim__gt=0.2)
            | Q(description_sim__gt=0.1)
        )

        # Order by relevance: exact matches first, then fuzzy matches
        ordering = ["-rank", "-title_sim", "-description_sim", "-created_at"]
        return queryset, ordering

    # ====================== FILTERING ======================

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        action_name = kwargs.get("action")
        previous_broadcasts = kwargs.get("previous_broadcasts")
        search_term = kwargs.get("search_term")
        is_open = kwargs.get("is_open", None)
        filter_by_region = kwargs.get("filter_by_region", True)
        sort_by = kwargs.get("sort_by", "recent")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")

        county = kwargs.get("county")
        constituency = kwargs.get("constituency")
        ward = kwargs.get("ward")

        if previous_broadcasts:
            queryset = queryset.exclude(id__in=previous_broadcasts)

        if search_term is not None:
            search_term = search_term.strip()

        if action_name == "list":
            queryset = queryset.filter(type=Broadcast.Type.MEETING)

            search_ordering = ["-created_at"]

            if search_term and len(search_term) >= 2:
                queryset, search_ordering = self._apply_advanced_search(
                    queryset, search_term
                )

            elif search_term:
                queryset = queryset.none()

            if is_open is not None:
                now = timezone.now()
                if is_open:
                    queryset = queryset.filter(Q(end_time__gt=now) | Q(end_time__isnull=True))
                else:
                    queryset = queryset.filter(end_time__lte=now)

            if filter_by_region:
                region_q = Q(county__isnull=True, constituency__isnull=True, ward__isnull=True)
                if county:
                    region_q |= Q(county=county, constituency__isnull=True, ward__isnull=True)
                if county and constituency:
                    region_q |= Q(county=county, constituency=constituency, ward__isnull=True)
                if county and constituency and ward:
                    region_q |= Q(county=county, constituency=constituency, ward=ward)
                queryset = queryset.filter(region_q)

            if start_date and end_date:
                queryset = queryset.filter(Q(start_time__lte=end_date) & Q(end_time__gte=start_date))

            if search_term and len(search_term) >= 2:
                return queryset.order_by(*search_ordering)

            if sort_by == "oldest":
                queryset = queryset.order_by("start_time")
            else:
                queryset = queryset.order_by("-start_time")

            return queryset

        if action_name == "user_broadcasts":
            return queryset.filter(host=kwargs.get("user"))

        if action_name in {"patch", "delete"}:
            return queryset.filter(host=self.scope["user"])

        return queryset

    # ====================== LIST / CREATE / RETRIEVE ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id: str, page_size=20, **kwargs):
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_regions()
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_regions(self):
        user = self.scope['user']
        return user.county, user.constituency, user.ward

    @action()
    @rate_limit(limit=40, period=60)
    async def user_broadcasts(self, request_id: str, page_size=None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=10, period=60)
    async def create(self, request_id: str = None, **kwargs):
        return await super().create(**kwargs)

    @action()
    @rate_limit(limit=60, period=60)
    async def retrieve(self, request_id: str, **kwargs):
        return await super().retrieve(**kwargs)

    @database_sync_to_async
    def get_user_regions(self):
        user = self.scope["user"]
        return user.county, user.constituency, user.ward

    @database_sync_to_async
    def list_(self, queryset, page_size: int, **kwargs):
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)
        broadcasts = list(page_obj.object_list)
        broadcast_ids = [broadcast.id for broadcast in broadcasts]

        participant_counts = BroadcastParticipantService.get_participant_counts(broadcast_ids)

        serializer = BroadcastSerializer(
            broadcasts,
            many=True,
            context={
                "scope": self.scope,
                "participant_counts": participant_counts,
            },
        )

        return {
            "results": serializer.data,
            "previous_broadcasts": kwargs.get("previous_broadcasts", []),
            "has_next": page_obj.has_next(),
        }

    # ====================== PERMISSION HELPERS ======================
    @database_sync_to_async
    def _user_is_host(self, broadcast: Broadcast) -> bool:
        user = self.scope["user"]

        return broadcast.host_id == user.id

    @database_sync_to_async
    def _broadcast_is_joinable(self, broadcast: Broadcast) -> bool:
        if not broadcast.is_active:
            return False

        if broadcast.end_time and broadcast.end_time <= timezone.now():
            return False

        return True

    @database_sync_to_async
    def _user_can_manage_speakers(self, broadcast: Broadcast) -> bool:
        user = self.scope["user"]

        return (
                broadcast.host_id == user.id or
                broadcast.co_hosts.filter(id=user.id).exists()
        )

    @database_sync_to_async
    def _user_is_speaker(self, broadcast: Broadcast) -> bool:
        user = self.scope["user"]

        return (
                broadcast.host_id == user.id or
                broadcast.co_hosts.filter(id=user.id).exists() or
                broadcast.speakers.filter(id=user.id).exists()
        )

    @database_sync_to_async
    def _user_can_access_broadcast(self, broadcast: Broadcast) -> bool:
        user = self.scope["user"]

        if not broadcast.county_id:
            return True

        if user.county_id != broadcast.county_id:
            return False

        if broadcast.constituency_id and user.constituency_id != broadcast.constituency_id:
            return False

        if broadcast.ward_id and user.ward_id != broadcast.ward_id:
            return False

        return True

    @database_sync_to_async
    def _target_is_speaker_or_co_host(self, broadcast: Broadcast, target_user_id: int) -> bool:
        return (
                broadcast.host_id == target_user_id or
                broadcast.co_hosts.filter(id=target_user_id).exists() or
                broadcast.speakers.filter(id=target_user_id).exists()
        )

    @database_sync_to_async
    def _get_active_user(self, user_id: int):
        return User.objects.filter(id=user_id, is_active=True).first()

    # ====================== JOIN / LEAVE ======================

    @action()
    @interaction_rate_limit
    async def subscribe(self, pk: int, request_id: str, is_muted: bool = False, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._broadcast_is_joinable(broadcast):
            raise ValidationError("This broadcast cannot be joined.")

        user_id = self.scope["user"].id

        await self.broadcast_activity.subscribe(pk=pk, request_id=user_id)
        await self.speaker_invite_activity.subscribe(pk=pk, request_id=user_id)
        await self.comment_activity.subscribe(pk=pk, request_id=user_id)

        if await self._user_can_manage_speakers(broadcast=broadcast):
            await self.speaker_request_activity.subscribe(pk=pk, request_id=user_id)

        if is_muted:
            await database_sync_to_async(BroadcastParticipantService.set_mute_status)(
                broadcast_id=pk,
                user_id=self.scope["user"].id,
                is_muted=True,
                muted_by=BroadcastParticipantService.MUTE_SELF,
            )

        result = await self.add_participant(pk=pk)
        return result, 200

    @database_sync_to_async
    def add_participant(self, pk: int):
        BroadcastParticipantService.connection_joined_broadcast(
            broadcast_id=pk,
            user_id=self.scope["user"].id,
            connection_id=getattr(self, "connection_id", "unknown"),
        )

        broadcast = get_object_or_404(self.get_queryset(), pk=pk)
        BroadcastParticipantService.signal_broadcast(broadcast)

        return BroadcastSerializer(broadcast, context={"scope": self.scope}).data

    @action()
    @interaction_rate_limit
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        user_id = self.scope["user"].id

        await database_sync_to_async(BroadcastParticipantService.connection_left_broadcast)(
            broadcast_id=pk,
            user_id=user_id,
            connection_id=getattr(self, "connection_id", "unknown"),
        )

        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)

        await self.broadcast_activity.unsubscribe(pk=pk, request_id=user_id)
        await self.speaker_invite_activity.unsubscribe(pk=pk, request_id=user_id)
        await self.speaker_request_activity.unsubscribe(pk=pk, request_id=user_id)
        await self.comment_activity.unsubscribe(pk=pk, request_id=user_id)

        return {"pk": pk}, 200

    # ====================== PATCH / DELETE ======================

    @action()
    @interaction_rate_limit
    async def patch(self, pk: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if broadcast.host_id != self.scope["user"].id:
            raise PermissionDenied("Only the host can update this broadcast.")

        return await super().patch(pk=pk, **kwargs)

    @action()
    @interaction_rate_limit
    async def delete(self, pk: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if broadcast.host_id != self.scope["user"].id:
            raise PermissionDenied("Only the host can delete this broadcast.")

        response, status = await super().delete(pk=pk, **kwargs)

        await database_sync_to_async(BroadcastParticipantService.cleanup_broadcast)(pk)

        return response, status

    # ====================== MUTE ======================

    @action()
    @interaction_rate_limit
    async def mute(self, pk: int, data: dict, **kwargs):
        if not isinstance(data, dict) or "is_muted" not in data:
            raise ValidationError({"is_muted": "This field is required"})

        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_is_speaker(broadcast):
            raise PermissionDenied("You are not a speaker.")

        is_muted = data.get("is_muted")
        user_id = self.scope["user"].id

        if not is_muted:
            mute_reason = await database_sync_to_async(
                BroadcastParticipantService.get_mute_reason
            )(pk, user_id)

            if mute_reason == BroadcastParticipantService.MUTE_HOST:
                if not await self._user_can_manage_speakers(broadcast):
                    raise PermissionDenied("You were muted by the host and cannot unmute yourself.")

        await database_sync_to_async(BroadcastParticipantService.set_mute_status)(
            broadcast_id=pk,
            user_id=user_id,
            is_muted=is_muted,
            muted_by=BroadcastParticipantService.MUTE_SELF if is_muted else None,
        )

        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)

        return {"is_muted": is_muted}, 200

    @action()
    @interaction_rate_limit
    async def mute_speaker(self, pk: int, data: dict, **kwargs):
        if not isinstance(data, dict):
            raise ValidationError("data must be an object.")

        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only hosts or co-hosts can mute speakers.")

        target_user_id = data.get("user_id")

        if not target_user_id:
            raise ValidationError({"user_id": "This field is required"})

        try:
            target_user_id = int(target_user_id)
        except Exception:
            raise ValidationError("user_id must be an integer.")

        if not await self._target_is_speaker_or_co_host(broadcast, target_user_id):
            raise ValidationError("Target user is not a speaker, co-host, or host.")

        is_muted = data.get("is_muted", True)

        await database_sync_to_async(BroadcastParticipantService.set_mute_status)(
            broadcast_id=pk,
            user_id=target_user_id,
            is_muted=is_muted,
            muted_by=BroadcastParticipantService.MUTE_HOST,
        )

        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)

        return {"user_id": target_user_id, "is_muted": is_muted}, 200

    @action()
    @interaction_rate_limit
    async def mute_everyone(self, pk: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only hosts or co-hosts can mute everyone.")

        await database_sync_to_async(BroadcastParticipantService.mute_everyone)(
            broadcast=broadcast,
            user_id=self.scope["user"].id,
        )

        await database_sync_to_async(BroadcastParticipantService.signal_broadcast)(broadcast=broadcast)

        return {}, 200

    # ====================== SPEAKER INVITES ======================

    @action()
    @interaction_rate_limit
    async def invite_to_speak(self, broadcast_id: int, user_id: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=broadcast_id)

        if not await self._user_can_manage_speakers(broadcast):
            raise PermissionDenied("Unauthorized.")

        user = await self._get_user(pk=user_id)

        data = await self._invite_to_speak(user=user, broadcast=broadcast)
        return data, 200

    @database_sync_to_async
    @transaction.atomic
    def _invite_to_speak(self, user, broadcast: Broadcast):
        BroadcastParticipantService.ensure_can_add_speaker(broadcast)
        invite_obj, created = SpeakerInvite.objects.get_or_create(
            broadcast=broadcast,
            user=user,
            defaults={"role": SpeakerInvite.Role.SPEAKER, "is_accepted": None},
        )

        if not created and invite_obj.is_accepted is not None:
            invite_obj.role = SpeakerInvite.Role.SPEAKER
            invite_obj.is_accepted = None
            invite_obj.save()

        return {
            "invite_id": invite_obj.id,
            "broadcast_id": invite_obj.broadcast_id,
            "user_id": invite_obj.user_id,
        }

    @action()
    @interaction_rate_limit
    async def invite_to_co_host(self, broadcast_id: int, user_id: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=broadcast_id)

        if not await self._user_is_host(broadcast):
            raise PermissionDenied("You are not the host.")

        user = await self._get_user(pk=user_id)

        data = await self._invite_to_co_host(user=user, broadcast=broadcast)
        return data, 200

    @database_sync_to_async
    @transaction.atomic
    def _invite_to_co_host(self, user, broadcast: Broadcast):
        BroadcastParticipantService.ensure_can_add_speaker(broadcast)
        invite_obj, created = SpeakerInvite.objects.get_or_create(
            broadcast=broadcast,
            user=user,
            defaults={"role": SpeakerInvite.Role.CO_HOST, "is_accepted": None},
        )

        if not created and invite_obj.is_accepted is not None:
            invite_obj.role = SpeakerInvite.Role.CO_HOST
            invite_obj.is_accepted = None
            invite_obj.save()

        return {
            "invite_id": invite_obj.id,
            "broadcast_id": invite_obj.broadcast_id,
            "user_id": invite_obj.user_id,
        }

    @staticmethod
    @database_sync_to_async
    def _get_user(pk: int) -> User:
        return get_object_or_404(
            User.objects.filter(is_active=True),
            pk=pk,
        )

    @action()
    @interaction_rate_limit
    async def cancel_invite(self, pk: int, **kwargs):
        invite_obj = await self.get_speaker_invite(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=invite_obj.broadcast):
            raise PermissionDenied("You are not authorized to cancel this invite.")

        await database_sync_to_async(invite_obj.delete)()

        return {"pk": pk}, 200

    @action()
    @interaction_rate_limit
    async def handle_speaker_invite(self, pk: int, data: dict, **kwargs):
        if not isinstance(data, dict) or "is_accepted" not in data:
            raise ValidationError({"is_accepted": "This field is required."})

        is_accepted = data.get("is_accepted")

        invite_obj = await self.get_speaker_invite(pk=pk)

        if not await self._is_the_invited_speaker(invite=invite_obj):
            raise PermissionDenied("This is not your invite.")

        result = await self._handle_speaker_invite(
            invite_obj=invite_obj,
            is_accepted=is_accepted,
        )

        return result, 200

    @staticmethod
    @database_sync_to_async
    def get_speaker_invite(pk: int) -> SpeakerRequest:
        return get_object_or_404(
            SpeakerInvite.objects.select_related("broadcast", "user"),
            pk=pk,
        )

    @database_sync_to_async
    def _is_the_invited_speaker(self, invite: SpeakerInvite) -> bool:
        user = self.scope["user"]

        return invite.user_id == user.id

    @database_sync_to_async
    @transaction.atomic
    def _handle_speaker_invite(self, invite_obj: SpeakerInvite, is_accepted: bool):
        broadcast = invite_obj.broadcast

        invite_obj.is_accepted = is_accepted

        if is_accepted:
            BroadcastParticipantService.ensure_can_add_speaker(broadcast)
            if invite_obj.role == SpeakerInvite.Role.CO_HOST:
                broadcast.speakers.remove(self.scope["user"])
                broadcast.co_hosts.add(self.scope["user"])
            else:
                broadcast.speakers.add(self.scope["user"])
                BroadcastParticipantService.set_mute_status(
                    broadcast_id=broadcast.id,
                    user_id=invite_obj.user_id,
                    is_muted=True,
                    muted_by=BroadcastParticipantService.MUTE_SELF,
                )

        invite_obj.save()

        transaction.on_commit(
            lambda: BroadcastParticipantService.signal_broadcast(broadcast)
        )

        return {
            "invite_id": invite_obj.pk,
            "is_accepted": is_accepted,
        }

    # ====================== SPEAKER REQUESTS ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def speaker_requests(self, pk: int, page_size=20, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only the host or co-host can view speaker requests.")

        data = await self.get_requests(broadcast=broadcast,page_size=page_size,**kwargs)

        return data, 200

    @database_sync_to_async
    def get_requests(self, broadcast: Broadcast, page_size: int, **kwargs):
        previous_requests = kwargs.get("previous_requests", [])

        queryset = (
            broadcast.speaker_requests.filter(is_approved=None)
            .exclude(id__in=previous_requests)
            .select_related("user", "decided_by")
        )

        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = SpeakerRequestSerializer(
            page_obj.object_list,
            many=True,
            context={"scope": self.scope},
        )

        return {
            "results": serializer.data,
            "previous_requests": previous_requests,
            "has_next": page_obj.has_next(),
        }

    @action()
    @interaction_rate_limit
    async def request_to_speak(self, pk: int, **kwargs):
        user = self.scope["user"]
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._broadcast_is_joinable(broadcast):
            raise ValidationError("This broadcast cannot be joined.")

        if await self._user_is_speaker(broadcast):
            raise ValidationError("You are already a speaker, co-host, or host.")

        if not await self._user_can_access_broadcast(broadcast):
            raise PermissionDenied("Not authorized to speak in this broadcast.")

        data = await self._request_to_speak(user=user, broadcast=broadcast)
        return data, 200

    @database_sync_to_async
    @transaction.atomic
    def _request_to_speak(self, user, broadcast: Broadcast):
        request_obj, created = SpeakerRequest.objects.get_or_create(
            broadcast=broadcast,
            user=user,
            defaults={"is_approved": None},
        )

        if not created and request_obj.is_approved is not None:
            request_obj.is_approved = None
            request_obj.decided_by = None
            request_obj.save()

        return {"request_id": request_obj.id}

    @action()
    @interaction_rate_limit
    async def handle_speaker_request(self, pk: int, data: dict, **kwargs):
        if not isinstance(data, dict) or "is_approved" not in data:
            raise ValidationError({"is_approved": "This field is required."})

        is_approved = data.get("is_approved")

        request_obj = await self.get_speaker_request(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=request_obj.broadcast):
            raise PermissionDenied("Only hosts or co-hosts can manage speaker requests.")

        result = await self._handle_speaker_request(
            request_obj=request_obj,
            is_approved=is_approved,
        )

        return result, 200

    @staticmethod
    @database_sync_to_async
    def get_speaker_request(pk: int) -> SpeakerRequest:
        return get_object_or_404(
            SpeakerRequest.objects.select_related("broadcast", "user", "decided_by"),
            pk=pk,
        )

    @database_sync_to_async
    @transaction.atomic
    def _handle_speaker_request(self, request_obj: SpeakerRequest, is_approved: bool):
        broadcast = request_obj.broadcast

        request_obj.is_approved = is_approved
        request_obj.decided_by = self.scope["user"]

        if is_approved:
            BroadcastParticipantService.ensure_can_add_speaker(broadcast)

            broadcast.co_hosts.remove(request_obj.user)
            broadcast.speakers.add(request_obj.user)

            BroadcastParticipantService.set_mute_status(
                broadcast_id=broadcast.id,
                user_id=request_obj.user.id,
                is_muted=True,
                muted_by=BroadcastParticipantService.MUTE_SELF,
            )
        else:
            broadcast.speakers.remove(request_obj.user)

            BroadcastParticipantService.set_mute_status(
                broadcast_id=broadcast.id,
                user_id=request_obj.user.id,
                is_muted=False,
            )

        request_obj.save()

        transaction.on_commit(
            lambda: BroadcastParticipantService.signal_broadcast(broadcast)
        )

        return {
            "user_id": request_obj.user.id,
            "is_approved": is_approved,
            "decided_by": self.scope["user"].id,
        }

    # ====================== CO-HOST / SPEAKER MANAGEMENT ======================

    @action()
    @interaction_rate_limit
    async def remove_co_host(self, pk: int, user_id: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if broadcast.host_id != self.scope["user"].id:
            raise PermissionDenied("Only the host can remove co-hosts.")

        target_user = await self._get_active_user(user_id)
        if not target_user:
            raise ValidationError("User not found.")

        data = await self._remove_co_host(broadcast=broadcast, user_id=target_user.id)

        await self.speaker_request_activity.unsubscribe(pk=pk, request_id=user_id)

        return data, 200

    @database_sync_to_async
    @transaction.atomic
    def _remove_co_host(self, broadcast: Broadcast, user_id: int):
        broadcast.co_hosts.remove(user_id)
        BroadcastParticipantService.set_mute_status(
            broadcast_id=broadcast.pk,
            user_id=user_id,
            is_muted=False,
        )
        BroadcastParticipantService.signal_broadcast(broadcast)
        return {"user_id": user_id, "broadcast_id": broadcast.pk}

    @action()
    @interaction_rate_limit
    async def remove_speaker(self, pk: int, user_id: int, **kwargs):
        broadcast = await database_sync_to_async(self.get_object)(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=broadcast):
            raise PermissionDenied("Only hosts or co-hosts can remove speakers.")

        target_user = await self._get_active_user(user_id)
        if not target_user:
            raise ValidationError("User not found.")

        data = await self._remove_speaker(broadcast=broadcast, user_id=target_user.id)
        return data, 200

    @database_sync_to_async
    @transaction.atomic
    def _remove_speaker(self, broadcast: Broadcast, user_id: int):
        broadcast.speakers.remove(user_id)
        BroadcastParticipantService.set_mute_status(
            broadcast_id=broadcast.pk,
            user_id=user_id,
            is_muted=False,
        )
        BroadcastParticipantService.signal_broadcast(broadcast)
        return {"user_id": user_id, "broadcast_id": broadcast.pk}

    # ====================== COMMENTS ======================

    @action()
    @rate_limit(limit=40, period=60)
    def comments(self, pk: int, oldest_comment_id: int = None, newest_comment_id: int = None, **kwargs):
        try:
            broadcast = Broadcast.objects.select_related("host").prefetch_related("comments").get(pk=pk)
        except Broadcast.DoesNotExist:
            raise NotFound("Broadcast not found.")

        queryset = broadcast.comments.all()

        if oldest_comment_id:
            # Fetch newer comments (polling)
            queryset = queryset.filter(id__lt=oldest_comment_id)
        elif newest_comment_id:
            # Fetch older comments (scrolling up)
            queryset = queryset.filter(id__gt=newest_comment_id)
        else:
            # Fetch latest comments
            queryset = queryset.all()

        from apps.utils.list_paginator import list_paginator

        page_obj = list_paginator(queryset=queryset, page=1, page_size=20)

        serializer = CommentSerializer(
            page_obj.object_list,
            many=True,
            context={"scope": self.scope}
        )

        return {
            "results": serializer.data,
            "broadcast_id": pk,
            "oldest_comment_id": oldest_comment_id,
            "newest_comment_id": newest_comment_id,
            "has_next": page_obj.has_next(),
        }, 200

    @action()
    @interaction_rate_limit
    def create_comment(self, **kwargs):
        serializer = CommentSerializer(data=kwargs['data'], context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data, 201

    @action()
    @interaction_rate_limit
    async def delete_comment(self, pk: int, **kwargs):
        comment = await self.get_comment(pk=pk)

        if not await self._user_can_manage_speakers(broadcast=comment.broadcast):
            raise PermissionDenied("Only hosts and co-hosts can delete comments.")

        if not await self._broadcast_is_joinable(comment.broadcast):
            raise ValidationError("This broadcast is not active or has ended.")

        await self._delete_comment(comment=comment)

        return {"pk": pk}, 204

    @database_sync_to_async
    def get_comment(self, pk: int):
        try:
            return Comment.objects.select_related("broadcast").get(pk=pk)
        except Comment.DoesNotExist:
            raise NotFound("Comment not found.")

    @database_sync_to_async
    def _delete_comment(self, comment: Comment):
        broadcast = comment.broadcast
        comment.delete()
        BroadcastParticipantService.signal_broadcast(broadcast)

    # ====================== CLEANUP ======================

    @database_sync_to_async
    def delete_pending_user_requests(self):
        return SpeakerRequest.objects.filter(
            user=self.scope["user"],
            is_approved=None,
        ).delete()


# ── Module-level helpers for observer payloads ────────────────

def get_activity_data(instance: Broadcast) -> dict:
    try:
        broadcast = Broadcast.objects.select_related(
            "host",
            "county",
            "constituency",
            "ward",
        ).prefetch_related(
            "speaker_invites",
            "co_hosts",
            "speakers",
            "recording_sessions",
        ).get(pk=instance.pk)
    except Broadcast.DoesNotExist:
        broadcast = instance

    data = BroadcastSerializer(
        broadcast,
        context={"scope": {"user": broadcast.host}},
    ).data

    return data
