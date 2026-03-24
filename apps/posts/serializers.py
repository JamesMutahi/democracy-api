from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.db.models.signals import post_save
from django.utils import timezone
from drf_extra_fields.fields import Base64ImageField
from rest_framework import serializers

from apps.ballot.models import Ballot
from apps.ballot.serializers import BallotSerializer
from apps.meeting.models import Meeting
from apps.meeting.serializers import MeetingSerializer
from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.posts.models import Post, Report
from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer
from apps.utils.base64_file_field import CustomBase64FileField
from apps.utils.link_extractor import extract_linked_object

User = get_user_model()


class PostSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    published_at = serializers.DateTimeField(default=timezone.now, read_only=True)
    likes = serializers.SerializerMethodField(read_only=True)
    is_liked = serializers.SerializerMethodField(read_only=True)
    bookmarks = serializers.SerializerMethodField(read_only=True)
    is_bookmarked = serializers.SerializerMethodField(read_only=True)
    replies = serializers.SerializerMethodField(read_only=True)
    reposts = serializers.SerializerMethodField(read_only=True)
    is_reposted = serializers.SerializerMethodField(read_only=True)
    is_quoted = serializers.SerializerMethodField(read_only=True)
    views = serializers.SerializerMethodField(read_only=True)
    is_viewed = serializers.SerializerMethodField(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    meeting = MeetingSerializer(read_only=True)
    tagged_users = UserSerializer(read_only=True, many=True)
    reply_to_id = serializers.IntegerField(write_only=True, allow_null=True)
    repost_of_id = serializers.PrimaryKeyRelatedField(
        queryset=Post.objects.all(),
        write_only=True,
        required=False,
        allow_null=True
    )
    community_note_of_id = serializers.PrimaryKeyRelatedField(
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
    tags = serializers.ListField(write_only=True, allow_empty=True)  # Holds both @ and # tags
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
    community_note = serializers.SerializerMethodField(read_only=True)
    is_upvoted = serializers.SerializerMethodField(read_only=True)
    is_downvoted = serializers.SerializerMethodField(read_only=True)
    upvotes = serializers.SerializerMethodField(read_only=True)
    downvotes = serializers.SerializerMethodField(read_only=True)

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
            'video1',
            'video2',
            'video3',
            'image1_base64',
            'image2_base64',
            'image3_base64',
            'image4_base64',
            'file',
            'file_base64',
            'file_name',
            'location',
            'is_deleted',
            'is_active',
            'likes',
            'is_liked',
            'bookmarks',
            'is_bookmarked',
            'tagged_users',
            'tags',
            'views',
            'is_viewed',
            'replies',
            'reposts',
            'is_reposted',
            'is_quoted',
            'reply_to',
            'repost_of',
            'community_note_of',
            'ballot',
            'survey',
            'petition',
            'meeting',
            'community_note',
            'is_upvoted',
            'is_downvoted',
            'upvotes',
            'downvotes',
            'reply_to_id',
            'repost_of_id',
            'community_note_of_id',
            'ballot_id',
            'survey_id',
            'petition_id',
            'meeting_id',
        )

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

    def get_fields(self):
        fields = super(PostSerializer, self).get_fields()
        fields['reply_to'] = PostSerializer(read_only=True)
        fields['repost_of'] = PostSerializer(read_only=True)
        fields['community_note_of'] = PostSerializer(read_only=True)
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
        return obj.get_reposts_count()

    def get_is_reposted(self, obj):
        is_reposted = obj.reposts.filter(author=self.context['scope']['user'], reply_to=None, body='').exists()
        return is_reposted

    def get_is_quoted(self, obj):
        is_quoted = obj.reposts.filter(author=self.context['scope']['user'], reply_to=None).exclude(body='').exists()
        return is_quoted

    @staticmethod
    def get_views(obj):
        count = obj.views.count()
        return count

    def get_is_viewed(self, obj):
        is_viewed = obj.views.contains(self.context['scope']['user'])
        return is_viewed

    @staticmethod
    def get_community_note(obj: Post):
        return obj.get_top_note()

    def get_is_upvoted(self, obj):
        is_upvoted = obj.upvotes.contains(self.context['scope']['user'])
        return is_upvoted

    def get_is_downvoted(self, obj):
        is_downvoted = obj.downvotes.contains(self.context['scope']['user'])
        return is_downvoted

    @staticmethod
    def get_upvotes(obj):
        count = 0
        if obj.community_note_of:
            count = obj.upvotes.count()
        return count

    @staticmethod
    def get_downvotes(obj):
        count = 0
        if obj.community_note_of:
            count = obj.downvotes.count()
        return count

    def create(self, validated_data):
        validated_data['author'] = self.context['scope']['user']
        if validated_data['reply_to_id']:
            validated_data['reply_to'] = Post.objects.get(id=validated_data['reply_to_id'])
        if validated_data['repost_of_id']:
            # Author can only have one repost of a post without body
            if validated_data['body'] == '':
                validated_data['repost_of_id'].reposts.filter(author=self.context['scope']['user'], body='',
                                                              reply_to=None, community_note_of=None,
                                                              image1=None, video1=None,
                                                              ballot=None, survey=None, petition=None,
                                                              meeting=None).delete()
            validated_data['repost_of'] = validated_data.pop('repost_of_id')
        if validated_data['community_note_of_id']:
            validated_data['community_note_of'] = validated_data.pop('community_note_of_id')
        if validated_data['ballot_id']:
            validated_data['ballot'] = validated_data.pop('ballot_id')
        if validated_data['survey_id']:
            validated_data['survey'] = validated_data.pop('survey_id')
        if validated_data['petition_id']:
            validated_data['petition'] = validated_data.pop('petition_id')
        if validated_data['meeting_id']:
            validated_data['meeting'] = validated_data.pop('meeting_id')

        validated_data['tagged_users'] = get_tagged(validated_data.pop('tags'))

        # Extract object if link is present in post body
        linked_object = extract_linked_object(text=validated_data['body'])
        if linked_object:
            if isinstance(linked_object, Post) and not validated_data.get('repost_of'):
                validated_data['repost_of_id'] = linked_object.pk
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

        file_obj = validated_data.pop('file_base64', None)
        file_name = validated_data.pop('file_name', None)

        # Change file name
        if file_name:
            file_obj.name = file_name

        if file_obj:
            validated_data['file'] = file_obj

        # Calling create method with new validated data
        post = super().create(validated_data)
        if post.reply_to:
            post_save.send(sender=Post, instance=post.reply_to, created=False)
        if post.repost_of:
            post_save.send(sender=Post, instance=post.repost_of, created=False)
        return post

    def update(self, instance, validated_data):
        tagged_users = get_tagged(validated_data.pop('tags'))
        # Save validated data to instance
        instance.body = validated_data.get('body', instance.body)
        instance.status = validated_data.get('status', instance.status)
        instance.published_at = timezone.now()
        instance.tagged_users.set(tagged_users)
        instance.save()
        return instance


def get_tagged(tags):
    tagged_users = []
    for tag in tags:
        user_qs = User.objects.filter(id=tag['id'], username=tag['text'])
        if user_qs.exists():
            tagged_users.append(user_qs.first())
    return tagged_users


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
        if obj.reply_to:
            author_replies = obj.replies.filter(author=obj.reply_to.author)
        else:
            author_replies = obj.replies.filter(author=obj.author)
        serializer = ThreadSerializer(author_replies, many=True, context=self.context)
        return serializer.data

    class Meta(PostSerializer.Meta):
        fields = PostSerializer.Meta.fields + ('thread',)
