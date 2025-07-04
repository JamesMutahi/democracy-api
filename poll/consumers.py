from channels.db import database_sync_to_async
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin

from poll.models import Poll, Option, Reason
from poll.serializers import PollSerializer


class PollConsumer(ListModelMixin, ObserverModelInstanceMixin, GenericAsyncAPIConsumer):
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
        return list(Poll.objects.filter(end_time__gte=timezone.now()).values_list('pk', flat=True))

    @action()
    async def vote(self, option: int, **kwargs):
        await self.vote_(pk=option)

    @database_sync_to_async
    def vote_(self, pk: int):
        option = Option.objects.get(pk=pk)
        user = self.scope['user']
        for o in option.poll.options.all():
            if o.votes.filter(id=user.id).exists():
                if o.id != pk:
                    o.votes.remove(user)
                    Reason.objects.filter(poll=o.poll, user=user).delete()
                else:
                    option.poll.save()
                    return option
        option.votes.add(user)
        option.poll.save()
        return option

    @action()
    async def add_reason(self, pk: int, text: str, **kwargs):
        poll = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.add_reason_(poll=poll, text=text)
        return await self.reply(data=data, action='update')

    @database_sync_to_async
    def add_reason_(self, poll: Poll, text: str):
        user = self.scope['user']
        reason_qs = Reason.objects.filter(poll=poll, user=user)
        if reason_qs.exists():
            reason = reason_qs.first()
            reason.text = text
            reason.save()
            return PollSerializer(poll, context={'scope': self.scope}).data
        else:
            Reason.objects.create(poll=poll, user=user, text=text)
            return PollSerializer(poll, context={'scope': self.scope}).data
