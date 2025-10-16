from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from rest_framework import serializers

from ballot.models import Ballot
from ballot.serializers import BallotSerializer
from constitution.models import Section
from constitution.serializers import SectionSerializer
from meet.models import Meeting
from meet.serializers import MeetingSerializer
from petition.models import Petition
from petition.serializers import PetitionSerializer
from posts.models import Post, Report
from survey.models import Survey
from survey.serializers import SurveySerializer
from users.serializers import UserSerializer

User = get_user_model()


class PostSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    likes = serializers.SerializerMethodField(read_only=True)
    is_liked = serializers.SerializerMethodField(read_only=True)
    bookmarks = serializers.SerializerMethodField(read_only=True)
    is_bookmarked = serializers.SerializerMethodField(read_only=True)
    replies = serializers.SerializerMethodField(read_only=True)
    reposts = serializers.SerializerMethodField(read_only=True)
    is_reposted = serializers.SerializerMethodField(read_only=True)
    is_quoted = serializers.SerializerMethodField(read_only=True)
    views = serializers.SerializerMethodField(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    meeting = MeetingSerializer(read_only=True)
    tagged_users = UserSerializer(read_only=True, many=True)
    tagged_sections = SectionSerializer(read_only=True, many=True)
    reply_to_id = serializers.IntegerField(write_only=True, allow_null=True)
    repost_of_id = serializers.IntegerField(write_only=True, allow_null=True)
    ballot_id = serializers.IntegerField(write_only=True, allow_null=True)
    survey_id = serializers.IntegerField(write_only=True, allow_null=True)
    petition_id = serializers.IntegerField(write_only=True, allow_null=True)
    meeting_id = serializers.IntegerField(write_only=True, allow_null=True)
    tags = serializers.ListField(write_only=True, allow_empty=True)  # Holds both @ and # tags

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
            'is_deleted',
            'is_active',
            'likes',
            'is_liked',
            'bookmarks',
            'is_bookmarked',
            'tagged_users',
            'tagged_sections',
            'tags',
            'views',
            'replies',
            'reposts',
            'is_reposted',
            'is_quoted',
            'reply_to',
            'repost_of',
            'ballot',
            'survey',
            'petition',
            'meeting',
            'reply_to_id',
            'repost_of_id',
            'ballot_id',
            'survey_id',
            'petition_id',
            'meeting_id',
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

    def get_is_reposted(self, obj):
        is_reposted = obj.reposts.filter(author=self.context['scope']['user'], body='').exists()
        return is_reposted

    def get_is_quoted(self, obj):
        is_quoted = obj.reposts.filter(author=self.context['scope']['user']).exclude(body='').exists()
        return is_quoted

    @staticmethod
    def get_views(obj):
        count = obj.views.count()
        return count

    def create(self, validated_data):
        validated_data['author'] = self.context['scope']['user']
        if validated_data['reply_to_id']:
            validated_data['reply_to'] = Post.objects.get(id=validated_data['reply_to_id'])
        if validated_data['repost_of_id']:
            repost_of = Post.objects.get(id=validated_data['repost_of_id'])
            # User can only have one repost of a post without body
            if validated_data['body'] == '':
                repost_of.reposts.filter(author=self.context['scope']['user'], body='', image1=None, video1=None,
                                         ballot=None, survey=None, petition=None, meeting=None).delete()
            validated_data['repost_of'] = repost_of
        if validated_data['ballot_id']:
            validated_data['ballot'] = Ballot.objects.get(id=validated_data['ballot_id'])
        if validated_data['survey_id']:
            validated_data['survey'] = Survey.objects.get(id=validated_data['survey_id'])
        if validated_data['petition_id']:
            validated_data['petition'] = Petition.objects.get(id=validated_data['petition_id'])
        if validated_data['meeting_id']:
            validated_data['meeting'] = Meeting.objects.get(id=validated_data['meeting_id'])
        tagged_users, tagged_sections = get_tagged(validated_data.pop('tags'))
        validated_data['tagged_users'] = tagged_users
        validated_data['tagged_sections'] = tagged_sections
        post = super().create(validated_data)
        if post.reply_to:
            post_save.send(sender=Post, instance=post.reply_to, created=False)
        if post.repost_of:
            post_save.send(sender=Post, instance=post.repost_of, created=False)
        return post

    def update(self, instance, validated_data):
        tagged_users, tagged_sections = get_tagged(validated_data.pop('tags'))
        # Save validated data to instance
        instance.body = validated_data.get('body', instance.body)
        instance.status = validated_data.get('status', instance.status)
        instance.tagged_users.set(tagged_users)
        instance.tagged_sections.set(tagged_sections)
        instance.save()
        return instance


def get_tagged(tags):
    tagged_users = []
    tagged_sections = []
    for tag in tags:
        user_qs = User.objects.filter(id=tag['id'], username=tag['text'])
        if user_qs.exists():
            tagged_users.append(user_qs.first())
        else:
            section_qs = Section.objects.filter(id=tag['id'], text=tag['text'])
            if section_qs.exists():
                tagged_sections.append(section_qs.first())
    return tagged_users, tagged_sections


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


class ThreadSerializer(PostSerializer):
    thread = serializers.SerializerMethodField(read_only=True)

    def get_thread(self, obj):
        author_replies = obj.replies.filter(author=obj.reply_to.author)
        serializer = ThreadSerializer(author_replies, many=True, context=self.context)
        return serializer.data

    class Meta(PostSerializer.Meta):
        fields = PostSerializer.Meta.fields + ('thread',)
