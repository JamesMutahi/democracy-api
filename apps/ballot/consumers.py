from channels.db import database_sync_to_async
from django.db import transaction
from django.db.models import QuerySet, Q, Count
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError

from apps.ballot.models import Ballot, Option, Reason, BallotVote
from apps.ballot.querysets import annotate_ballot_metrics
from apps.ballot.serializers import BallotSerializer, OptionSerializer
from apps.ballot.tasks import redact_reason
from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit


class BallotConsumer(RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = BallotSerializer
    lookup_field = "pk"
    page_size = 20

    def get_queryset(self, **kwargs) -> QuerySet:
        return annotate_ballot_metrics(
            Ballot.objects.filter(is_active=True),
            self.scope.get("user"),
        )

    # ── connection ──────────────────────────────────────────────
    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Observers ======================
    @model_observer(Ballot)
    async def ballot_activity(self, message, **kwargs):
        await self.send_json(message)

    @ballot_activity.groups_for_signal
    def ballot_activity_signal_groups(self, instance: Ballot, **kwargs):
        yield f'ballot__{instance.pk}'

    @ballot_activity.groups_for_consumer
    def ballot_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'ballot__{pk}'

    @ballot_activity.serializer
    def ballot_activity_serializer(self, instance: Ballot, action, **kwargs):
        return {
            'data': get_activity_data(instance),
            'action': action.value,
            'pk': instance.pk,
            'response_status': 201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        }

    @model_observer(Option, many_to_many=True)
    async def option_activity(self, message, **kwargs):
        await self.send_json(message)

    @option_activity.groups_for_signal
    def option_activity_signal_groups(self, instance: Option, **kwargs):
        yield f'ballot__{instance.ballot_id}'

    @option_activity.groups_for_consumer
    def option_activity_consumer_groups(self, ballot=None, **kwargs):
        if ballot is not None:
            yield f'ballot__{ballot}'

    @option_activity.serializer
    def option_activity_serializer(self, instance: Option, action, **kwargs):
        return {
            'data': get_activity_data_for_ballot_id(instance.ballot_id),
            'action': 'update',
            'pk': instance.ballot.pk,
            'response_status': 200,
        }

    @model_observer(BallotVote)
    async def vote_activity(self, message, **kwargs):
        await self.send_json(message)

    @vote_activity.groups_for_signal
    def vote_activity_signal_groups(self, instance: BallotVote, **kwargs):
        yield f"ballot__{instance.ballot_id}"

    @vote_activity.groups_for_consumer
    def vote_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"ballot__{pk}"

    @vote_activity.serializer
    def vote_activity_serializer(self, instance: BallotVote, action, **kwargs):
        return {
            "data": get_activity_data_for_ballot_id(instance.ballot_id),
            "action": "update",
            "pk": instance.ballot_id,
            "response_status": 200,
        }

    async def disconnect(self, code):
        await self.ballot_activity.unsubscribe()
        await self.option_activity.unsubscribe()
        await self.vote_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Filter ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        action_ = kwargs.get('action')

        if action_ != 'list':
            return queryset

        # === Core filters for list action ===
        search_term = kwargs.get('search_term')
        is_open = kwargs.get('is_open', None)
        filter_by_region = kwargs.get('filter_by_region', True)
        sort_by = kwargs.get('sort_by', 'recent')
        start_date = kwargs.get('start_date')
        end_date = kwargs.get('end_date')
        county = kwargs.get('county')
        constituency = kwargs.get('constituency')
        ward = kwargs.get('ward')

        previous_ballots = kwargs.get('previous_ballots', None)
        if previous_ballots:
            queryset = queryset.exclude(id__in=previous_ballots)

        # Search (applied early - uses icontains)
        if search_term:
            queryset = queryset.filter(
                Q(title__icontains=search_term) |
                Q(description__icontains=search_term) |
                Q(county__name__icontains=search_term) |
                Q(constituency__name__icontains=search_term) |
                Q(ward__name__icontains=search_term)
            ).distinct()

        # Open status
        if is_open is not None:
            now = timezone.now()
            if is_open:
                # Open if end_time is in the future OR if there is no end_time at all
                queryset = queryset.filter(Q(end_time__gt=now))
            else:
                # Closed if end_date is in the past (ignores null values)
                queryset = queryset.filter(end_time__lte=now)

        # Regional filtering
        if filter_by_region:
            # Always allow global objects (where all region fields are null)
            region_q = Q(county__isnull=True, constituency__isnull=True, ward__isnull=True)

            # Strict inclusion rules based on what the user actually belongs to
            if county:
                region_q |= Q(county=county, constituency__isnull=True, ward__isnull=True)

            if constituency:
                region_q |= Q(county=county, constituency=constituency, ward__isnull=True)

            if ward:
                region_q |= Q(county=county, constituency=constituency, ward=ward)

            queryset = queryset.filter(region_q)

        # Date range
        if start_date and end_date:
            queryset = queryset.filter(Q(start_time__lte=end_date) & Q(end_time__gte=start_date))

        # Sorting (applied last)
        if sort_by == 'recent':
            queryset = queryset.order_by('-start_time', '-id')
        elif sort_by == 'oldest':
            queryset = queryset.order_by('start_time', 'id')
        else:
            queryset = queryset.order_by('-start_time', '-id')

        return queryset

    # ====================== List Action ======================
    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id: str, page_size=None, **kwargs):
        # Get user's region asynchronously
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_regions()
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size or self.page_size, **kwargs)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def get_regions(self):
        user = self.scope['user']
        return user.county, user.constituency, user.ward

    @database_sync_to_async
    def list_(self, queryset: QuerySet, page_size: int, **kwargs):
        page_obj = list_paginator(
            queryset=queryset,
            page=1,
            page_size=page_size
        )

        serializer = BallotSerializer(
            page_obj.object_list,
            many=True,
            context={'scope': self.scope}
        )

        return {
            'results': serializer.data,
            'previous_ballots': kwargs.get('previous_ballots'),
            'has_next': page_obj.has_next()
        }

    # ====================== Retrieve & Subscription ======================
    @action()
    @rate_limit(limit=40, period=60)
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        pk = response.get("id")
        if pk:
            await self.ballot_activity.subscribe(pk=pk, request_id=request_id)
            await self.option_activity.subscribe(pk=pk, request_id=request_id)
            await self.vote_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    @interaction_rate_limit
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.ballot_activity.unsubscribe(pk=pk, request_id=request_id)
        await self.option_activity.unsubscribe(pk=pk, request_id=request_id)
        await self.vote_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    # ====================== Voting Actions ======================
    @action()
    @interaction_rate_limit
    async def vote(self, pk: int, **kwargs):
        result = await self.perform_vote(option_pk=pk)
        return result, 200

    @database_sync_to_async
    def perform_vote(self, option_pk: int):
        user = self.scope["user"]

        try:
            with transaction.atomic():
                option = (
                    Option.objects.select_related("ballot")
                    .get(pk=option_pk, ballot__is_active=True)
                )

                ballot = option.ballot

                self._user_can_vote_in_ballot(user, ballot)

                vote, created = BallotVote.objects.get_or_create(
                    user=user,
                    ballot=ballot,
                    defaults={
                        "option": option,
                        "voted_at": timezone.now(),
                    },
                )

                # First time voting in this ballot.
                if created:
                    return {
                        "ballot_id": ballot.pk,
                        "option_id": option.pk,
                        "has_changed": False,
                        "already_voted": False,
                    }

                # Voter clicked the same option again.
                # Do not delete the reason.
                if vote.option_id == option.pk:
                    return {
                        "ballot_id": ballot.pk,
                        "option_id": option.pk,
                        "has_changed": False,
                        "already_voted": True,
                    }

                # Voter is changing option.
                # The previous reason no longer applies.
                Reason.objects.filter(ballot=ballot, user=user).delete()

                vote.option = option
                vote.voted_at = timezone.now()
                vote.save(update_fields=["option", "voted_at"])

                return {
                    "ballot_id": ballot.pk,
                    "option_id": option.pk,
                    "has_changed": True,
                    "already_voted": True,
                }

        except Option.DoesNotExist:
            raise NotFound("Option not found")

    @staticmethod
    def _user_can_vote_in_ballot(user, ballot: Ballot):
        if not ballot.is_active:
            raise PermissionDenied("This ballot is not active.")

        voting_time = timezone.now()

        # Start time check
        if voting_time < ballot.start_time:
            raise PermissionDenied('Voting has not started yet')

        # End time check
        if ballot.end_time and ballot.end_time < voting_time:
            raise PermissionDenied('Voting has ended')

        # Region check
        if not ballot.county:
            return True  # National

        if ballot.county != user.county:
            raise PermissionDenied(f'You are not a registered voter in {ballot.county.name} county')
        if ballot.constituency and ballot.constituency != user.constituency:
            raise PermissionDenied(f'You are not a registered voter in {ballot.constituency.name} constituency')
        if ballot.ward and ballot.ward != user.ward:
            raise PermissionDenied(f'You are not a registered voter in {ballot.ward.name} ward')
        return True

    @action()
    @interaction_rate_limit
    async def add_reason(self, pk: int, text: str, **kwargs):
        result = await self.perform_add_reason(ballot_pk=pk, text=text)
        return result, 200

    @database_sync_to_async
    def perform_add_reason(self, ballot_pk: int, text: str):
        user = self.scope["user"]

        try:
            ballot = Ballot.objects.get(pk=ballot_pk, is_active=True)
        except Ballot.DoesNotExist:
            raise NotFound("Ballot not found or inactive")

        self._user_can_vote_in_ballot(user, ballot)

        has_voted = BallotVote.objects.filter(
            user=user,
            ballot=ballot,
        ).exists()

        if not has_voted:
            raise ValidationError("Please cast your vote first.")

        text = (text or "").strip()
        reason = None

        current_vote = BallotVote.objects.filter(user=user, ballot=ballot).first()
        current_option = current_vote.option if current_vote else None

        with transaction.atomic():
            if not text:
                Reason.objects.filter(ballot=ballot, user=user).delete()
            else:
                reason, _ = Reason.objects.update_or_create(
                    ballot=ballot,
                    user=user,
                    defaults={"text": text, "option": current_option},
                )
            transaction.on_commit(
                lambda reason_id=reason.id: redact_reason.delay(reason_id)
            )

        return {
            "ballot_id": ballot.pk,
            "reason": reason.text if reason is not None else None,
        }


# ── Module-level helpers for observer payloads ────────────────

def get_activity_data_for_ballot_id(ballot_id: int) -> dict:
    ballot = (
        Ballot.objects.select_related("county", "constituency", "ward")
        .filter(pk=ballot_id)
        .first()
    )

    if ballot is None:
        return {"id": ballot_id}

    return get_activity_data(ballot)


def get_activity_data(ballot: Ballot) -> dict:
    now = timezone.now()

    total_votes = getattr(ballot, "total_votes", None)
    if total_votes is None:
        total_votes = ballot.votes.count()

    options = (
        ballot.options.annotate(
            vote_count=Count("votes", distinct=True),
        ).order_by("number", "id")
    )

    return {
        "id": ballot.pk,
        "title": ballot.title,
        "description": ballot.description,
        "county": CountySerializer(ballot.county).data if ballot.county else None,
        "constituency": (
            ConstituencySerializer(ballot.constituency).data
            if ballot.constituency
            else None
        ),
        "ward": WardSerializer(ballot.ward).data if ballot.ward else None,
        "start_time": ballot.start_time.isoformat(),
        "end_time": ballot.end_time.isoformat(),
        "has_started": now >= ballot.start_time,
        "has_ended": ballot.end_time < now,
        "total_votes": total_votes,
        "options": OptionSerializer(options, many=True).data,
        "is_active": ballot.is_active,
    }
