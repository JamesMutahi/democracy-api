from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.core.cache import cache
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer

from apps.geo.models import (
    GEO_CACHE_VERSION_KEY,
    County,
    Constituency,
    Ward,
)
from apps.geo.serializers import (
    CountySerializer,
    CountyListSerializer,
    ConstituencySerializer,
    ConstituencyListSerializer,
    WardSerializer,
    WardListSerializer,
)
from apps.utils.throttles import rate_limit

GEO_CACHE_TIMEOUT = 60 * 60 * 24 * 30  # 30 days


def _bool_param(kwargs: dict, name: str, default: bool = True) -> bool:
    """
    Small helper to parse boolean-like query/action params.

    Accepted truthy values:
        "1", "true", "yes", "on"

    Anything else is treated as false.
    """
    value = kwargs.get(name)

    if value is None:
        return default

    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _geo_cache_version() -> int:
    return cache.get_or_set(GEO_CACHE_VERSION_KEY, 1, timeout=None)


def _counties_cache_key(include_boundaries: bool) -> str:
    version = _geo_cache_version()
    return f"geo:counties:v{version}:boundaries={int(include_boundaries)}"


def _constituencies_cache_key(county_id: int, include_boundaries: bool) -> str:
    version = _geo_cache_version()
    return (
        f"geo:constituencies:v{version}:"
        f"county={county_id}:boundaries={int(include_boundaries)}"
    )


def _wards_cache_key(constituency_id: int, include_boundaries: bool) -> str:
    version = _geo_cache_version()
    return (
        f"geo:wards:v{version}:"
        f"constituency={constituency_id}:boundaries={int(include_boundaries)}"
    )


async def _cache_get(cache_key: str):
    return await sync_to_async(cache.get)(cache_key)


async def _cache_set(cache_key: str, data):
    await sync_to_async(cache.set)(cache_key, data, GEO_CACHE_TIMEOUT)


@database_sync_to_async
def _get_counties_data(include_boundaries: bool = True):
    queryset = County.objects.all().order_by("name")

    if include_boundaries:
        serializer_class = CountySerializer
    else:
        serializer_class = CountyListSerializer

    return serializer_class(queryset, many=True).data


@database_sync_to_async
def _get_constituencies_data(county_id: int, include_boundaries: bool = True):
    county_exists = County.objects.filter(pk=county_id).exists()

    if not county_exists:
        return None

    queryset = Constituency.objects.filter(
        county_id=county_id
    ).order_by("name")

    if include_boundaries:
        serializer_class = ConstituencySerializer
    else:
        serializer_class = ConstituencyListSerializer

    return serializer_class(queryset, many=True).data


@database_sync_to_async
def _get_wards_data(constituency_id: int, include_boundaries: bool = True):
    constituency_exists = Constituency.objects.filter(
        pk=constituency_id
    ).exists()

    if not constituency_exists:
        return None

    queryset = Ward.objects.filter(
        constituency_id=constituency_id
    ).order_by("name")

    if include_boundaries:
        serializer_class = WardSerializer
    else:
        serializer_class = WardListSerializer

    return serializer_class(queryset, many=True).data


class GeoConsumer(GenericAsyncAPIConsumer):
    async def connect(self):
        user = self.scope.get("user")

        if user is not None and user.is_authenticated:
            await self.accept()
        else:
            await self.close(code=4401)

    @action()
    @rate_limit(limit=40, period=60)
    async def counties(self, **kwargs):
        """
        Returns all counties.

        Optional param:
            include_boundaries=true|false

        By default, this returns boundaries.
        If clients only need names/centers, they can request:
            include_boundaries=false
        """
        include_boundaries = _bool_param(kwargs, "include_boundaries", True)

        cache_key = await sync_to_async(_counties_cache_key)(
            include_boundaries
        )

        cached = await _cache_get(cache_key)
        if cached is not None:
            return cached, 200

        data = await _get_counties_data(include_boundaries)

        await _cache_set(cache_key, data)

        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def constituencies(self, **kwargs):
        """
        Returns constituencies for a county.

        Required param:
            county=<county_id>

        Optional param:
            include_boundaries=true|false
        """
        county = kwargs.get("county")

        if county is None:
            return {"error": "county is required."}, 400

        try:
            county_id = int(county)
        except (TypeError, ValueError):
            return {"error": "county must be an integer."}, 400

        include_boundaries = _bool_param(kwargs, "include_boundaries", True)

        cache_key = await sync_to_async(_constituencies_cache_key)(
            county_id,
            include_boundaries,
        )

        cached = await _cache_get(cache_key)
        if cached is not None:
            return cached, 200

        data = await _get_constituencies_data(
            county_id,
            include_boundaries,
        )

        if data is None:
            return {"error": "County not found."}, 404

        await _cache_set(cache_key, data)

        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def wards(self, **kwargs):
        """
        Returns wards for a constituency.

        Required param:
            constituency=<constituency_id>

        Optional param:
            include_boundaries=true|false
        """
        constituency = kwargs.get("constituency")

        if constituency is None:
            return {"error": "constituency is required."}, 400

        try:
            constituency_id = int(constituency)
        except (TypeError, ValueError):
            return {"error": "constituency must be an integer."}, 400

        include_boundaries = _bool_param(kwargs, "include_boundaries", True)

        cache_key = await sync_to_async(_wards_cache_key)(
            constituency_id,
            include_boundaries,
        )

        cached = await _cache_get(cache_key)
        if cached is not None:
            return cached, 200

        data = await _get_wards_data(
            constituency_id,
            include_boundaries,
        )

        if data is None:
            return {"error": "Constituency not found."}, 404

        await _cache_set(cache_key, data)

        return data, 200
