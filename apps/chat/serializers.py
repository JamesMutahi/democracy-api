from django.contrib.auth import get_user_model
from django.db.models import Count
from django.db.models.signals import post_save
from rest_framework import serializers

from apps.ballot.models import Ballot
from apps.ballot.serializers import BallotSerializer
from apps.chat.models import Message, Chat, Asset
from apps.constitution.models import Section
from apps.constitution.serializers import SectionSerializer
from apps.meeting.models import Meeting
from apps.meeting.serializers import MeetingSerializer
from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.posts.models import Post
from apps.posts.serializers import PostSerializer
from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer
from apps.utils.link_extractor import extract_linked_object

User = get_user_model()


class AssetSerializer(serializers.ModelSerializer):
    # model @property
    url = serializers.ReadOnlyField()

    class Meta:
        model = Asset
        fields = [
            'id',
            'name',
            'file_size',
            'content_type',
            'url',  # External S3 URL for the frontend
            'is_completed',  # Status of the upload
            'uploaded_at'
        ]
        # Prevents the frontend from trying to overwrite the S3 path
        read_only_fields = ['id', 'file_key', 'is_completed', 'uploaded_at']


class MessageSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    post = PostSerializer(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    meeting = MeetingSerializer(read_only=True)
    section = SectionSerializer(read_only=True)
    post_id = serializers.PrimaryKeyRelatedField(
        queryset=Post.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    ballot_id = serializers.PrimaryKeyRelatedField(
        queryset=Ballot.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    survey_id = serializers.PrimaryKeyRelatedField(
        queryset=Survey.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    petition_id = serializers.PrimaryKeyRelatedField(
        queryset=Petition.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    meeting_id = serializers.PrimaryKeyRelatedField(
        queryset=Meeting.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    section_id = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    asset = AssetSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            'id',
            'chat',
            'author',
            'text',
            'post',
            'ballot',
            'survey',
            'petition',
            'meeting',
            'section',
            'post_id',
            'ballot_id',
            'survey_id',
            'petition_id',
            'meeting_id',
            'section_id',
            'location',
            'asset',
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
        validated_data['author'] = self.context['scope']['user']
        if validated_data.get('post_id'):
            validated_data['post'] = validated_data.pop('post_id')
        if validated_data.get('ballot_id'):
            validated_data['ballot'] = validated_data.pop('ballot_id')
        if validated_data.get('survey_id'):
            validated_data['survey'] = validated_data.pop('survey_id')
        if validated_data.get('petition_id'):
            validated_data['petition'] = validated_data.pop('petition_id')
        if validated_data.get('meeting_id'):
            validated_data['meeting'] = validated_data.pop('meeting_id')
        if validated_data.get('section_id'):
            validated_data['section'] = validated_data.pop('section_id')

        # Extract object if link is present in message text
        linked_object = extract_linked_object(text=validated_data['text'])
        if linked_object:
            if isinstance(linked_object, Post) and not validated_data.get('post'):
                validated_data['post_id'] = linked_object.pk
            if isinstance(linked_object, Ballot) and not validated_data.get('ballot'):
                validated_data['ballot_id'] = linked_object.pk
            if isinstance(linked_object, Survey) and not validated_data.get('survey'):
                validated_data['survey_id'] = linked_object.pk
            if isinstance(linked_object, Petition) and not validated_data.get('petition'):
                validated_data['petition_id'] = linked_object.pk
            if isinstance(linked_object, Meeting) and not validated_data.get('meeting'):
                validated_data['meeting_id'] = linked_object.pk
            if isinstance(linked_object, Section) and not validated_data.get('section'):
                validated_data['section_id'] = linked_object.pk

        message = super().create(validated_data)
        post_save.send(sender=Chat, instance=message.chat, created=False)
        return message


class ChatSerializer(serializers.ModelSerializer):
    users = UserSerializer(many=True, read_only=True)
    user = serializers.IntegerField(write_only=True)
    last_message = serializers.SerializerMethodField(read_only=True)
    unread_messages = serializers.SerializerMethodField(read_only=True)
    is_self_chat = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Chat
        fields = ['id', 'users', 'last_message', 'unread_messages', 'user', 'is_self_chat']
        read_only_fields = ['last_message']

    def get_last_message(self, obj: Chat):
        if obj.messages.exists():
            serializer = MessageSerializer(obj.messages.order_by('created_at').last(), context=self.context)
            return serializer.data
        else:
            return None

    def get_unread_messages(self, instance: Chat):
        return instance.messages.filter(is_read=False).exclude(author=self.context['scope']['user']).count()

    @staticmethod
    def get_is_self_chat(obj: Chat):
        return obj.users.count() == 1

    def create(self, validated_data):
        user = User.objects.get(id=validated_data.pop('user'))
        validated_data['users'] = [self.context['scope']['user'], user]
        chat = get_or_create_direct_chat(self.context['scope']['user'], user)
        return chat


def get_or_create_direct_chat(user1, user2):
    """
    Returns (or creates) a Chat for 1:1 or self-chat.
    For self-chat: chat contains only 1 user.
    For normal DM: chat contains exactly 2 users.
    """
    # Efficient query: find chat containing both users with correct count
    num_users = 1 if user1.id == user2.id else 2

    chat = Chat.objects.annotate(
        num_users=Count('users', distinct=True)
    ).filter(
        users=user1
    ).filter(
        users=user2
    ).filter(
        num_users=num_users
    ).first()

    if not chat:
        chat = Chat.objects.create()
        if user1.id == user2.id:
            chat.users.add(user1)  # Self-chat: only one user
        else:
            chat.users.add(user1, user2)  # Normal 1:1 chat

    return chat
