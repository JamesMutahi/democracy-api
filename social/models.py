from django.contrib.auth import get_user_model
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class IpAddress(BaseModel):
    ip = models.GenericIPAddressField(max_length=255)

    class Meta:
        db_table = 'IpAddress'

    def __str__(self):
        return self.ip


class Post(BaseModel):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    body = models.TextField()
    image1 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image2 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image3 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image4 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image5 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    image6 = models.ImageField(upload_to='posts/images/', null=True, blank=True)
    video1 = models.ImageField(upload_to='posts/videos/', null=True, blank=True)
    video2 = models.ImageField(upload_to='posts/videos/', null=True, blank=True)
    video3 = models.ImageField(upload_to='posts/videos/', null=True, blank=True)
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    repost_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reposts')
    likes = models.ManyToManyField(User, blank=True, related_name='post_likes')
    ip_address = models.GenericIPAddressField()
    is_edited = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    ip_views = models.ManyToManyField(IpAddress, blank=True)
    user_views = models.ManyToManyField(User, blank=True)

    class Meta:
        ordering = ['-id']
        db_table = 'Post'

    def __str__(self):
        return self.body

    def clean(self):
        if self.reply_to is not None and self.repost_of is not None:
            raise ValidationError(
                {
                    "reply_to": ValidationError(_("reply to and repost of cannot be selected at the same time"), code="invalid"),
                }
            )
        
