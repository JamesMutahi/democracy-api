import re
from typing import Dict, Any

from channels.consumer import AsyncConsumer
from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import (
    ListModelMixin,
    CreateModelMixin,
    PatchModelMixin,
    DeleteModelMixin,
    RetrieveModelMixin
)
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.permissions import IsAuthenticated

from social import models, serializers


class IsAuthenticatedForWrite(IsAuthenticated):
    async def has_permission(
            self, scope: Dict[str, Any],
            consumer: AsyncConsumer,
            action: str,
            **kwargs
    ) -> bool:
        """
        This method will permit un-authenticated requests
         for non-destructive actions only.
        """

        if action in ('list', 'retrieve'):
            return True
        return await super().has_permission(
            scope,
            consumer,
            action,
            **kwargs
        )


class PostConsumer(
    ListModelMixin,
    RetrieveModelMixin,
    CreateModelMixin,
    PatchModelMixin,
    DeleteModelMixin,
    GenericAsyncAPIConsumer
):
    queryset = models.Post.objects.all()
    serializer_class = serializers.PostSerializer
    permission_classes = (IsAuthenticatedForWrite,)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.subscribed_to_list = False
        self.subscribed_to_hashtag = None

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        # we need to ensure that only the author can edit there posts.
        if kwargs.get('action') == 'list':
            filter = kwargs.get("body_contains", None)
            if filter:
                queryset = queryset.filter(body__icontains=filter)
            # users can list the latest 500 posts
            return queryset.order_by('-created_at')[:500]

        if kwargs.get('action') == 'retrieve':
            return queryset

        # for other actions we can only expose the posts created by this user.
        return queryset.filter(author=self.scope.get("user"))

    @model_observer(models.Post)
    async def post_change_handler(self, message, observer=None, **kwargs):
        # called when a subscribed item changes
        await self.send_json(message)

    @post_change_handler.groups_for_signal
    def post_change_handler(self, instance: models.Post, **kwargs):
        # DO NOT DO DATABASE QUERIES HERE
        # This is called very often through the lifecycle of every instance of a Post model
        for hashtag in re.findall(r"#[a-z0-9]+", instance.body.lower()):
            yield f'-hashtag-{hashtag}'
        yield '-all'

    @post_change_handler.groups_for_consumer
    def post_change_handler(self, hashtag=None, list=False, **kwargs):
        # This is called when you subscribe/unsubscribe
        if hashtag is not None:
            yield f'-hashtag-#{hashtag}'
        if list:
            yield '-all'

    @action()
    async def subscribe_to_hashtag(self, hashtag, **kwargs):
        await self.clear_subscription()
        await self.post_change_handler.subscribe(hashtag=hashtag)
        self.subscribed_to_hashtag = hashtag
        return {}, 201

    @action()
    async def subscribe_to_list(self, **kwargs):
        await self.clear_subscription()
        await self.post_change_handler.subscribe(list=True)
        self.subscribed_to_list = True
        return {}, 201

    @action()
    async def unsubscribe_from_hashtag(self, hashtag, **kwargs):
        await self.post_change_handler.unsubscribe(hashtag=hashtag)
        if self.subscribe_to_hashtag == hashtag:
            self.subscribed_to_hashtag = None
        return {}, 204

    @action()
    async def unsubscribe_from_list(self, **kwargs):
        await self.post_change_handler.unsubscribe(list=True)
        self.subscribed_to_list = False
        return {}, 204

    async def clear_subscription(self):
        if self.subscribe_to_hashtag is not None:
            await self.post_change_handler.unsubscribe(
                hashtag=self.subscribe_to_hashtag
            )
            self.subscribed_to_hashtag = None

        if self.subscribe_to_list:
            await self.post_change_handler.unsubscribe(
                list=True
            )
            self.subscribed_to_list = False

    @post_change_handler.serializer
    def post_change_handler(self, instance: models.Post, action, **kwargs):
        if action == 'delete':
            return {"pk": instance.pk}
        return {"pk": instance.pk, "data": {"body": instance.body}}
