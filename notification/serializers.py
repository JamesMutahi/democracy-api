from rest_framework import serializers

from ballot.serializers import BallotSerializer
from chat.serializers import ChatSerializer, MessageSerializer
from notification.models import Notification, Preferences
from petition.serializers import PetitionSerializer
from posts.serializers import PostSerializer
from survey.serializers import SurveySerializer


class NotificationSerializer(serializers.ModelSerializer):
    post = PostSerializer(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    chat = ChatSerializer(read_only=True)
    message = MessageSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'text',
            'is_read',
            'post',
            'ballot',
            'survey',
            'petition',
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
            'allow_follow_notifications',
            'allow_like_notifications',
            'allow_tag_notifications',
            'allow_reply_notifications',
            'allow_repost_notifications',
            'allow_message_notifications',
        ]
