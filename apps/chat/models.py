from django.contrib.auth import get_user_model
from django.contrib.gis.db import models
from django.db.models import Manager, Max, Q, Exists, OuterRef
from django.utils.translation import gettext_lazy as _

from apps.ballot.models import Ballot
from apps.constitution.models import Section
from apps.meeting.models import Meeting
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ChatQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(users=user)

    def with_latest_message(self):
        return self.annotate(latest_message_id=Max('messages__id'))

    def search_by_other_user(self, user, search_term: str):
        if not search_term:
            return self
        search_term = search_term.strip().lower()

        other_user_match = User.objects.filter(
            chats=OuterRef('pk')
        ).exclude(id=user.id).filter(
            Q(username__icontains=search_term) |
            Q(name__icontains=search_term) if hasattr(User, 'name') else Q()
        )

        return self.annotate(
            has_matching_user=Exists(other_user_match)
        ).filter(has_matching_user=True)


class ChatManager(Manager.from_queryset(ChatQuerySet)):
    pass


class Chat(BaseModel):
    users = models.ManyToManyField(User, related_name="chats")
    objects = ChatManager()

    class Meta:
        db_table = 'Chat'

    def __str__(self):
        return f"Chat({self.id})"


class UploadImageTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/messages/{}'.format(instance.user.id, filename)

    def deconstruct(self):
        return 'apps.chat.models.UploadImageTo', [self.name], {}


class UploadVideoTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/messages/{}'.format(instance.user.id, filename)

    def deconstruct(self):
        return 'apps.chat.models.UploadVideoTo', [self.name], {}


class UploadFileTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/messages/{}'.format(instance.user.id, filename)

    def deconstruct(self):
        return 'apps.chat.models.UploadFileTo', [self.name], {}


class Message(BaseModel):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="messages")
    text = models.TextField(max_length=500, blank=True)
    post = models.ForeignKey(Post, on_delete=models.PROTECT, null=True, blank=True, related_name='messages')
    ballot = models.ForeignKey(Ballot, on_delete=models.PROTECT, null=True, blank=True, related_name='messages')
    survey = models.ForeignKey(Survey, on_delete=models.PROTECT, null=True, blank=True, related_name='messages')
    petition = models.ForeignKey(Petition, on_delete=models.PROTECT, null=True, blank=True, related_name='messages')
    meeting = models.ForeignKey(Meeting, on_delete=models.PROTECT, null=True, blank=True, related_name='messages')
    section = models.ForeignKey(Section, on_delete=models.PROTECT, null=True, blank=True, related_name='messages')
    image1 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    image2 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    image3 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    image4 = models.ImageField(upload_to=UploadImageTo('images/'), null=True, blank=True)
    video = models.FileField(upload_to=UploadVideoTo('videos/'), null=True, blank=True)
    file = models.FileField(upload_to=UploadFileTo('files/'), null=True, blank=True)
    location = models.PointField(srid=4326, null=True)
    is_read = models.BooleanField(_('read'), default=False)
    is_edited = models.BooleanField(_('edited'), default=False)
    is_deleted = models.BooleanField(_('deleted'), default=False)

    class Meta:
        db_table = 'Message'
        ordering = ['-created_at']

    def __str__(self):
        return f"Message({self.author.username} {self.chat})"
