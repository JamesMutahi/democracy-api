from django.contrib.gis.db import models
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save


GEO_CACHE_VERSION_KEY = "geo:cache-version"


class County(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    center = models.PointField(srid=4326, null=True, blank=True)
    boundaries = models.MultiPolygonField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "county"
        verbose_name_plural = "counties"
        db_table = "County"
        indexes = [
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return self.name


class Constituency(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    county = models.ForeignKey(
        County,
        on_delete=models.PROTECT,
        related_name="constituencies",
    )
    boundaries = models.MultiPolygonField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "constituency"
        verbose_name_plural = "constituencies"
        db_table = "Constituency"
        indexes = [
            models.Index(fields=["county", "name"]),
        ]

    def __str__(self):
        return self.name


class Ward(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    constituency = models.ForeignKey(
        Constituency,
        on_delete=models.PROTECT,
        related_name="wards",
    )
    boundaries = models.MultiPolygonField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "ward"
        verbose_name_plural = "wards"
        db_table = "Ward"
        indexes = [
            models.Index(fields=["constituency", "name"]),
        ]

    def __str__(self):
        return self.name


def _bump_geo_cache_version(sender, **kwargs):
    """
    Invalidate cached geo responses by bumping the geo cache version.

    Cached geo keys include this version, so changing it makes old cached
    responses unreachable without requiring cache pattern deletion.
    """
    version = cache.get(GEO_CACHE_VERSION_KEY, 1)
    cache.set(GEO_CACHE_VERSION_KEY, version + 1, timeout=None)


for model in (County, Constituency, Ward):
    post_save.connect(_bump_geo_cache_version, sender=model)
    post_delete.connect(_bump_geo_cache_version, sender=model)