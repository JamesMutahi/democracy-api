from channels.db import database_sync_to_async
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models.signals import post_save
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

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Poll)
    async def poll_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_poll_serializer_data(poll=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_poll_serializer_data(self, poll: Poll):
        serializer = PollSerializer(instance=poll, context={'scope': self.scope})
        return serializer.data

    @poll_activity.groups_for_signal
    def poll_activity(self, instance: Poll, **kwargs):
        yield f'poll__{instance.pk}'

    @poll_activity.groups_for_consumer
    def poll_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'poll__{pk}'

    @poll_activity.serializer
    def poll_activity(self, instance: Poll, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance,
            action=action.value,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Option)
    async def option_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        message['data'] = await self.get_poll_serializer_data(poll=instance)
        await self.send_json(message)

    @option_activity.groups_for_signal
    def option_activity(self, instance: Option, **kwargs):
        yield f'poll__{instance.poll.id}'

    @option_activity.groups_for_consumer
    def option_activity(self, poll=None, **kwargs):
        if poll is not None:
            yield f'poll__{poll}'

    @option_activity.serializer
    def option_activity(self, instance: Option, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance.poll,
            action='update',
            pk=instance.pk,
            response_status=200,
        )

    async def disconnect(self, code):
        await self.unsubscribe()
        await super().disconnect(code)

    @action()
    async def subscribe(self, pk, request_id, **kwargs):
        await self.poll_activity.subscribe(pk=pk, request_id=request_id)
        await self.option_activity.subscribe(poll=pk, request_id=request_id)

    async def unsubscribe(self):
        await self.poll_activity.unsubscribe()
        await self.option_activity.unsubscribe()

    @action()
    async def list(self, request_id, page, page_size=5, **kwargs):
        if page == 1:
            await self.unsubscribe()
        object_list, data = await self.list_(page, page_size, **kwargs)
        pks = await self.get_poll_pks(object_list)
        for pk in pks:
            await self.subscribe(pk=pk, request_id=request_id)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def list_(self, page, page_size, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        paginator = Paginator(queryset, page_size)
        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
        serializer = PollSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return page_obj.object_list, dict(results=serializer.data, current_page=page_obj.number,
                                          has_next=page_obj.has_next())

    @database_sync_to_async
    def get_poll_pks(self, queryset):
        return list(queryset.values_list('id', flat=True))

    @action()
    async def vote(self, option: int, **kwargs):
        await self.vote_(pk=option)

    @database_sync_to_async
    def vote_(self, pk: int):
        option = Option.objects.get(pk=pk)
        user = self.scope['user']
        for o in option.poll.options.all():
            if o.votes.contains(user):
                if o.id != pk:
                    o.votes.remove(user)
                    Reason.objects.filter(poll=o.poll, user=user).delete()
                else:
                    return self.signal(option.poll)
        option.votes.add(user)
        self.signal(option.poll)
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

    @staticmethod
    def signal(poll: Poll):
        return post_save.send(sender=Poll, instance=poll, created=False)
