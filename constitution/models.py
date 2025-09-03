from django.db import models


class Chapter(models.Model):
    title = models.CharField(max_length=255)

    class Meta:
        db_table = 'Chapter'
        ordering = ['id']

    def __str__(self):
        return self.title


class Part(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.PROTECT, related_name='parts')
    title = models.CharField(max_length=255)

    class Meta:
        db_table = 'Part'
        ordering = ['id']

    def __str__(self):
        return f'{self.chapter} > {self.title}'


class Article(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.PROTECT, related_name='articles', null=True, blank=True)
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='articles', null=True, blank=True)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)

    class Meta:
        db_table = 'Article'
        ordering = ['id']

    def __str__(self):
        return self.title


class Schedule(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)

    class Meta:
        db_table = 'Schedule'
        ordering = ['id']

    def __str__(self):
        return self.title