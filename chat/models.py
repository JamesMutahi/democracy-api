from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Chat(BaseModel):
    users = models.ManyToManyField(User, related_name="chats")

    class Meta:
        db_table = 'Chat'

    def __str__(self):
        return f"Chat({self.id})"


class Message(BaseModel):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="messages")
    text = models.TextField(max_length=500)
    is_read = models.BooleanField(_('read'), default=False)
    is_edited = models.BooleanField(_('edited'), default=False)
    is_deleted = models.BooleanField(_('deleted'), default=False)

    class Meta:
        db_table = 'Message'
        ordering = ['-created_at']

    def __str__(self):
        return f"Message({self.user} {self.chat})"
