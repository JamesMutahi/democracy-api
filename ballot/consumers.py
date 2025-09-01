from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.observer import model_observer

from ballot.models import Ballot, Option, Reason
from ballot.serializers import BallotSerializer
from chat.utils.list_paginator import list_paginator


class BallotConsumer(GenericAsyncAPIConsumer):
    serializer_class = BallotSerializer
    queryset = Ballot.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        await self.ballot_activity.subscribe()
        await self.option_activity.subscribe()

    @model_observer(Ballot)
    async def ballot_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_ballot_serializer_data(ballot=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_ballot_serializer_data(self, ballot: Ballot):
        serializer = BallotSerializer(instance=ballot, context={'scope': self.scope})
        return serializer.data

    @ballot_activity.serializer
    def ballot_activity(self, instance: Ballot, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance,
            action=action.value,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Option, many_to_many=True)
    async def option_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        message['data'] = await self.get_ballot_serializer_data(ballot=instance)
        await self.send_json(message)

    @option_activity.serializer
    def option_activity(self, instance: Option, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance.ballot,
            action='update',
            pk=instance.pk,
            response_status=200,
        )

    async def disconnect(self, code):
        await self.unsubscribe()
        await super().disconnect(code)

    async def unsubscribe(self):
        await self.ballot_activity.unsubscribe()
        await self.option_activity.unsubscribe()

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        search_term = kwargs.get('search_term', None)
        if search_term:
            return queryset.filter(Q(name__icontains=search_term) | Q(description__icontains=search_term)).distinct()
        return queryset

    @action()
    async def list(self, request_id, last_ballot: int = None, page_size=page_size, **kwargs):
        if not last_ballot:
            await self.unsubscribe()
        data = await self.list_(page_size=page_size, last_ballot=last_ballot, **kwargs)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def list_(self, page_size, last_ballot: int = None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        if last_ballot:
            ballot = Ballot.objects.get(pk=last_ballot)
            queryset = queryset.filter(start_time__lt=ballot.start_time)
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size, )
        serializer = BallotSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, last_ballot=last_ballot, has_next=page_obj.has_next())

    @action()
    async def vote(self, pk: int, **kwargs):
        option: Option = await database_sync_to_async(Option.objects.get)(pk=pk, poll__is_active=True)
        await self.vote_(option=option)

    @database_sync_to_async
    def vote_(self, option):
        user = self.scope['user']
        for o in option.ballot.options.all():
            if o.votes.contains(user):
                if o.id != option.id:
                    o.votes.remove(user)
                    Reason.objects.filter(ballot=o.ballot, user=user).delete()
        option.votes.add(user)
        return option

    @action()
    async def add_reason(self, pk: int, text: str, **kwargs):
        ballot = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.add_reason_(ballot=ballot, text=text)
        return await self.reply(data=data, action='update')

    @database_sync_to_async
    def add_reason_(self, ballot: Ballot, text: str):
        user = self.scope['user']
        reason_qs = Reason.objects.filter(ballot=ballot, user=user)
        if reason_qs.exists():
            reason = reason_qs.first()
            reason.text = text
            reason.save()
            return BallotSerializer(ballot, context={'scope': self.scope}).data
        else:
            Reason.objects.create(ballot=ballot, user=user, text=text)
            return BallotSerializer(ballot, context={'scope': self.scope}).data
