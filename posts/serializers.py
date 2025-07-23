from django.contrib.auth import get_user_model
from rest_framework import serializers

from poll.models import Poll
from poll.serializers import PollSerializer
from posts.models import Post, Report
from survey.models import Survey
from survey.serializers import SurveySerializer
from users.serializers import UserSerializer

User = get_user_model()


class PostSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    likes = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    bookmarks = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    replies = serializers.SerializerMethodField()
    reposts = serializers.SerializerMethodField()
    views = serializers.SerializerMethodField()
    poll = PollSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    tagged_users = UserSerializer(read_only=True, many=True)
    reply_to_id = serializers.IntegerField(write_only=True, allow_null=True)
    repost_of_id = serializers.IntegerField(write_only=True, allow_null=True)
    poll_id = serializers.IntegerField(write_only=True, allow_null=True)
    survey_id = serializers.IntegerField(write_only=True, allow_null=True)
    tagged_user_ids = serializers.ListField(write_only=True, allow_empty=True)

    class Meta:
        model = Post
        fields = (
            'id',
            'author',
            'status',
            'published_at',
            'body',
            'image1',
            'image2',
            'image3',
            'image4',
            'image5',
            'image6',
            'video1',
            'video2',
            'video3',
            'is_edited',
            'is_deleted',
            'is_active',
            'likes',
            'is_liked',
            'bookmarks',
            'is_bookmarked',
            'tagged_users',
            'tagged_user_ids',
            'views',
            'replies',
            'reposts',
            'reply_to',
            'repost_of',
            'poll',
            'survey',
            'reply_to_id',
            'repost_of_id',
            'poll_id',
            'survey_id',
        )

    def get_fields(self):
        fields = super(PostSerializer, self).get_fields()
        fields['reply_to'] = PostSerializer(read_only=True)
        fields['repost_of'] = PostSerializer(read_only=True)
        return fields

    @staticmethod
    def get_likes(obj):
        count = obj.likes.count()
        return count

    def get_is_liked(self, obj):
        is_liked = obj.likes.contains(self.context['scope']['user'])
        return is_liked

    @staticmethod
    def get_bookmarks(obj):
        count = obj.bookmarks.count()
        return count

    def get_is_bookmarked(self, obj):
        is_bookmarked = obj.bookmarks.contains(self.context['scope']['user'])
        return is_bookmarked

    @staticmethod
    def get_replies(obj):
        count = obj.replies.count()
        return count

    @staticmethod
    def get_reposts(obj):
        count = obj.reposts.count()
        return count

    @staticmethod
    def get_views(obj):
        count = obj.views.count()
        return count

    def create(self, validated_data):
        validated_data['author'] = self.context['scope']['user']
        if validated_data['reply_to_id'] is not None:
            validated_data['reply_to'] = Post.objects.get(id=validated_data['reply_to_id'])
        if validated_data['repost_of_id'] is not None:
            validated_data['repost_of'] = Post.objects.get(id=validated_data['repost_of_id'])
        if validated_data['poll_id'] is not None:
            validated_data['poll'] = Poll.objects.get(id=validated_data['poll_id'])
        if validated_data['survey_id'] is not None:
            validated_data['survey'] = Survey.objects.get(id=validated_data['survey_id'])
        tagged_user_ids = validated_data.pop('tagged_user_ids')
        validated_data['tagged_users'] = []
        for tagged_user_id in tagged_user_ids:
            user = User.objects.get(id=tagged_user_id)
            validated_data['tagged_users'].append(user)
        return super().create(validated_data)


class ReportSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = Report
        fields = (
            'id',
            'post',
            'user',
            'issue',
        )

    def create(self, validated_data):
        validated_data['user'] = self.context['scope']['user']
        return super().create(validated_data)
