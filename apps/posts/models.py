import uuid

from django.contrib.auth import get_user_model
from django.contrib.gis.db import models
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField, SearchVector
from django.db import transaction
from django.db.models import ExpressionWrapper, Count, F, FloatField
from django.db.models.functions import NullIf
from django.db.models.signals import post_save
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from taggit.managers import TaggableManager

from apps.ballot.models import Ballot
from apps.constitution.models import Section
from apps.broadcast.models import Broadcast
from apps.petition.models import Petition
from apps.survey.models import Survey

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Post(BaseModel):
    class Status(models.TextChoices):
        REPOST = 'draft', 'Draft'
        QUOTE = 'published', 'Published'

    class RepostType(models.TextChoices):
        REPOST = 'repost', 'Repost'
        QUOTE = 'quote', 'Quote'

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    body = models.TextField(blank=True)
    location = models.PointField(srid=4326, null=True, blank=True)
    # Dependencies
    reply_to = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    repost_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reposts')
    repost_type = models.CharField(max_length=10, null=True, blank=True, choices=RepostType.choices)
    community_note_of = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                                          related_name='community_notes')
    ballot = models.ForeignKey(Ballot, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    survey = models.ForeignKey(Survey, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    petition = models.ForeignKey(Petition, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    broadcast = models.ForeignKey(Broadcast, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    section = models.ForeignKey(Section, on_delete=models.PROTECT, null=True, blank=True, related_name='posts')
    tagged_users = models.ManyToManyField(User, blank=True, related_name='tagged_in_posts')
    hashtags = TaggableManager(blank=True, verbose_name="Hashtags")
    # User interaction
    likes = models.ManyToManyField(User, blank=True, through='PostLike', related_name='liked_posts')
    bookmarks = models.ManyToManyField(User, blank=True, related_name='bookmarked_posts')
    views = models.PositiveIntegerField(default=0)
    clicks = models.ManyToManyField(User, blank=True, through='PostClick', related_name='clicked_posts')
    is_muted = models.BooleanField(_('muted'), default=False)  # For muting conversations/threads
    is_pinned = models.BooleanField(_('pinned'), default=False)
    search_vector = SearchVectorField(null=True, blank=True)
    trending_vector = SearchVectorField(null=True, blank=True)
    # For community notes
    upvotes = models.ManyToManyField(User, blank=True, related_name='upvotes')
    downvotes = models.ManyToManyField(User, blank=True, related_name='downvotes')
    # Status
    status = models.CharField(max_length=10, choices=Status.choices, default='published')
    published_at = models.DateTimeField(default=timezone.now)
    # Deletion and deactivation
    is_deleted = models.BooleanField(_('deleted'), default=False)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        ordering = ['-published_at']
        db_table = 'Post'
        indexes = [
            GinIndex(fields=['search_vector']),
            GinIndex(fields=['trending_vector']),
        ]

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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Update vector after initial save
        Post.objects.filter(pk=self.pk).update(
            search_vector=SearchVector('body', config='english'),
            trending_vector=SearchVector('body', config='simple'),
        )

    def delete(self, *args, **kwargs):
        self.reposts.filter(repost_type=Post.RepostType.REPOST).delete()
        if self.reply_to is None:
            self.replies.all().delete()
        if self.reply_to is not None and self.replies.exists():
            return mark_deleted(self)
        if self.reposts.exists():
            return mark_deleted(self)
        if self.messages.exists():
            return mark_deleted(self)
        return super(Post, self).delete(*args, **kwargs)


class Asset(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='assets')

    # The actual path/key in the S3 bucket (e.g., "uploads/user_1/photo.jpg")
    file_key = models.CharField(max_length=512, unique=True)

    # Helpful metadata
    name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="Size in bytes")
    content_type = models.CharField(max_length=100, help_text="e.g., image/jpeg")
    is_completed = models.BooleanField(default=False)

    class Meta:
        db_table = 'PostAsset'

    def __str__(self):
        return self.name


class PostLike(models.Model):
    """Through model for Post likes with timestamp"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='liked_posts_through')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='likes_through')
    liked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ('user', 'post')
        ordering = ['-liked_at']
        db_table = 'PostLike'
        verbose_name = 'Post Like'
        verbose_name_plural = 'Post Likes'

    def __str__(self):
        return f"{self.user} liked post {self.post.id} at {self.liked_at}"


class PostClick(models.Model):
    """Through model for Post clicks with timestamp"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='clicked_posts_through')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='clicks_through')
    clicked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ('user', 'post')
        ordering = ['-clicked_at']
        db_table = 'PostClick'
        verbose_name = 'Post Click'
        verbose_name_plural = 'Post Clicks'

    def __str__(self):
        return f"{self.user} clicked post {self.post.id} at {self.clicked_at}"


class SearchHistory(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='search_history')
    search_term = models.CharField(max_length=255, null=True, blank=True)
    profile = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']
        db_table = 'SearchHistory'


class Report(BaseModel):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='reports')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports')
    issue = models.CharField(max_length=255)

    class Meta:
        db_table = 'Report'

    def __str__(self):
        return self.issue


@transaction.atomic
def mark_deleted(post: Post):
    post.body = ''
    post.ballot = None
    post.survey = None
    post.petition = None
    post.broadcast = None
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
    post.views = 0
    post.tagged_users.clear()
    post_save.send(sender=Post, instance=post, created=False)
    return post
