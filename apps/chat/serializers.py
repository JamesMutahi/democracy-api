import os

import filetype
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.db.models.signals import post_save
from drf_extra_fields.fields import Base64ImageField
from rest_framework import serializers

from apps.ballot.models import Ballot
from apps.ballot.serializers import BallotSerializer
from apps.chat.models import Message, Chat
from apps.meeting.models import Meeting
from apps.meeting.serializers import MeetingSerializer
from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.posts.models import Post
from apps.posts.serializers import PostSerializer
from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer
from apps.utils.base64_file_field import CustomBase64FileField
from apps.utils.link_extractor import extract_linked_object

User = get_user_model()


class MessageSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    post = PostSerializer(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    meeting = MeetingSerializer(read_only=True)
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
    image1 = serializers.SerializerMethodField()
    image2 = serializers.SerializerMethodField()
    image3 = serializers.SerializerMethodField()
    image4 = serializers.SerializerMethodField()
    file = serializers.SerializerMethodField()
    image1_base64 = Base64ImageField(write_only=True, required=False, allow_null=True, allow_empty_file=True)
    image2_base64 = Base64ImageField(write_only=True, required=False, allow_null=True, allow_empty_file=True)
    image3_base64 = Base64ImageField(write_only=True, required=False, allow_null=True, allow_empty_file=True)
    image4_base64 = Base64ImageField(write_only=True, required=False, allow_null=True, allow_empty_file=True)
    file_base64 = CustomBase64FileField(write_only=True, required=False, allow_null=True, allow_empty_file=True)
    file_name = serializers.CharField(
        write_only=True,
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=255
    )

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
            'petition',
            'meeting',
            'post_id',
            'ballot_id',
            'survey_id',
            'petition_id',
            'meeting_id',
            'image1',
            'image2',
            'image3',
            'image4',
            'image1_base64',
            'image2_base64',
            'image3_base64',
            'image4_base64',
            'file',
            'file_base64',
            'file_name',
            'location',
            'is_read',
            'is_edited',
            'is_deleted',
            'created_at',
            'updated_at'
        ]

    @staticmethod
    def get_image1(obj):
        if obj.image1:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.image1.url
        return None

    @staticmethod
    def get_image2(obj):
        if obj.image2:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.image2.url
        return None

    @staticmethod
    def get_image3(obj):
        if obj.image3:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.image3.url
        return None

    @staticmethod
    def get_image4(obj):
        if obj.image4:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.image4.url
        return None

    @staticmethod
    def get_file(obj):
        if obj.file:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.file.url
        return None

    def validate(self, attrs):
        if not attrs['chat'].users.contains(self.context['scope']['user']):
            raise serializers.ValidationError(detail='Not found')
        return super().validate(attrs)

    def create(self, validated_data):
        validated_data['user'] = self.context['scope']['user']
        if validated_data['post_id']:
            validated_data['post'] = validated_data.pop('post_id')
        if validated_data['ballot_id']:
            validated_data['ballot'] = validated_data.pop('ballot_id')
        if validated_data['survey_id']:
            validated_data['survey'] = validated_data.pop('survey_id')
        if validated_data['petition_id']:
            validated_data['petition'] = validated_data.pop('petition_id')
        if validated_data['meeting_id']:
            validated_data['meeting'] = validated_data.pop('meeting_id')

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

        # Handle files
        if 'image1_base64' in validated_data:
            validated_data['image1'] = validated_data.pop('image1_base64')
        if 'image2_base64' in validated_data:
            validated_data['image2'] = validated_data.pop('image2_base64')
        if 'image3_base64' in validated_data:
            validated_data['image3'] = validated_data.pop('image3_base64')
        if 'image4_base64' in validated_data:
            validated_data['image4'] = validated_data.pop('image4_base64')

        # Change file name
        file_obj = validated_data.pop('file_base64', None)
        file_name = validated_data.pop('file_name', None)

        if file_obj and file_name:
            # Optional: append extension if missing (recommended)
            detected_ext = filetype.guess_extension(file_obj.read())
            file_obj.seek(0)
            if detected_ext:
                base, ext = os.path.splitext(file_name)
                if not ext or ext.lower() == '.':
                    file_name = f"{base}.{detected_ext}"

            file_obj.name = file_name

        if file_obj:
            validated_data['file'] = file_obj

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
