from django.contrib.auth import get_user_model
from django.db import models

from ballot.models import Ballot
from chat.models import Chat, Message
from meet.models import Meeting
from petition.models import Petition
from posts.models import Post
from survey.models import Survey

User = get_user_model()


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    text = models.TextField()
    is_read = models.BooleanField(default=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, null=True, blank=True)
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, null=True, blank=True)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, null=True, blank=True)
    petition = models.ForeignKey(Petition, on_delete=models.CASCADE, null=True, blank=True)
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, null=True, blank=True)
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, null=True, blank=True)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'Notification'
        ordering = ['-id']

    def __str__(self):
        return self.text


class Preferences(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    allowed_users = models.ManyToManyField(User, blank=True, related_name='followers_notified')  # Notification bell
    allow_notifications = models.BooleanField(default=True)
    allow_follow_notifications = models.BooleanField(default=True)
    allow_tag_notifications = models.BooleanField(default=True)
    allow_like_notifications = models.BooleanField(default=True)
    allow_reply_notifications = models.BooleanField(default=True)
    allow_repost_notifications = models.BooleanField(default=True)
    allow_message_notifications = models.BooleanField(default=True)
    muted_posts = models.ManyToManyField(Post, blank=True)  # For muting conversations/threads

    class Meta:
        db_table = 'Preferences'

    def __str__(self):
        return self.user.username
