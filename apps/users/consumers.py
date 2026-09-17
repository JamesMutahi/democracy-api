from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchQuery, TrigramSimilarity, SearchRank
from django.db import transaction, DatabaseError
from django.db.models import QuerySet, Q
from django.db.models.signals import post_save
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound

from apps.broadcast.models import Broadcast
from apps.broadcast.services import BroadcastParticipantService
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.recommendations.follow_recommender import FollowRecommender
from apps.users.models import ProfileVisit
from apps.users.querysets import annotate_user_queryset
from apps.users.serializers import UserSerializer
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit

User = get_user_model()


class UserConsumer(RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = UserSerializer
    queryset = User.objects.all()
    lookup_field = "pk"

    page_size = 20
    max_page_size = 100

    async def connect(self):
        if self.scope["user"].is_authenticated:
            await self.accept()
        else:
            await self.close()

    async def disconnect(self, code):
        await self.user_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Real-time Observer ======================

    @model_observer(User)
    async def user_activity(self, message, **kwargs):
        pk = message.get("data")

        if not pk:
            return

        if message.get("action") != "delete":
            try:
                message["data"] = await self.get_user_serializer_data(pk=pk)
            except (User.DoesNotExist, NotFound):
                return

        await self.send_json(message)

    @user_activity.groups_for_signal
    def user_activity_signal_groups(self, instance: User, **kwargs):
        yield f"user__{instance.pk}"

    @user_activity.groups_for_consumer
    def user_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"user__{pk}"

    @user_activity.serializer
    def user_activity_serializer(self, instance: User, action, **kwargs):
        return {
            "data": instance.pk,
            "action": action.value,
            "pk": instance.pk,
            "response_status": 200,
        }

    @database_sync_to_async
    def get_user_serializer_data(self, pk: int):
        user = self.get_annotated_queryset(include_inactive=True).get(pk=pk)
        return UserSerializer(user, context={"scope": self.scope}).data

    # ====================== Querysets / Permissions ======================

    def get_annotated_queryset(self, include_inactive: bool = False):
        queryset = User.objects.select_related(
            "county",
            "constituency",
            "ward",
        )

        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        return annotate_user_queryset(queryset, self.scope.get("user"))

    def get_object(self, **kwargs):
        pk = kwargs.get(self.lookup_field)

        if pk is None:
            raise NotFound("User not found")

        try:
            obj = self.get_annotated_queryset(include_inactive=True).get(pk=pk)
        except User.DoesNotExist:
            raise NotFound("User not found")

        return obj

    @staticmethod
    def get_user_or_error(pk: int):
        try:
            return User.objects.get(pk=pk)
        except (User.DoesNotExist, ValueError, TypeError):
            raise NotFound("User not found")

    def exclude_blocked_users(self, queryset: QuerySet) -> QuerySet:
        """
        Exclude users blocked by the current user and users who blocked the current user.
        """
        current = self.scope.get("user")

        if current and current.is_authenticated:
            queryset = queryset.exclude(pk__in=current.blocked.values("pk"))
            queryset = queryset.exclude(pk__in=current.blockers.values("pk"))

        return queryset

    def exclude_hidden_social_users(self, queryset: QuerySet) -> QuerySet:
        """
        Exclude blocked/blocking users and the current user.
        Useful for search and recommendation lists.
        """
        current = self.scope.get("user")

        queryset = self.exclude_blocked_users(queryset)

        if current and current.is_authenticated:
            queryset = queryset.exclude(pk=current.pk)

        return queryset

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        action_name = kwargs.get("action")

        if action_name == "list":
            search_term = kwargs.get("search_term")

            if search_term is not None:
                search_term = search_term.strip()

            if search_term and len(search_term) >= 2:
                try:
                    # Use 'simple' config for multilingual support
                    search_query = SearchQuery(
                        search_term,
                        config="simple",
                        search_type="websearch",
                    )
                except DatabaseError:
                    # Fallback to plain text search if websearch syntax is invalid
                    search_query = SearchQuery(search_term, config="simple")

                queryset = queryset.annotate(
                    rank=SearchRank("name_vector", search_query),
                    username_sim=TrigramSimilarity("username", search_term),
                    name_sim=TrigramSimilarity("name", search_term),
                ).filter(
                    Q(name_vector=search_query)
                    | Q(username_vector=search_query)
                    | Q(username_sim__gt=0.25)
                    | Q(name_sim__gt=0.25)
                ).order_by(
                    "-rank",
                    "-username_sim",
                    "-name_sim",
                )
            elif search_term:
                queryset = queryset.none()

        return queryset

    # ====================== List ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def list(
            self,
            request_id: str = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await database_sync_to_async(self.paginate_users)(queryset, page, page_size, last_user)
        return data, 200

    @action()
    @rate_limit(limit=20, period=60)
    async def recommendations(
            self,
            request_id: str = None,
            page: int = 1,
            page_size: int = None,
            **kwargs,
    ):
        data = await self.get_recommendations(page=page, page_size=page_size)
        return data, 200

    @database_sync_to_async
    def get_recommendations(self, page: int, page_size: int):
        recommender = FollowRecommender(self.scope["user"])
        recommended = recommender.get_follow_recommendations(limit=50)

        if isinstance(recommended, QuerySet):
            pks = recommended.values("pk")
        else:
            pks = [user.pk for user in recommended]

        queryset = self.get_annotated_queryset(include_inactive=False).filter(pk__in=pks)
        queryset = self.exclude_hidden_social_users(queryset)

        return self.paginate_users(queryset, page, page_size)

    # ====================== Subscription ======================

    @action()
    @interaction_rate_limit
    async def retrieve(self, request_id: str, username: str, **kwargs):
        data = await self.get_user_by_username(username=username)
        await self.user_activity.subscribe(pk=data["id"], request_id=request_id)
        return data, 200

    @database_sync_to_async
    def get_user_by_username(self, username: str):
        try:
            user = self.get_annotated_queryset(include_inactive=True).get(username=username)
        except User.DoesNotExist:
            raise NotFound("User not found")
        return UserSerializer(user, context={"scope": self.scope}).data

    @action()
    @interaction_rate_limit
    async def subscribe(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        pk = response.get("id")

        if pk:
            await self.user_activity.subscribe(pk=pk, request_id=request_id)

        return response, status

    @action()
    @interaction_rate_limit
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.user_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    # ====================== Social Actions ======================

    @action()
    @interaction_rate_limit
    async def mute(self, pk: int, **kwargs):
        if pk == self.scope["user"].id:
            raise ValidationError("You cannot mute yourself")

        result = await self.toggle_mute(pk=pk)
        return result, 200

    @database_sync_to_async
    def toggle_mute(self, pk: int):
        target = self.get_user_or_error(pk)
        current = self.scope["user"]

        if current.muted.filter(pk=target.pk).exists():
            current.muted.remove(target)
        else:
            current.muted.add(target)
            current.notifiers.remove(target)

        self._signal_user_update(current, target)
        return self.serialize_user(target.pk)

    @action()
    @interaction_rate_limit
    async def block(self, pk: int, **kwargs):
        if pk == self.scope["user"].id:
            raise ValidationError("You cannot block yourself")

        result = await self.toggle_block(pk=pk)
        return result, 200

    @database_sync_to_async
    def toggle_block(self, pk: int):
        target = self.get_user_or_error(pk)
        current = self.scope["user"]

        if current.blocked.filter(pk=target.pk).exists():
            current.blocked.remove(target)
        else:
            current.blocked.add(target)

            current.muted.remove(target)
            current.following.remove(target)
            current.notifiers.remove(target)

            target.following.remove(current)
            target.notifiers.remove(current)

        self._signal_user_update(current, target)
        return self.serialize_user(target.pk)

    @action()
    @interaction_rate_limit
    async def follow(self, pk: int, **kwargs):
        if pk == self.scope["user"].id:
            raise ValidationError("You cannot follow yourself")

        result = await self.toggle_follow(pk=pk)
        return result, 200

    @database_sync_to_async
    def toggle_follow(self, pk: int):
        target = self.get_user_or_error(pk)
        current = self.scope["user"]

        if not target.is_active:
            raise PermissionDenied("You cannot follow this user.")

        if current.blocked.filter(pk=target.pk).exists():
            raise PermissionDenied("Unblock this user before following them.")

        if target.blocked.filter(pk=current.pk).exists():
            raise PermissionDenied("You cannot follow this user.")

        if current.following.filter(pk=target.pk).exists():
            current.following.remove(target)
            current.notifiers.remove(target)
        else:
            current.following.add(target)
            current.notifiers.add(target)

        self._signal_user_update(current, target)
        return self.serialize_user(target.pk)

    @action()
    @interaction_rate_limit
    async def toggle_notifications(self, pk: int, **kwargs):
        if pk == self.scope["user"].id:
            raise ValidationError("Cannot change notification for yourself")

        data = await self.toggle_notify(pk=pk)
        return data, 200

    @database_sync_to_async
    def toggle_notify(self, pk: int):
        target = self.get_user_or_error(pk)
        current = self.scope["user"]

        if not target.is_active:
            raise PermissionDenied("You cannot change notifications for this user.")

        if current.blocked.filter(pk=target.pk).exists():
            raise PermissionDenied("Unblock this user before changing notifications.")

        if target.blocked.filter(pk=current.pk).exists():
            raise PermissionDenied("You cannot change notifications for this user.")

        if current.notifiers.filter(pk=target.pk).exists():
            current.notifiers.remove(target)
        else:
            current.notifiers.add(target)

        self._signal_user_update(current, target)
        return self.serialize_user(target.pk)

    @action()
    @interaction_rate_limit
    async def add_visit(self, pk: int, **kwargs):
        await self.record_profile_visit(pk=pk)
        return {"pk": pk}, 200

    @database_sync_to_async
    @transaction.atomic
    def record_profile_visit(self, pk: int):
        current = self.scope["user"]
        visited = self.get_user_or_error(pk)

        if pk == current.id:
            raise PermissionDenied("You cannot visit yourself")

        if not visited.is_active:
            raise NotFound("User not found")

        if current.blocked.filter(pk=visited.pk).exists():
            raise PermissionDenied("You cannot visit this profile.")

        if visited.blocked.filter(pk=current.pk).exists():
            raise PermissionDenied("You cannot visit this profile.")

        visit, created = ProfileVisit.objects.update_or_create(
            visitor=current,
            visited=visited,
            defaults={"visited_at": timezone.now()},
        )

        return visit.pk

    # ====================== Private Lists ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def muted(
            self,
            request_id: str = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        requested_pk = kwargs.get("pk")

        if requested_pk is not None and not self._is_current_user(requested_pk):
            raise PermissionDenied("You can only view your own muted list")

        data = await self.get_muted_list(page, page_size, last_user)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def blocked(
            self,
            request_id: str = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        requested_pk = kwargs.get("pk")

        if requested_pk is not None and not self._is_current_user(requested_pk):
            raise PermissionDenied("You can only view your own blocked list")

        data = await self.get_blocked_list(page, page_size, last_user)
        return data, 200

    # ====================== Public Lists ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def following(
            self,
            request_id: str = None,
            pk: int = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        data = await self.get_following_list(pk, page, page_size, last_user)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def followers(
            self,
            request_id: str = None,
            pk: int = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        data = await self.get_followers_list(pk, page, page_size, last_user)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def petition_supporters(
            self,
            request_id: str = None,
            pk: int = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        data = await self.get_petition_supporters(pk, page, page_size, last_user)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def reposts(
            self,
            request_id: str = None,
            pk: int = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        data = await self.get_reposts(pk, page, page_size, last_user)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def broadcast_participants(
            self,
            request_id: str = None,
            pk: int = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        data = await self.get_broadcast_participants(pk, page, page_size, last_user)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def broadcast_listeners(
            self,
            request_id: str = None,
            pk: int = None,
            page: int = 1,
            page_size: int = None,
            last_user: int = None,
            **kwargs,
    ):
        data = await self.get_broadcast_listeners(pk, page, page_size, last_user)
        return data, 200

    # ====================== Private List Helpers ======================

    @database_sync_to_async
    def get_muted_list(self, page: int, page_size: int, last_user: int = None):
        current = self.scope["user"]

        queryset = self.get_annotated_queryset(include_inactive=True).filter(
            pk__in=current.muted.values("pk")
        )

        return self.paginate_users(queryset, page, page_size, last_user)

    @database_sync_to_async
    def get_blocked_list(self, page: int, page_size: int, last_user: int = None):
        current = self.scope["user"]

        queryset = self.get_annotated_queryset(include_inactive=True).filter(
            pk__in=current.blocked.values("pk")
        )

        return self.paginate_users(queryset, page, page_size, last_user)

    # ====================== Public List Helpers ======================

    @database_sync_to_async
    def get_following_list(self, pk: int, page: int, page_size: int, last_user: int = None):
        target = self.get_user_or_error(pk)

        queryset = self.get_annotated_queryset(include_inactive=False).filter(
            pk__in=target.following.values("pk")
        )
        queryset = self.exclude_blocked_users(queryset)

        return self.paginate_users(queryset, page, page_size, last_user)

    @database_sync_to_async
    def get_followers_list(self, pk: int, page: int, page_size: int, last_user: int = None):
        target = self.get_user_or_error(pk)

        queryset = self.get_annotated_queryset(include_inactive=False).filter(
            pk__in=target.followers.values("pk")
        )
        queryset = self.exclude_blocked_users(queryset)

        return self.paginate_users(queryset, page, page_size, last_user)

    @database_sync_to_async
    def get_petition_supporters(self, pk: int, page: int, page_size: int, last_user: int = None):
        petition = self.get_petition_or_error(pk)

        queryset = self.get_annotated_queryset(include_inactive=False).filter(
            pk__in=petition.supporters.values("pk")
        )
        queryset = self.exclude_blocked_users(queryset)

        return self.paginate_users(queryset, page, page_size, last_user)

    @database_sync_to_async
    def get_reposts(self, pk: int, page: int, page_size: int, last_user: int = None):
        if not Post.objects.filter(pk=pk).exists():
            raise NotFound("Post not found")

        user_ids = (
            User.objects.filter(
                posts__repost_of=pk,
                posts__repost_type=Post.RepostType.REPOST,
            )
            .values("pk")
            .distinct()
        )

        queryset = self.get_annotated_queryset(include_inactive=False).filter(pk__in=user_ids)
        queryset = self.exclude_blocked_users(queryset)

        return self.paginate_users(queryset, page, page_size, last_user)

    @database_sync_to_async
    def get_broadcast_participants(self, pk: int, page: int, page_size: int, last_user: int = None):
        self.get_broadcast_or_error(pk)

        ids = BroadcastParticipantService.get_all_participant_ids(pk)

        queryset = self.get_annotated_queryset(include_inactive=False).filter(pk__in=ids)
        queryset = self.exclude_blocked_users(queryset)

        return self.paginate_users(queryset, page, page_size, last_user)

    @database_sync_to_async
    def get_broadcast_listeners(self, pk: int, page: int, page_size: int, last_user: int = None):
        broadcast = self.get_broadcast_or_error(pk)

        ids = BroadcastParticipantService.get_all_participant_ids(pk)

        queryset = self.get_annotated_queryset(include_inactive=False).filter(pk__in=ids)

        if broadcast.host_id:
            queryset = queryset.exclude(pk=broadcast.host_id)

        queryset = queryset.exclude(pk__in=broadcast.co_hosts.values("pk"))
        queryset = queryset.exclude(pk__in=broadcast.speakers.values("pk"))
        queryset = self.exclude_blocked_users(queryset)

        return self.paginate_users(queryset, page, page_size, last_user)

    # ====================== Object Helpers ======================

    @staticmethod
    def get_petition_or_error(pk: int):
        try:
            return Petition.objects.get(pk=pk)
        except (Petition.DoesNotExist, ValueError, TypeError):
            raise NotFound("Petition not found")

    @staticmethod
    def get_broadcast_or_error(pk: int):
        try:
            return Broadcast.objects.get(pk=pk)
        except (Broadcast.DoesNotExist, ValueError, TypeError):
            raise NotFound("Broadcast not found")

    # ====================== Serialization / Pagination ======================

    def serialize_user(self, pk: int):
        user = self.get_annotated_queryset(include_inactive=True).get(pk=pk)
        return UserSerializer(user, context={"scope": self.scope}).data

    def get_page_size(self, page_size=None) -> int:
        try:
            page_size = int(page_size or self.page_size)
        except (TypeError, ValueError):
            page_size = self.page_size

        return max(1, min(page_size, self.max_page_size))

    def paginate_users(
            self,
            queryset: QuerySet,
            page: int,
            page_size: int,
            last_user: int = None,
    ):
        """
        Stable cursor-aware paginator.
        """
        page_size = self.get_page_size(page_size)

        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1

        if last_user:
            page = 1

        if last_user:
            try:
                last = User.objects.only("name", "pk").get(pk=last_user)
                queryset = queryset.filter(
                    Q(name__gt=last.name)
                    | (Q(name=last.name) & Q(pk__gt=last.pk))
                )
            except (User.DoesNotExist, ValueError, TypeError):
                pass

        page_obj = list_paginator(
            queryset=queryset,
            page=page,
            page_size=page_size,
        )

        object_list = list(page_obj.object_list)

        serializer = UserSerializer(
            object_list,
            many=True,
            context={"scope": self.scope},
        )

        return {
            "results": serializer.data,
            "last_user": last_user,
            "has_next": page_obj.has_next(),
        }

    # ====================== Helpers ======================

    def _is_current_user(self, user_id: int) -> bool:
        current = self.scope.get("user")
        return current.pk == int(user_id)

    def _signal_user_update(self, *users: User):
        """
        Manually notify observers that user objects changed.

        This is useful because M2M changes do not always emit model save signals.
        """
        current = self.scope.get("user")
        seen = set()

        post_save.send(sender=User, instance=current, created=False)
        seen.add(current.pk)

        for user in users:
            if user and user.pk not in seen:
                post_save.send(sender=User, instance=user, created=False)
                seen.add(user.pk)
