from rest_framework import serializers

from apps.ballot.serializers import BallotSerializer
from apps.chat.serializers import ChatSerializer, MessageSerializer
from apps.meeting.serializers import MeetingSerializer
from apps.notification.models import Notification, Preferences
from apps.petition.serializers import PetitionSerializer
from apps.posts.serializers import PostSerializer
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer


class NotificationSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    post = PostSerializer(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    meeting = MeetingSerializer(read_only=True)
    chat = ChatSerializer(read_only=True)
    message = MessageSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'text',
            'is_read',
            'user',
            'post',
            'ballot',
            'survey',
            'petition',
            'meeting',
            'chat',
            'message',
            'created_at',
        ]


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
