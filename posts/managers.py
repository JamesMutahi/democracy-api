from django.db import models


class PublishedManager(models.Manager):
    def get_queryset(self):
        return super(PublishedManager, self).get_queryset().filter(status='published', reply_to=None)


class RepostManager(models.Manager):
    def get_queryset(self):
        return super(RepostManager, self).get_queryset().filter(status='published', reply_to=None).exclude(
            repost_of=None)


class ReplyManager(models.Manager):
    def get_queryset(self):
        return super(ReplyManager, self).get_queryset().filter(status='published').exclude(reply_to=None)
