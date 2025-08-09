from rest_framework import serializers

from chat.serializers import ChatSerializer, MessageSerializer
from notification.models import Notification, Preferences
from poll.serializers import PollSerializer
from posts.serializers import PostSerializer
from survey.serializers import SurveySerializer


class NotificationSerializer(serializers.ModelSerializer):
    post = PostSerializer(read_only=True)
    poll = PollSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    chat = ChatSerializer(read_only=True)
    message = MessageSerializer(read_only=True)

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
            'message',
            'created_at',
        ]
        read_only_fields = ['text']


class PreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Preferences
        fields = [
            'allow_notifications',
            'follow_notifications',
            'like_notifications',
            'tag_notifications',
            'reply_notifications',
            'repost_notifications',
            'message_notifications',
        ]
