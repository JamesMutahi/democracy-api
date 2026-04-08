from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer

from apps.ballot.models import Ballot, Option, Reason
from apps.ballot.serializers import BallotSerializer
from apps.utils.list_paginator import list_paginator


class BallotConsumer(RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = BallotSerializer
    queryset = Ballot.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Observers ======================
    @model_observer(Ballot)
    async def ballot_activity(self, message, **kwargs):
        if message['action'] != 'delete':
            message['data'] = await self.get_ballot_serializer_data(pk=message['data'])
        await self.send_json(message)

    @database_sync_to_async
    def get_ballot_serializer_data(self, pk: int):
        ballot = Ballot.objects.select_related('county', 'constituency', 'ward').get(pk=pk)
        return BallotSerializer(ballot, context={'scope': self.scope}).data

    @ballot_activity.serializer
    def ballot_activity_serializer(self, instance: Ballot, action, **kwargs):
        return {
            'data': instance.pk,
            'action': action.value,
            'pk': instance.pk,
            'response_status': 201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        }

    @model_observer(Option, many_to_many=True)
    async def option_activity(self, message, **kwargs):
        # When an option changes, we send update for the parent ballot
        ballot_pk = message['data'].get('ballot') if isinstance(message['data'], dict) else message['data']
        if ballot_pk:
            message['data'] = await self.get_ballot_serializer_data(pk=ballot_pk)
            message['action'] = 'update'
        await self.send_json(message)

    @option_activity.serializer
    def option_activity_serializer(self, instance: Option, action, **kwargs):
        return {
            'data': instance.ballot.pk,
            'action': 'update',
            'pk': instance.pk,
            'response_status': 200,
        }

    async def disconnect(self, code):
        await self.ballot_activity.unsubscribe()
        await self.option_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Optimized Filter ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        """Optimized and cleaner filter_queryset for ballots"""
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        action = kwargs.get('action')

        if action != 'list':
            return queryset

        # === Core filters for list action ===
        search_term = kwargs.get('search_term')
        is_active = kwargs.get('is_active', True)
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

        # 1. Search (applied early - uses icontains)
        if search_term:
            queryset = queryset.filter(
                Q(title__icontains=search_term) |
                Q(description__icontains=search_term) |
                Q(county__name__icontains=search_term) |
                Q(constituency__name__icontains=search_term) |
                Q(ward__name__icontains=search_term)
            ).distinct()

        # 2. Active status filter
        if is_active is not None:
            queryset = queryset.filter(is_active=bool(is_active))

        # 3. Regional filtering (very important - optimized)
        if filter_by_region and (county or constituency or ward):
            region_filters = Q()
            if county:
                region_filters &= Q(county__isnull=True) | Q(county=county)
            if constituency:
                region_filters &= Q(constituency__isnull=True) | Q(constituency=constituency)
            if ward:
                region_filters &= Q(ward__isnull=True) | Q(ward=ward)
            queryset = queryset.filter(region_filters)

        # 4. Date range
        if start_date and end_date:
            queryset = queryset.filter(start_time__range=(start_date, end_date))

        # 5. Sorting (apply last)
        if sort_by == 'recent':
            queryset = queryset.order_by('-start_time')
        elif sort_by == 'oldest':
            queryset = queryset.order_by('start_time')

        return queryset

    # ====================== List Action ======================
    @action()
    async def list(self, request_id: str, page_size=None, **kwargs):
        # Get user's region asynchronously
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_regions()

        data = await self.list_(page_size=page_size or self.page_size, **kwargs)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def get_regions(self):
        user = self.scope['user']
        return user.county, user.constituency, user.ward

    @database_sync_to_async
    def list_(self, page_size: int, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)

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
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        if isinstance(response, dict):
            pk = response.get("id")
            if pk:
                await self.ballot_activity.subscribe(pk=pk, request_id=request_id)
                await self.option_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.ballot_activity.unsubscribe(pk=pk, request_id=request_id)
        await self.option_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    # ====================== Voting Actions ======================
    @action()
    async def vote(self, pk: int, **kwargs):
        result = await self.perform_vote(option_pk=pk)
        if isinstance(result, dict) and result.get('error'):
            return await self.reply(
                action='vote',
                errors=[result['error']],
                status=result.get('status', 403)
            )
        return result, 200

    @database_sync_to_async
    def perform_vote(self, option_pk: int):
        """Atomic vote: remove from other options + add to new one"""
        from django.db import transaction

        user = self.scope['user']

        try:
            with transaction.atomic():
                # Fetch option + ballot efficiently
                option = Option.objects.select_related('ballot').get(
                    pk=option_pk,
                    ballot__is_active=True
                )
                ballot = option.ballot

                # Regional permission check
                if not self._user_can_vote_in_ballot(user, ballot):
                    return {'error': 'You are not a registered voter in the region', 'status': 403}

                # === Critical Fix: Remove user from ALL other options in this ballot ===
                # This is the efficient way (avoids .update() on m2m)
                ballot.options.filter(votes=user).exclude(pk=option.pk).update()  # No, use remove via manager

                # Clear previous votes
                for other_option in ballot.options.filter(votes=user).exclude(pk=option.pk):
                    other_option.votes.remove(user)

                # Clear any existing reason
                Reason.objects.filter(ballot=ballot, user=user).delete()

                # Add vote to the chosen option
                option.votes.add(user)

                # Refresh and return updated ballot
                # ballot.refresh_from_db()
                return BallotSerializer(ballot, context={'scope': self.scope}).data

        except Option.DoesNotExist:
            return {'error': 'Option not found or ballot is inactive', 'status': 404}
        except Exception:
            return {'error': 'Failed to cast vote', 'status': 400}

    def _user_can_vote_in_ballot(self, user, ballot: Ballot) -> bool:
        """Fast region check"""
        if not ballot.county:
            return True  # National

        if ballot.county != user.county:
            return False
        if ballot.constituency and ballot.constituency != user.constituency:
            return False
        if ballot.ward and ballot.ward != user.ward:
            return False
        return True

    @action()
    async def add_reason(self, pk: int, text: str, **kwargs):
        result = await self.perform_add_reason(ballot_pk=pk, text=text)
        return result, 200

    @database_sync_to_async
    def perform_add_reason(self, ballot_pk: int, text: str):
        """Efficient reason handling"""
        user = self.scope['user']

        try:
            ballot = Ballot.objects.prefetch_related('options__votes').get(pk=ballot_pk, is_active=True)
        except Ballot.DoesNotExist:
            return {'error': 'Ballot not found or inactive', 'status': 404}

        # Check if user has already voted (efficient .exists())
        has_voted = ballot.options.filter(votes=user).exists()
        if not has_voted:
            return {'error': 'Please cast your vote first', 'status': 400}

        # Update or create reason in one query
        if len(text) == 0:
            Reason.objects.filter(ballot=ballot, user=user).delete()
        else:
            Reason.objects.update_or_create(
                ballot=ballot,
                user=user,
                defaults={'text': text}
            )

        # Return fresh ballot data
        return BallotSerializer(ballot, context={'scope': self.scope}).data
