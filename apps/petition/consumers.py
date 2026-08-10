from datetime import datetime, time

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
)
from djangochannelsrestframework.observer import model_observer
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.petition.models import Petition, PetitionClick, PetitionSupport
from apps.petition.serializers import PetitionSerializer
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import interaction_rate_limit, rate_limit

User = get_user_model()


class PetitionConsumer(
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericAsyncAPIConsumer,
):
    serializer_class = PetitionSerializer
    queryset = Petition.objects.all()
    lookup_field = "pk"

    page_size = 20
    max_page_size = 100

    async def connect(self):
        user = self.scope.get("user")

        if user and getattr(user, "is_authenticated", False):
            await self.accept()
        else:
            await self.close()

    # ====================== Real-time Observers ======================

    @model_observer(Petition, many_to_many=True)
    async def petition_activity(self, message, **kwargs):
        """
        Observer for Petition model changes.
        """
        if message.get("action") != "delete":
            data = await self.get_petition_serializer_data(
                pk=message.get("data")
            )

            if data is None:
                message["action"] = "delete"
                message["data"] = message.get("data")
            else:
                message["data"] = data

        await self.send_json(message)

    @database_sync_to_async
    def get_petition_serializer_data(self, pk: int):
        """
        Serialize a petition for realtime updates.

        Uses the optimized queryset with select_related and annotations.
        """
        try:
            petition = self.get_queryset().get(pk=pk)
        except Petition.DoesNotExist:
            return None

        return PetitionSerializer(
            petition,
            context={"scope": self.scope},
        ).data

    @petition_activity.groups_for_signal
    def petition_activity_signal_groups(self, instance: Petition, **kwargs):
        if instance.pk:
            yield f"petition__{instance.pk}"

    @petition_activity.groups_for_consumer
    def petition_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"petition__{pk}"

    @petition_activity.serializer
    def petition_activity_serializer(self, instance: Petition, action, **kwargs):
        return {
            "data": instance.pk,
            "action": action.value,
            "pk": instance.pk,
            "response_status": (
                201
                if action.value == "create"
                else 204
                if action.value == "delete"
                else 200
            ),
        }

    @model_observer(PetitionSupport, many_to_many=True)
    async def support_activity(self, message, **kwargs):
        """
        Observer for PetitionSupport changes.

        When support changes, broadcast updated parent petition data.
        """
        petition_pk = message.get("data")

        if not petition_pk:
            return

        data = await self.get_petition_serializer_data(pk=petition_pk)

        if data is None:
            return

        message["data"] = data
        message["action"] = "update"

        await self.send_json(message)

    @support_activity.groups_for_signal
    def support_activity_signal_groups(
        self,
        instance: PetitionSupport,
        **kwargs,
    ):
        if instance.petition_id:
            yield f"petition__{instance.petition_id}"

    @support_activity.groups_for_consumer
    def support_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"petition__{pk}"

    @support_activity.serializer
    def support_activity_serializer(
        self,
        instance: PetitionSupport,
        action,
        **kwargs,
    ):
        return {
            "data": instance.petition_id,
            "action": "update",
            "pk": instance.pk,
            "response_status": 200,
        }

    async def disconnect(self, code):
        await self.petition_activity.unsubscribe()
        await self.support_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Queryset / Helpers ======================

    def get_queryset(self, **kwargs) -> QuerySet:
        """
        Base queryset for all consumer queries.

        Optimizations:
        - select_related author/location FKs
        - exclude inactive petitions except internal author-only actions
        - annotate supporters_count
        - annotate whether current user supports each petition
        """
        action_name = kwargs.get("action")

        queryset = Petition.objects.select_related(
            "author",
            "county",
            "constituency",
            "ward",
        )

        if action_name not in {"delete", "patch"}:
            queryset = queryset.filter(is_active=True)

        queryset = queryset.annotate(
            supporters_count=Count("supporters", distinct=True),
        )

        user = self.scope.get("user")

        if user is not None and getattr(user, "is_authenticated", False):
            queryset = queryset.annotate(
                is_supported_by_request_user=Exists(
                    PetitionSupport.objects.filter(
                        petition_id=OuterRef("pk"),
                        user_id=user.pk,
                    )
                )
            )

        return queryset

    def _as_bool(self, value, default: bool = True) -> bool:
        """
        Parse JSON/client boolean-like values safely.
        """
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {
                "0",
                "false",
                "no",
                "off",
                "",
                "none",
                "null",
            }:
                return False

            return True

        return bool(value)

    def _parse_datetime(self, value, end_of_day: bool = False):
        """
        Parse datetime or date strings.

        If only a date is provided:
        - start_date becomes 00:00:00
        - end_date becomes 23:59:59.999999
        """
        if value in (None, ""):
            return None

        if isinstance(value, datetime):
            parsed = value
        else:
            value = str(value)
            parsed = parse_datetime(value)

            if parsed is None:
                parsed_date = parse_date(value)

                if parsed_date is None:
                    return None

                parsed = datetime.combine(
                    parsed_date,
                    time.max if end_of_day else time.min,
                )

        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)

        return parsed

    def _normalize_previous_petitions(self, value):
        """
        Normalize previous_petitions into a clean list of integers.
        """
        if not value:
            return []

        if isinstance(value, (int, str)):
            value = [value]

        normalized = []

        for item in value:
            try:
                normalized.append(int(item))
            except (TypeError, ValueError):
                continue

        return normalized

    def _get_page_size(self, page_size) -> int:
        try:
            size = int(page_size or self.page_size)
        except (TypeError, ValueError):
            size = self.page_size

        return max(1, min(size, self.max_page_size))

    # ====================== Filter ======================

    def filter_queryset(self, queryset: QuerySet, **kwargs) -> QuerySet:
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        action_name = kwargs.get("action")
        previous_petitions = kwargs.get("previous_petitions") or []

        if previous_petitions:
            queryset = queryset.exclude(id__in=previous_petitions)

        if action_name == "list":
            search_term = kwargs.get("search_term")

            if isinstance(search_term, str):
                search_term = search_term.strip()

            if search_term:
                search_filter = (
                    Q(title__icontains=search_term)
                    | Q(description__icontains=search_term)
                    | Q(county__name__icontains=search_term)
                    | Q(constituency__name__icontains=search_term)
                    | Q(ward__name__icontains=search_term)
                )

                if hasattr(User, "name"):
                    search_filter |= Q(author__name__icontains=search_term)

                username_field = getattr(User, "USERNAME_FIELD", None)

                if username_field and hasattr(User, username_field):
                    search_filter |= Q(
                        **{
                            f"author__{username_field}__icontains": search_term
                        }
                    )

                queryset = queryset.filter(search_filter).distinct()

            is_open_raw = kwargs.get("is_open", True)

            if is_open_raw is not None:
                queryset = queryset.filter(
                    is_open=self._as_bool(is_open_raw, default=True)
                )

            filter_by_region = self._as_bool(
                kwargs.get("filter_by_region", True),
                default=True,
            )

            if filter_by_region:
                county = kwargs.get("county")
                constituency = kwargs.get("constituency")
                ward = kwargs.get("ward")

                county_id = getattr(county, "pk", county)
                constituency_id = getattr(constituency, "pk", constituency)
                ward_id = getattr(ward, "pk", ward)

                region_q = Q(
                    county_id__isnull=True,
                    constituency_id__isnull=True,
                    ward_id__isnull=True,
                )

                if county_id:
                    region_q |= Q(
                        county_id=county_id,
                        constituency_id__isnull=True,
                        ward_id__isnull=True,
                    )

                if constituency_id:
                    if county_id:
                        region_q |= Q(
                            county_id=county_id,
                            constituency_id=constituency_id,
                            ward_id__isnull=True,
                        )
                    else:
                        region_q |= Q(
                            constituency_id=constituency_id,
                            ward_id__isnull=True,
                        )

                if ward_id:
                    if county_id and constituency_id:
                        region_q |= Q(
                            county_id=county_id,
                            constituency_id=constituency_id,
                            ward_id=ward_id,
                        )
                    elif constituency_id:
                        region_q |= Q(
                            constituency_id=constituency_id,
                            ward_id=ward_id,
                        )
                    elif county_id:
                        region_q |= Q(
                            county_id=county_id,
                            ward_id=ward_id,
                        )
                    else:
                        region_q |= Q(ward_id=ward_id)

                queryset = queryset.filter(region_q)

            start_date = self._parse_datetime(
                kwargs.get("start_date"),
                end_of_day=False,
            )
            end_date = self._parse_datetime(
                kwargs.get("end_date"),
                end_of_day=True,
            )

            if start_date and end_date:
                queryset = queryset.filter(
                    created_at__range=(start_date, end_date)
                )
            elif start_date:
                queryset = queryset.filter(created_at__gte=start_date)
            elif end_date:
                queryset = queryset.filter(created_at__lte=end_date)

            sort_by = kwargs.get("sort_by", "popular")

            if sort_by in {"recent", "latest", "newest"}:
                return queryset.order_by("-created_at")

            if sort_by in {"oldest", "least_recent"}:
                return queryset.order_by("created_at")

            if sort_by in {"views", "most_viewed"}:
                return queryset.order_by("-views", "-created_at")

            return queryset.order_by("-supporters_count", "-created_at")

        if action_name == "user_petitions":
            user = kwargs.get("user") or self.scope.get("user")

            if not user or not getattr(user, "is_authenticated", False):
                return queryset.none()

            return queryset.filter(author=user).order_by("-created_at")

        if action_name in {"delete", "patch"}:
            user = self.scope.get("user")

            if not user:
                return queryset.none()

            return queryset.filter(author=user)

        return queryset.order_by("-supporters_count", "-created_at")

    # ====================== List & Create ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id: str, page_size=None, **kwargs):
        kwargs["action"] = "list"
        kwargs["previous_petitions"] = self._normalize_previous_petitions(
            kwargs.get("previous_petitions")
        )

        kwargs["county"], kwargs["constituency"], kwargs["ward"] = (
            await self.get_user_regions()
        )

        queryset = self.filter_queryset(
            self.get_queryset(**kwargs),
            **kwargs,
        )

        data = await self.list_(
            queryset=queryset,
            page_size=page_size,
            **kwargs,
        )

        await self.reply(
            action="list",
            data=data,
            request_id=request_id,
        )

    @database_sync_to_async
    def get_user_regions(self):
        user = self.scope.get("user")

        if not user or not getattr(user, "is_authenticated", False):
            return None, None, None

        return (
            getattr(user, "county", None),
            getattr(user, "constituency", None),
            getattr(user, "ward", None),
        )

    @database_sync_to_async
    def list_(self, queryset: QuerySet, page_size=None, **kwargs):
        page_size = self._get_page_size(page_size)

        try:
            page = int(kwargs.get("page", 1))
        except (TypeError, ValueError):
            page = 1

        page_obj = list_paginator(
            queryset=queryset,
            page=page,
            page_size=page_size,
        )

        serializer = PetitionSerializer(
            page_obj.object_list,
            many=True,
            context={"scope": self.scope},
        )

        previous = self._normalize_previous_petitions(
            kwargs.get("previous_petitions")
        )

        seen = set(previous)
        combined_previous = previous + [
            obj.pk
            for obj in page_obj.object_list
            if obj.pk not in seen
        ]

        return {
            "results": serializer.data,
            "previous_petitions": combined_previous,
            "has_next": page_obj.has_next(),
        }

    @action()
    @rate_limit(limit=40, period=60)
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)

        if status == 201 and isinstance(response, dict) and response.get("id"):
            await self.petition_activity.subscribe(
                pk=response["id"],
                request_id=request_id,
            )
            await self.support_activity.subscribe(
                pk=response["id"],
                request_id=request_id,
            )

        return response, status

    @action()
    @rate_limit(limit=40, period=60)
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)

        if status == 200 and isinstance(response, dict) and response.get("id"):
            await self.petition_activity.subscribe(
                pk=response["id"],
                request_id=request_id,
            )
            await self.support_activity.subscribe(
                pk=response["id"],
                request_id=request_id,
            )

        return response, status

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.petition_activity.unsubscribe(
            pk=pk,
            request_id=request_id,
        )
        await self.support_activity.unsubscribe(
            pk=pk,
            request_id=request_id,
        )

        return {}, 200

    # ====================== Support Action ======================

    @action()
    @interaction_rate_limit
    async def support(self, pk: int, request_id: str, **kwargs):
        try:
            result = await self.record_support(pk=pk)
        except (Petition.DoesNotExist, NotFound):
            return {"error": "Petition not found."}, 404
        except PermissionDenied as exc:
            return {"error": str(getattr(exc, "detail", "Permission denied."))}, 403
        except User.DoesNotExist:
            return {"error": "Authenticated user not found."}, 401

        return result, 200

    @database_sync_to_async
    def record_support(self, pk: int):
        """
        Atomic support toggle.

        Uses select_for_update on the petition to avoid race conditions.
        Uses the through model directly so observers fire correctly.
        """
        user_pk = getattr(self.scope.get("user"), "pk", None)

        if not user_pk:
            raise PermissionDenied("Authentication is required.")

        user = User.objects.only(
            "id",
            "county_id",
            "constituency_id",
            "ward_id",
        ).get(pk=user_pk)

        with transaction.atomic():
            petition = Petition.objects.select_for_update().get(
                pk=pk,
                is_open=True,
                is_active=True,
            )

            if not self._user_can_support(petition, user):
                raise PermissionDenied(
                    "You are not a registered voter in the region."
                )

            support = PetitionSupport.objects.filter(
                petition=petition,
                user_id=user.id,
            ).first()

            if support:
                support.delete()
                is_supported = False
            else:
                PetitionSupport.objects.create(
                    petition=petition,
                    user_id=user.id,
                )
                is_supported = True

            supporters_count = petition.supporters.count()

            return {
                "pk": petition.pk,
                "is_supported": is_supported,
                "supporters": supporters_count,
            }

    def _user_can_support(self, petition: Petition, user) -> bool:
        """
        Region eligibility check using IDs to avoid extra object fetches.
        """
        if not petition.county_id:
            return True

        if petition.county_id != getattr(user, "county_id", None):
            return False

        if (
            petition.constituency_id
            and petition.constituency_id != getattr(user, "constituency_id", None)
        ):
            return False

        if petition.ward_id and petition.ward_id != getattr(user, "ward_id", None):
            return False

        return True

    # ====================== Status / Views / Clicks ======================

    @action()
    @interaction_rate_limit
    async def change_status(self, pk: int, request_id: str, **kwargs):
        try:
            result = await self.perform_change_status(pk=pk)
        except Petition.DoesNotExist:
            return {"error": "Petition not found."}, 404

        return result, 200

    @database_sync_to_async
    def perform_change_status(self, pk: int):
        user_pk = getattr(self.scope.get("user"), "pk", None)

        if not user_pk:
            raise PermissionDenied("Authentication is required.")

        with transaction.atomic():
            petition = Petition.objects.select_for_update().get(
                pk=pk,
                author_id=user_pk,
                is_active=True,
            )

            petition.is_open = not petition.is_open
            petition.save(update_fields=["is_open", "updated_at"])

            return {
                "pk": petition.pk,
                "is_open": petition.is_open,
            }

    @action()
    @interaction_rate_limit
    async def add_view(self, pk: int, request_id: str = None, **kwargs):
        try:
            result = await self.record_view(pk=pk)
        except Petition.DoesNotExist:
            return {"error": "Petition not found."}, 404

        return result, 200

    @database_sync_to_async
    def record_view(self, pk: int):
        """
        Atomic view increment.

        Avoids lost updates from read-modify-write.
        """
        updated = Petition.objects.filter(
            pk=pk,
            is_active=True,
        ).update(views=F("views") + 1)

        if not updated:
            raise Petition.DoesNotExist

        return {"pk": pk}

    @action()
    @interaction_rate_limit
    async def add_click(self, pk: int, request_id: str = None, **kwargs):
        try:
            result = await self.record_click(pk=pk)
        except Petition.DoesNotExist:
            return {"error": "Petition not found."}, 404

        return result, 200

    @database_sync_to_async
    def record_click(self, pk: int):
        user_pk = getattr(self.scope.get("user"), "pk", None)

        if not user_pk:
            raise PermissionDenied("Authentication is required.")

        with transaction.atomic():
            petition = Petition.objects.only("id").get(
                pk=pk,
                is_active=True,
            )

            click, created = PetitionClick.objects.update_or_create(
                user_id=user_pk,
                petition=petition,
                defaults={"clicked_at": timezone.now()},
            )

            return {
                "pk": pk,
                "click_id": click.pk,
            }

    # ====================== User Petitions ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def user_petitions(self, request_id: str, page_size=None, **kwargs):
        kwargs["action"] = "user_petitions"
        kwargs["user"] = self.scope.get("user")
        kwargs["previous_petitions"] = self._normalize_previous_petitions(
            kwargs.get("previous_petitions")
        )

        queryset = self.filter_queryset(
            self.get_queryset(**kwargs),
            **kwargs,
        )

        data = await self.list_(
            queryset=queryset,
            page_size=page_size,
            **kwargs,
        )

        return data, 200