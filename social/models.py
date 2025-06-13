from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublishedManager(models.Manager):
    def get_queryset(self):
        return super(PublishedManager, self).get_queryset().filter(status='published')


class Post(BaseModel):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('published', 'Published'),
    )
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    body = models.TextField(blank=True)
    image1 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image2 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image3 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image4 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image5 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image6 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    video1 = models.ImageField(upload_to='posts/videos/', null=True, blank=True)
    video2 = models.ImageField(upload_to='posts/videos/', null=True, blank=True)
    video3 = models.ImageField(upload_to='posts/videos/', null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    published_at = models.DateTimeField(default=timezone.now)
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='replies')
    repost_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='reposts')
    likes = models.ManyToManyField(User, blank=True, related_name='post_likes')
    views = models.ManyToManyField(User, blank=True)
    is_edited = models.BooleanField(_('edited'), default=False)
    is_deleted = models.BooleanField(_('deleted'), default=False)
    is_active = models.BooleanField(_('active'), default=True)
    objects = models.Manager()  # Default manager.
    published = PublishedManager()  # Custom manager.

    class Meta:
        ordering = ['-published_at']
        db_table = 'Post'

    def __str__(self):
        return self.body
