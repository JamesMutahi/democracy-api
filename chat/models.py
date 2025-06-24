from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Room(BaseModel):
    users = models.ManyToManyField(User, related_name="rooms", blank=True)

    class Meta:
        db_table = 'Room'

    def __str__(self):
        return f"Room({self.id})"


class Message(BaseModel):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="messages")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="messages")
    text = models.TextField(max_length=500)

    class Meta:
        db_table = 'Message'

    def __str__(self):
        return f"Message({self.user} {self.room})"
