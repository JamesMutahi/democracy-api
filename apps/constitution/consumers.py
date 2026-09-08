from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin, RetrieveModelMixin

from apps.constitution.models import (
    CONSTITUTION_CACHE_VERSION_KEY,
    Section,
)
from apps.constitution.serializers import SectionSerializer
from apps.utils.throttles import rate_limit

CONSTITUTION_LIST_CACHE_PREFIX = "constitution:list:v1"
CONSTITUTION_LIST_CACHE_TIMEOUT = 60 * 60 * 24 * 30  # 30 days


@database_sync_to_async
def build_section_depth_cache():
    """
    Build a section_id -> parent_count map in Python.

    This avoids N+1 database queries during serializer.parent_count traversal.
    """
    parent_map = dict(
        Section.objects.values_list("id", "parent_id")
    )

    depth_map = {}

    for start in parent_map:
        if start in depth_map:
            continue

        path = []
        current = start

        while (
                current is not None
                and current in parent_map
                and current not in depth_map
        ):
            # Cycle guard. This should not happen after model validation and
            # DB constraints, but we keep the serializer/consumer safe anyway.
            if current in path:
                path = []
                current = None
                break

            path.append(current)
            current = parent_map[current]

        if not path:
            depth_map[start] = 0
            continue

        if current in depth_map:
            depth = depth_map[current]
        else:
            # current is None, missing, or part of a broken/circular hierarchy.
            # Treat the last resolved node as root-like.
            depth = -1

        for node in reversed(path):
            depth += 1
            depth_map[node] = depth

    return depth_map


class ConstitutionConsumer(ListModelMixin, RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = SectionSerializer
    queryset = Section.objects.select_related("parent").order_by("id")
    lookup_field = "pk"

    async def connect(self):
        user = self.scope.get("user")

        if user is not None and user.is_authenticated:
            await self.accept()
        else:
            await self.close(code=4401)

    def get_serializer_context(self, **kwargs):
        context = super().get_serializer_context(**kwargs)
        context["section_depth"] = getattr(self, "_section_depth_cache", None)
        return context

    @staticmethod
    def _apply_search(queryset, search_term: str):
        """
        Apply advanced full-text search with ranking and fuzzy matching.
        Supports multilingual content (English, Swahili, etc.)
        """
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
            rank=SearchRank("search_vector", search_query),
            text_sim=TrigramSimilarity("text", search_term),
            numeral_sim=TrigramSimilarity("numeral", search_term),
        ).filter(
            Q(search_vector=search_query)
            | Q(text_sim__gt=0.2)
            | Q(numeral_sim__gt=0.3)
        )

        # Order by relevance: exact matches first, then fuzzy matches
        ordering = ["-rank", "-numeral_sim", "-text_sim", "id"]
        return queryset, ordering

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, **kwargs):
        search_term = kwargs.get("search_term")

        # NEW: If searching, skip cache and use advanced search
        if search_term and isinstance(search_term, str):
            search_term = search_term.strip()
            if search_term:
                self._section_depth_cache = await build_section_depth_cache()
                try:
                    queryset = self.get_queryset()
                    if len(search_term) >= 2:
                        queryset, ordering = self._apply_search(queryset, search_term)
                        queryset = queryset.order_by(*ordering)
                    else:
                        # Fallback for single character searches
                        queryset = queryset.none()

                    # Serialize results
                    serializer = self.serializer_class(
                        queryset[:100],  # Limit search results
                        many=True,
                        context=self.get_serializer_context(**kwargs)
                    )
                    data = {
                        "results": serializer.data,
                        "has_next": False,
                    }
                finally:
                    self._section_depth_cache = None

                return data, 200

        # Original cached list logic
        cache_key = await sync_to_async(self._build_cache_key)(kwargs)

        cached = await sync_to_async(cache.get)(cache_key)
        if cached is not None:
            return cached

        self._section_depth_cache = await build_section_depth_cache()

        try:
            data = await super().list(**kwargs)
        finally:
            # Do not leak list-specific depth cache into later retrieve calls.
            self._section_depth_cache = None

        await sync_to_async(cache.set)(
            cache_key,
            data,
            CONSTITUTION_LIST_CACHE_TIMEOUT,
        )

        return data

    @staticmethod
    def _build_cache_key(kwargs: dict) -> str:
        """
        Cache key includes:
        - serializer/list shape version
        - cache invalidation version
        - pagination params that can change the response
        """
        version = cache.get_or_set(
            CONSTITUTION_CACHE_VERSION_KEY,
            1,
            timeout=None,
        )

        page = kwargs.get("page")
        page_size = kwargs.get("page_size")

        return (
            f"{CONSTITUTION_LIST_CACHE_PREFIX}:{version}:"
            f"page={page}:page_size={page_size}"
        )
