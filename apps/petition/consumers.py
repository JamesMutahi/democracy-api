from channels.db import database_sync_to_async
from django.db import transaction
from django.db.models import QuerySet, Q, Count
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin, CreateModelMixin, RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer

from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.utils.list_paginator import list_paginator


class PetitionConsumer(ListModelMixin, CreateModelMixin, RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = PetitionSerializer
    queryset = Petition.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Real-time Observer ======================
    @model_observer(Petition, many_to_many=True)
    async def petition_activity(self, message, **kwargs):
        if message['action'] != 'delete':
            message['data'] = await self.get_petition_serializer_data(pk=message['data'])
        await self.send_json(message)

    @database_sync_to_async
    def get_petition_serializer_data(self, pk: int):
        petition = Petition.objects.select_related('author', 'county', 'constituency', 'ward').get(pk=pk)
        return PetitionSerializer(petition, context={'scope': self.scope}).data

    @petition_activity.groups_for_signal
    def petition_activity_groups(self, instance: Petition, **kwargs):
        yield f'petition__{instance.pk}'

    @petition_activity.groups_for_consumer
    def petition_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'petition__{pk}'

    @petition_activity.serializer
    def petition_activity_serializer(self, instance: Petition, action, **kwargs):
        return {
            'data': instance.pk,
            'action': action.value,
            'request_id': 'petitions',
            'pk': instance.pk,
            'response_status': 201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        }

    async def disconnect(self, code):
        await self.petition_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Filter ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        action = kwargs.get('action')
        previous_petitions = kwargs.get('previous_petitions')
        search_term = kwargs.get('search_term')
        is_open = kwargs.get('is_open', True)
        filter_by_region = kwargs.get('filter_by_region', True)
        sort_by = kwargs.get('sort_by', 'popular')
        start_date = kwargs.get('start_date')
        end_date = kwargs.get('end_date')
        county = kwargs.get('county')
        constituency = kwargs.get('constituency')
        ward = kwargs.get('ward')

        # Pagination exclusion
        if previous_petitions:
            queryset = queryset.exclude(id__in=previous_petitions)

        if action == 'list':
            # Search
            if search_term:
                queryset = queryset.filter(
                    Q(title__icontains=search_term) |
                    Q(description__icontains=search_term) |
                    Q(author__name__icontains=search_term) |
                    Q(county__name__icontains=search_term) |
                    Q(constituency__name__icontains=search_term) |
                    Q(ward__name__icontains=search_term)
                ).distinct()

            # Open status
            if is_open is not None:
                queryset = queryset.filter(is_open=bool(is_open))

            # Regional filtering (optimized)
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
                queryset = queryset.filter(created_at__range=(start_date, end_date))

            # Sorting
            if sort_by == 'recent':
                queryset = queryset.order_by('-created_at')
            elif sort_by == 'oldest':
                queryset = queryset.order_by('created_at')
            else:
                # Default: popular (most supporters)
                queryset = queryset.annotate(
                    supporters_count=Count('supporters')
                ).order_by('-supporters_count', '-created_at')

            return queryset

        elif action == 'user_petitions':
            return queryset.filter(author=kwargs.get('user')).order_by('-created_at')

        elif action in ['delete', 'patch']:
            return queryset.filter(author=self.scope['user'])

        # Default fallback
        return queryset.annotate(supporters_count=Count('supporters')).order_by('-supporters_count', '-created_at')

    # ====================== List & Create ======================
    @action()
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
    def list_(self, queryset: QuerySet, page_size: int, **kwargs):
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = PetitionSerializer(
            page_obj.object_list,
            many=True,
            context={'scope': self.scope}
        )

        return {
            'results': serializer.data,
            'previous_petitions': kwargs.get('previous_petitions'),
            'has_next': page_obj.has_next()
        }

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        if isinstance(response, dict) and "id" in response:
            await self.petition_activity.subscribe(pk=response["id"], request_id=request_id)
        return response, status

    @action()
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        pk = response["id"]
        await self.petition_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.petition_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    # ====================== Support Action (Optimized) ======================
    @action()
    async def support(self, pk: int, request_id: str, **kwargs):
        result = await self.perform_support(pk=pk)
        if isinstance(result, dict) and result.get('error'):
            return await self.reply(
                action='support',
                errors=[result['error']],
                status=result.get('status', 403)
            )
        return result, 200

    @database_sync_to_async
    def perform_support(self, pk: int):
        """Atomic support toggle"""
        try:
            with transaction.atomic():
                petition = Petition.objects.select_related('county', 'constituency', 'ward').get(
                    pk=pk, is_open=True, is_active=True
                )

                if not self._user_can_support(petition):
                    return {'error': 'You are not a registered voter in the region', 'status': 403}

                user = self.scope['user']
                if petition.supporters.filter(pk=user.pk).exists():
                    petition.supporters.remove(user)
                    is_supported = False
                else:
                    petition.supporters.add(user)
                    is_supported = True

                # Get fresh count
                supporters_count = petition.supporters.count()

                return {
                    'pk': petition.pk,
                    'is_supported': is_supported,
                    'supporters': supporters_count
                }

        except Petition.DoesNotExist:
            return {'error': 'Petition not found or closed', 'status': 404}
        except Exception:
            return {'error': 'Failed to update support', 'status': 400}

    def _user_can_support(self, petition: Petition) -> bool:
        """Fast region check"""
        user = self.scope['user']
        if not petition.county:
            return True

        if petition.county != user.county:
            return False
        if petition.constituency and petition.constituency != user.constituency:
            return False
        if petition.ward and petition.ward != user.ward:
            return False
        return True

    # ====================== Other Actions ======================
    @action()
    async def change_status(self, pk: int, request_id: str, **kwargs):
        result = await self.perform_change_status(pk=pk)
        return result, 200

    @database_sync_to_async
    def perform_change_status(self, pk: int):
        petition = Petition.objects.get(pk=pk, author=self.scope['user'])
        petition.is_open = not petition.is_open
        petition.save()
        return {'pk': petition.pk, 'is_open': petition.is_open}

    @action()
    async def user_petitions(self, request_id: str, page_size=None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size or self.page_size, **kwargs)
        return data, 200
