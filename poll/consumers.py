from datetime import datetime

from channels.db import database_sync_to_async
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin, CreateModelMixin, UpdateModelMixin
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin

from poll.models import Poll, Option
from poll.serializers import PollSerializer


class PollConsumer(ListModelMixin, CreateModelMixin, UpdateModelMixin, ObserverModelInstanceMixin,
                   GenericAsyncAPIConsumer):
    serializer_class = PollSerializer
    queryset = Poll.objects.all()
    lookup_field = "pk"

    @action()
    async def subscribe(self, request_id, **kwargs):
        pks = await self.get_running_polls_pks()
        for pk in pks:
            await self.subscribe_instance(pk=pk, request_id=request_id)

    @database_sync_to_async
    def get_running_polls_pks(self):
        return list(Poll.objects.filter(end_time__gte=datetime.now()).values_list('pk', flat=True))

    @action()
    async def vote(self, option: int, **kwargs):
        await self.vote_(pk=option)

    @database_sync_to_async
    def vote_(self, pk: int):
        option = Option.objects.get(id=pk)
        user = self.scope['user']
        for o in option.poll.options.all():
            if o.votes.filter(id=user.id).exists():
                o.votes.remove(user)
        option.votes.add(user)
        option.poll.save()
