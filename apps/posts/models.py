from django.contrib.auth import get_user_model
from django.contrib.gis.db import models
from django.db.models import ExpressionWrapper, Count, F, FloatField
from django.db.models.functions import NullIf
from django.db.models.signals import post_save
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.ballot.models import Ballot
from apps.constitution.models import Section
from apps.meeting.models import Meeting
from apps.petition.models import Petition
from apps.survey.models import Survey

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UploadVideoTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/posts/{}'.format(instance.author.id, filename)

    def deconstruct(self):
        return 'apps.posts.models.UploadVideoTo', [self.name], {}


class UploadImageTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/posts/{}'.format(instance.author.id, filename)

    def deconstruct(self):
        return 'apps.posts.models.UploadImageTo', [self.name], {}


class UploadFileTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/posts/{}'.format(instance.author.id, filename)

    def deconstruct(self):
        return 'apps.posts.models.UploadFileTo', [self.name], {}


class Post(BaseModel):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('published', 'Published'),
    )
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    body = models.TextField(blank=True)
    image1 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    image2 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    image3 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    image4 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    video = models.FileField(upload_to=UploadVideoTo('videos/'), null=True, blank=True)
    file = models.FileField(upload_to=UploadFileTo('files/'), null=True, blank=True)
    location = models.PointField(srid=4326, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='published')
    published_at = models.DateTimeField(default=timezone.now)
    reply_to = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    repost_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reposts')
    community_note_of = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                                          related_name='community_notes')
    ballot = models.ForeignKey(Ballot, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    survey = models.ForeignKey(Survey, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    petition = models.ForeignKey(Petition, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    meeting = models.ForeignKey(Meeting, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    section = models.ForeignKey(Section, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    likes = models.ManyToManyField(User, blank=True, related_name='liked_posts')
    bookmarks = models.ManyToManyField(User, blank=True, related_name='bookmarked_posts')
    views = models.ManyToManyField(User, blank=True, related_name='viewed_posts')
    tagged_users = models.ManyToManyField(User, blank=True, related_name='tagged_in_posts')
    # For community notes
    upvotes = models.ManyToManyField(User, blank=True, related_name='upvotes')
    downvotes = models.ManyToManyField(User, blank=True, related_name='downvotes')

    is_deleted = models.BooleanField(_('deleted'), default=False)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        ordering = ['-published_at']
        db_table = 'Post'

    def __str__(self):
        return self.body

    def get_top_note(self):
        top_note = self.community_notes.annotate(
            upvotes_count=Count('upvotes'),
            downvotes_count=Count('downvotes'),
            total_votes=ExpressionWrapper(F('upvotes_count') + F('downvotes_count'), output_field=FloatField()),
            helpful_score=ExpressionWrapper(
                F('upvotes_count') * 1.0 / NullIf('total_votes', 0),
                output_field=FloatField()
            )).filter(total_votes__gt=0, helpful_score__gt=0.7).order_by(
            '-helpful_score',
            '-upvotes_count',
            '-downvotes_count',
            '-created_at'
        ).first()
        if not top_note:
            return ''
        return top_note.body

    def get_reposts_count(self):
        count = self.reposts.filter(reply_to=None, community_note_of=None).count()
        return count

    def delete(self, *args, **kwargs):
        self.reposts.filter(body='').delete()
        if self.reply_to is None:
            self.replies.all().delete()
        if self.reply_to is not None and self.replies.exists():
            return mark_deleted(self)
        if self.reposts.exists():
            return mark_deleted(self)
        if self.messages.exists():
            return mark_deleted(self)
        return super(Post, self).delete(*args, **kwargs)


class Report(BaseModel):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='reports')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports')
    issue = models.CharField(max_length=255)

    class Meta:
        db_table = 'Report'

    def __str__(self):
        return self.issue


def mark_deleted(post: Post):
    post.body = ''
    post.ballot = None
    post.survey = None
    post.petition = None
    post.meeting = None
    post.image1 = None
    post.image2 = None
    post.image3 = None
    post.image4 = None
    post.video1 = None
    post.video2 = None
    post.video3 = None
    post.is_deleted = True
    post.save()
    post.bookmarks.clear()
    post.likes.clear()
    post.views.clear()
    post.tagged_users.clear()
    post_save.send(sender=Post, instance=post, created=False)
    return post
