from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import ExpressionWrapper, Count, F, FloatField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ballot.models import Ballot
from constitution.models import Section
from meet.models import Meeting
from petition.models import Petition
from survey.models import Survey

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


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
    video1 = models.FileField(upload_to='posts/videos/', null=True, blank=True)
    video2 = models.FileField(upload_to='posts/videos/', null=True, blank=True)
    video3 = models.FileField(upload_to='posts/videos/', null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='published')
    published_at = models.DateTimeField(default=timezone.now)
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='replies')
    repost_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='reposts')
    community_note_of = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name='community_notes')
    ballot = models.ForeignKey(Ballot, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    survey = models.ForeignKey(Survey, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    petition = models.ForeignKey(Petition, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    meeting = models.ForeignKey(Meeting, on_delete=models.SET_NULL, null=True, blank=True, related_name='posts')
    likes = models.ManyToManyField(User, blank=True, related_name='liked_posts')
    bookmarks = models.ManyToManyField(User, blank=True, related_name='bookmarked_posts')
    views = models.ManyToManyField(User, blank=True, related_name='viewed_posts')
    tagged_users = models.ManyToManyField(User, blank=True, related_name='tagged_in_posts')
    tagged_sections = models.ManyToManyField(Section, blank=True, related_name='tagged_posts')

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
            helpful_score=ExpressionWrapper(
                F('upvotes_count') * 1.0 / (F('upvotes_count') + F('downvotes_count')),
                output_field=FloatField()
            )).filter(helpful_score__gt=0.7).order_by(
            '-helpful_score',
            '-upvotes_count',
            '-downvotes_count',
            '-created_at'
        ).first()
        if not top_note:
            return ''
        return top_note.body


class Report(BaseModel):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='reports')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports')
    issue = models.CharField(max_length=255)

    class Meta:
        db_table = 'Report'

    def __str__(self):
        return self.issue
