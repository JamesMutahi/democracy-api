from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Q
from django.db.models.signals import post_save
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin, PatchModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import action

from chat.utils.list_paginator import list_paginator
from users.serializers import UserSerializer

User = get_user_model()


class UserConsumer(RetrieveModelMixin, PatchModelMixin, GenericAsyncAPIConsumer):
    serializer_class = UserSerializer
    queryset = User.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(User)
    async def user_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        message['data'] = await self.get_user_serializer_data(user=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_user_serializer_data(self, user: User):
        serializer = UserSerializer(instance=user, context={'scope': self.scope})
        return serializer.data

    @user_activity.groups_for_signal
    def user_activity(self, instance: User, **kwargs):
        yield f'user__{instance.pk}'

    @user_activity.groups_for_consumer
    def user_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'user__{pk}'

    @user_activity.serializer
    def user_activity(self, instance: User, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance,
            action=action.value,
            pk=instance.pk,
            response_status=200
        )

    async def disconnect(self, code):
        await self.user_activity.unsubscribe()
        await super().disconnect(code)

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        if kwargs.get('action') == 'list':
            search_term = kwargs.get('search_term', None)
            if search_term:
                return queryset.filter(Q(username__icontains=search_term) |
                                       Q(name__icontains=search_term)
                                       ).distinct()
        if kwargs.get('action') == 'patch' and self.scope['user'].id != kwargs.get('pk'):
            return None
        return queryset

    @action()
    def list(self, page=1, page_size=page_size, **kwargs):
        users = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = self.users_paginator(users, page, page_size)
        return data, 200

    @action()
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        pk = response["id"]
        await self.user_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def mute(self, pk: int, **kwargs):
        user = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.mute_(user=user)
        return await self.reply(data=data, action='update', status=200)

    @database_sync_to_async
    def mute_(self, user: User):
        if user in self.scope['user'].muted.all():
            self.scope['user'].muted.remove(user)
        else:
            self.scope['user'].muted.add(user)
        self.signal(user)
        return UserSerializer(user, context={'scope': self.scope}).data

    @action()
    async def block(self, pk: int, **kwargs):
        user = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.block_(user=user)
        return await self.reply(data=data, action='update', status=200)

    @database_sync_to_async
    def block_(self, user: User):
        if user in self.scope['user'].blocked.all():
            self.scope['user'].blocked.remove(user)
        else:
            self.scope['user'].blocked.add(user)
            self.scope['user'].following.remove(user)
            self.scope['user'].followers.remove(user)
        self.signal(user)
        return UserSerializer(user, context={'scope': self.scope}).data

    @action()
    async def follow(self, pk: int, **kwargs):
        user = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.follow_(user=user)
        return await self.reply(data=data, action='update', status=200)

    @database_sync_to_async
    def follow_(self, user: User):
        if user in self.scope['user'].following.all():
            self.scope['user'].following.remove(user)
        else:
            self.scope['user'].following.add(user)
        self.signal(user)
        return UserSerializer(user, context={'scope': self.scope}).data

    def signal(self, user: User):
        post_save.send(sender=User, instance=self.scope['user'], created=False)
        post_save.send(sender=User, instance=user, created=False)
        return

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.user_activity.unsubscribe(pk=pk, request_id=request_id)

    @action()
    def following(self, pk: int, page=1, page_size=page_size, **kwargs):
        user = self.get_object(pk=pk)
        users = user.following.all()
        data = self.users_paginator(users, page, page_size)
        return data, 200

    @action()
    def followers(self, pk: int, page=1, page_size=page_size, **kwargs):
        user = self.get_object(pk=pk)
        users = user.followers.all()
        data = self.users_paginator(users, page, page_size)
        return data, 200

    @action()
    def muted(self, page=1, page_size=page_size, **kwargs):
        users = self.scope['user'].muted.all()
        data = self.users_paginator(users, page, page_size)
        return data, 200

    @action()
    def blocked(self, page=1, page_size=page_size, **kwargs):
        users = self.scope['user'].blocked.all()
        data = self.users_paginator(users, page, page_size)
        return data, 200

    def users_paginator(self, users, page: int, page_size, **kwargs):
        page_obj = list_paginator(users, page, page_size)
        serializer = UserSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        data = dict(results=serializer.data, current_page=page_obj.number, has_next=page_obj.has_next())
        return data
