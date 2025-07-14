from rest_framework import serializers

from chat.serializers import ChatSerializer, MessageSerializer
from notification.models import Notification
from poll.serializers import PollSerializer
from posts.serializers import PostSerializer
from survey.serializers import SurveySerializer


class NotificationSerializer(serializers.ModelSerializer):
    post = PostSerializer(read_only=True)
    poll = PollSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    chat = ChatSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'text',
            'is_read',
            'post',
            'poll',
            'survey',
            'chat',
            'created_at',
        ]
        read_only_fields = ['text']
