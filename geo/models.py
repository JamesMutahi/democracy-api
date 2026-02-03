from django.db import models


class County(models.Model):
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ['name']
        verbose_name = 'county'
        verbose_name_plural = 'counties'
        db_table = 'County'

    def __str__(self):
        return self.name


class Constituency(models.Model):
    name = models.CharField(max_length=255)
    county = models.ForeignKey(County, on_delete=models.PROTECT, related_name='constituencies')

    class Meta:
        ordering = ['name']
        verbose_name = 'constituency'
        verbose_name_plural = 'constituencies'
        db_table = 'Constituency'

    def __str__(self):
        return self.name


class Ward(models.Model):
    name = models.CharField(max_length=255)
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, related_name='wards')

    class Meta:
        ordering = ['name']
        verbose_name = 'ward'
        verbose_name_plural = 'wards'
        db_table = 'Ward'

    def __str__(self):
        return self.name
