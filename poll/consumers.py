from channels.db import database_sync_to_async
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer

from poll.models import Poll, Option, Reason
from poll.serializers import PollSerializer


class PollConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    serializer_class = PollSerializer
    queryset = Poll.objects.all()
    lookup_field = "pk"

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        await self.poll_activity.subscribe()
        await self.option_activity.subscribe()

    @model_observer(Poll)
    async def poll_activity(self, message, observer=None, action=None, **kwargs):
        message['data'] = await self.get_poll_serializer_data(pk=message['pk'])
        await self.send_json(message)

    @database_sync_to_async
    def get_poll_serializer_data(self, pk):
        poll = Poll.objects.get(pk=pk)
        serializer = PollSerializer(instance=poll, context={'scope': self.scope})
        return serializer.data

    @poll_activity.serializer
    def poll_activity(self, instance: Poll, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            action=action.value,
            request_id=1,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Option)
    async def option_activity(self, message, observer=None, action=None, **kwargs):
        message['data'] = await self.get_poll_serializer_data(pk=message['pk'])
        await self.send_json(message)

    @option_activity.serializer
    def option_activity(self, instance: Option, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            action='update',
            request_id=1,
            pk=instance.poll.pk,
            response_status=200,
        )

    async def disconnect(self, code):
        await self.poll_activity.unsubscribe()
        await self.option_activity.unsubscribe()
        await super().disconnect(code)

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
