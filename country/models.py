from django.contrib.auth import get_user_model
from django.contrib.gis.db import models

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class County(BaseModel):
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    center = models.PointField(srid=4326, null=True)
    boundaries = models.MultiPolygonField(null=True)
    website = models.URLField(null=True, blank=True)
    followers = models.ManyToManyField(User, blank=True, related_name='followed_counties')

    class Meta:
        ordering = ['name']
        verbose_name = 'county'
        verbose_name_plural = 'counties'
        db_table = 'County'

    def __str__(self):
        return self.name


class Constituency(BaseModel):
    name = models.CharField(max_length=255)
    county = models.ForeignKey(County, on_delete=models.PROTECT, related_name='constituencies')
    boundaries = models.MultiPolygonField(null=True)
    followers = models.ManyToManyField(User, blank=True, related_name='followed_constituencies')

    class Meta:
        ordering = ['name']
        verbose_name = 'constituency'
        verbose_name_plural = 'constituencies'
        db_table = 'Constituency'

    def __str__(self):
        return self.name
