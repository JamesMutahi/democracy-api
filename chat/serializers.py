from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from rest_framework import serializers

from chat.models import Message, Chat
from ballot.models import Ballot
from ballot.serializers import BallotSerializer
from posts.models import Post
from posts.serializers import PostSerializer
from survey.models import Survey
from survey.serializers import SurveySerializer
from users.serializers import UserSerializer

User = get_user_model()


class MessageSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    post = PostSerializer(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    post_id = serializers.IntegerField(write_only=True, allow_null=True)
    ballot_id = serializers.IntegerField(write_only=True, allow_null=True)
    survey_id = serializers.IntegerField(write_only=True, allow_null=True)

    class Meta:
        model = Message
        fields = [
            'id',
            'chat',
            'user',
            'text',
            'post',
            'ballot',
            'survey',
            'post_id',
            'ballot_id',
            'survey_id',
            'is_read',
            'is_edited',
            'is_deleted',
            'created_at',
            'updated_at'
        ]

    def validate(self, attrs):
        if not attrs['chat'].users.contains(self.context['scope']['user']):
            raise serializers.ValidationError(detail='Not found')
        return super().validate(attrs)

    def create(self, validated_data):
        validated_data['user'] = self.context['scope']['user']
        if validated_data['post_id'] is not None:
            validated_data['post'] = Post.objects.get(id=validated_data['post_id'])
        if validated_data['ballot_id'] is not None:
            validated_data['ballot'] = Ballot.objects.get(id=validated_data['ballot_id'])
        if validated_data['survey_id'] is not None:
            validated_data['survey'] = Survey.objects.get(id=validated_data['survey_id'])
        message = super().create(validated_data)
        post_save.send(sender=Chat, instance=message.chat, created=False)
        return message


class ChatSerializer(serializers.ModelSerializer):
    users = UserSerializer(many=True, read_only=True)
    user = serializers.IntegerField(write_only=True)
    last_message = serializers.SerializerMethodField(read_only=True)
    unread_messages = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Chat
        fields = ['id', 'users', 'last_message', 'unread_messages', 'user']
        read_only_fields = ['last_message']

    def get_last_message(self, obj: Chat):
        if obj.messages.exists():
            serializer = MessageSerializer(obj.messages.order_by('created_at').last(), context=self.context)
            return serializer.data
        else:
            return None

    def get_unread_messages(self, instance: Chat):
        return instance.messages.filter(is_read=False).exclude(user=self.context['scope']['user']).count()

    def create(self, validated_data):
        user = User.objects.get(id=validated_data.pop('user'))
        validated_data['users'] = [self.context['scope']['user'], user]
        return super().create(validated_data)
