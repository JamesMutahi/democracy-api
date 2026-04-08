from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Q
from django.db.models.signals import post_save
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin, PatchModelMixin
from djangochannelsrestframework.observer import model_observer
from rest_framework.exceptions import PermissionDenied

from apps.petition.models import Petition
from apps.users.serializers import UserSerializer
from apps.utils.list_paginator import list_paginator

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

    # ====================== Real-time Observer ======================
    @model_observer(User)
    async def user_activity(self, message, **kwargs):
        if message['action'] != 'delete':
            message['data'] = await self.get_user_serializer_data(pk=message['data'])
        await self.send_json(message)

    @database_sync_to_async
    def get_user_serializer_data(self, pk: int):
        user = User.objects.select_related('preferences').prefetch_related(
            'following', 'followers', 'muted', 'blocked'
        ).get(pk=pk)
        return UserSerializer(user, context={'scope': self.scope}).data

    @user_activity.groups_for_signal
    def user_activity_groups(self, instance: User, **kwargs):
        yield f'user__{instance.pk}'

    @user_activity.groups_for_consumer
    def user_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'user__{pk}'

    @user_activity.serializer
    def user_activity_serializer(self, instance: User, action, **kwargs):
        return {
            'data': instance.pk,
            'action': action.value,
            'pk': instance.pk,
            'response_status': 200
        }

    async def disconnect(self, code):
        await self.user_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Filter with Permission ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        action = kwargs.get('action')

        if action == 'list':
            search_term = kwargs.get('search_term')
            if search_term:
                queryset = queryset.filter(
                    Q(username__icontains=search_term) |
                    Q(name__icontains=search_term)
                ).distinct()

        # Restrict patch to self only
        if action == 'patch':
            if self.scope['user'].id != kwargs.get('pk'):
                return queryset.none()

        return queryset

    # ====================== List ======================
    @action()
    def list(self, page: int = 1, page_size=None, last_user: int = None, **kwargs):
        users = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = self.users_paginator(users, page, page_size or self.page_size, last_user)
        return data, 200

    def users_paginator(self, users, page: int, page_size: int, last_user: int = None):
        if last_user:
            try:
                last = User.objects.get(pk=last_user)
                users = users.filter(name__gt=last.name)
            except User.DoesNotExist:
                pass

        page_obj = list_paginator(queryset=users, page=page, page_size=page_size)
        serializer = UserSerializer(page_obj.object_list, many=True, context={'scope': self.scope})

        return {
            'results': serializer.data,
            'last_user': last_user,
            'has_next': page_obj.has_next()
        }

    # ====================== Retrieve & Subscription ======================
    @action()
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        if isinstance(response, dict):
            pk = response.get("id")
            if pk:
                await self.user_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.user_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    # ====================== Social Actions with Permission Checks ======================
    @action()
    async def mute(self, pk: int, **kwargs):
        if pk == self.scope['user'].id:
            return await self.reply(errors=["You cannot mute yourself"], status=400)

        result = await self.toggle_mute(pk=pk)
        return result, 200

    @database_sync_to_async
    def toggle_mute(self, pk: int):
        target = User.objects.get(pk=pk)
        current = self.scope['user']

        if target in current.muted.all():
            current.muted.remove(target)
        else:
            current.muted.add(target)

        return UserSerializer(target, context={'scope': self.scope}).data

    @action()
    async def block(self, pk: int, **kwargs):
        if pk == self.scope['user'].id:
            return await self.reply(errors=["You cannot block yourself"], status=400)

        result = await self.toggle_block(pk=pk)
        return result, 200

    @database_sync_to_async
    def toggle_block(self, pk: int):
        target = User.objects.get(pk=pk)
        current = self.scope['user']

        if target in current.blocked.all():
            current.blocked.remove(target)
        else:
            current.blocked.add(target)
            current.muted.remove(target)
            current.following.remove(target)

        self._signal_user_update(target)
        return UserSerializer(target, context={'scope': self.scope}).data

    @action()
    async def follow(self, pk: int, **kwargs):
        if pk == self.scope['user'].id:
            return await self.reply(errors=["You cannot follow yourself"], status=400)

        result = await self.toggle_follow(pk=pk)
        return result, 200

    @database_sync_to_async
    def toggle_follow(self, pk: int):
        target = User.objects.get(pk=pk)
        current = self.scope['user']

        if target in current.following.all():
            current.following.remove(target)
            if hasattr(current, 'preferences'):
                current.preferences.allowed_users.remove(target)
        else:
            current.following.add(target)
            if hasattr(current, 'preferences'):
                current.preferences.allowed_users.add(target)

        self._signal_user_update(target)
        return UserSerializer(target, context={'scope': self.scope}).data

    @action()
    async def notify(self, pk: int, **kwargs):
        if pk == self.scope['user'].id:
            return await self.reply(errors=["Cannot change notification for yourself"], status=400)

        result = await self.toggle_notify(pk=pk)
        return await self.reply(data=result, action='update', status=200)

    @database_sync_to_async
    def toggle_notify(self, pk: int):
        target = User.objects.get(pk=pk)
        current = self.scope['user']

        if hasattr(current, 'preferences'):
            if target in current.preferences.allowed_users.all():
                current.preferences.allowed_users.remove(target)
            else:
                current.preferences.allowed_users.add(target)

        return UserSerializer(target, context={'scope': self.scope}).data

    # ====================== Private Lists (with Permission) ======================
    @action()
    async def muted(self, request_id: str, page: int = 1, page_size=None, last_user: int = None, **kwargs):
        """Only the owner can see their muted list"""
        if not self._is_current_user(self.scope['user'].id):
            raise PermissionDenied("You can only view your own muted list")

        data = await self.get_muted_list(page, page_size or self.page_size, last_user)
        return data, 200

    @action()
    async def blocked(self, request_id: str, page: int = 1, page_size=None, last_user: int = None, **kwargs):
        """Only the owner can see their blocked list"""
        if not self._is_current_user(self.scope['user'].id):
            raise PermissionDenied("You can only view your own blocked list")

        data = await self.get_blocked_list(page, page_size or self.page_size, last_user)
        return data, 200

    # ====================== Public Lists ======================
    @action()
    async def following(self, request_id: str, pk: int, page: int = 1, page_size=None, last_user: int = None, **kwargs):
        data = await self.get_following_list(pk, page, page_size or self.page_size, last_user)
        return data, 200

    @action()
    async def followers(self, request_id: str, pk: int, page: int = 1, page_size=None, last_user: int = None, **kwargs):
        data = await self.get_followers_list(pk, page, page_size or self.page_size, last_user)
        return data, 200

    @action()
    async def petition_supporters(self, request_id: str, pk: int, page: int = 1, page_size=None, last_user: int = None,
                                  **kwargs):
        data = await self.get_petition_supporters(pk, page, page_size or self.page_size, last_user)
        return data, 200

    # ====================== Private List Helpers ======================
    @database_sync_to_async
    def get_muted_list(self, page: int, page_size: int, last_user: int = None):
        users = self.scope['user'].muted.all()
        return self.users_paginator(users, page, page_size, last_user)

    @database_sync_to_async
    def get_blocked_list(self, page: int, page_size: int, last_user: int = None):
        users = self.scope['user'].blocked.all()
        return self.users_paginator(users, page, page_size, last_user)

    # ====================== Public List Helpers ======================
    @database_sync_to_async
    def get_following_list(self, pk: int, page: int, page_size: int, last_user: int = None):
        user = User.objects.get(pk=pk)
        users = user.following.all()
        return self.users_paginator(users, page, page_size, last_user)

    @database_sync_to_async
    def get_followers_list(self, pk: int, page: int, page_size: int, last_user: int = None):
        user = User.objects.get(pk=pk)
        users = user.followers.all()
        return self.users_paginator(users, page, page_size, last_user)

    @database_sync_to_async
    def get_petition_supporters(self, pk: int, page: int, page_size: int, last_user: int = None):
        petition = Petition.objects.get(pk=pk)
        users = petition.supporters.all()
        return self.users_paginator(users, page, page_size, last_user)

    # ====================== Helpers ======================
    def _is_current_user(self, user_id: int) -> bool:
        """Check if the requested user is the currently authenticated user"""
        return self.scope['user'].id == user_id

    def _signal_user_update(self, user: User):
        """Signal both current user and target user for real-time updates"""
        post_save.send(sender=User, instance=self.scope['user'], created=False)
        post_save.send(sender=User, instance=user, created=False)
