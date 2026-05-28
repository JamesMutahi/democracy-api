import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.utils import timezone
from rest_framework import serializers
from taggit.serializers import TagListSerializerField

from apps.ballot.models import Ballot
from apps.ballot.serializers import BallotSerializer
from apps.constitution.models import Section
from apps.constitution.serializers import SectionSerializer
from apps.meeting.models import Meeting
from apps.meeting.serializers import MeetingSerializer
from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.posts.models import Post, Report, PostLike, Asset
from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer
from apps.utils.link_extractor import extract_linked_object
from apps.utils.presigned_url import s3_client

User = get_user_model()


class TagSerializer(serializers.Serializer):
    id = serializers.CharField()
    text = serializers.CharField()


class AssetSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            'id',
            'name',
            'file_key',
            'file_size',
            'content_type',
            'url',  # External S3 URL for the frontend
            'is_completed',  # Status of the upload
            'created_at',
        ]
        # Prevents the frontend from trying to overwrite the S3 path
        read_only_fields = ['id', 'file_key', 'is_completed', 'created_at']

    @staticmethod
    def get_url(obj):
        if not obj.file_key:
            return None

        # Generates a temporary GET link
        return s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': obj.file_key
            },
            ExpiresIn=3600
        )


class PostSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    published_at = serializers.DateTimeField(default=timezone.now, read_only=True)
    body = serializers.SerializerMethodField()
    likes = serializers.SerializerMethodField(read_only=True)
    is_liked = serializers.SerializerMethodField(read_only=True)
    bookmarks = serializers.SerializerMethodField(read_only=True)
    is_bookmarked = serializers.SerializerMethodField(read_only=True)
    replies = serializers.SerializerMethodField(read_only=True)
    reposts = serializers.SerializerMethodField(read_only=True)
    is_reposted = serializers.SerializerMethodField(read_only=True)
    is_quoted = serializers.SerializerMethodField(read_only=True)
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    meeting = MeetingSerializer(read_only=True)
    section = SectionSerializer(read_only=True)
    tagged_users = UserSerializer(read_only=True, many=True)
    hashtags = TagListSerializerField(required=False, allow_null=True)
    reply_to_id = serializers.PrimaryKeyRelatedField(
        queryset=Post.objects.all(),
        source='reply_to',
        write_only=True,
        required=False,
        allow_null=True
    )
    repost_of_id = serializers.PrimaryKeyRelatedField(
        queryset=Post.objects.all(),
        source='repost_of',
        write_only=True,
        required=False,
        allow_null=True
    )
    community_note_of_id = serializers.PrimaryKeyRelatedField(
        queryset=Post.objects.all(),
        source='community_note_of',
        write_only=True,
        required=False,
        allow_null=True
    )
    ballot_id = serializers.PrimaryKeyRelatedField(
        queryset=Ballot.objects.all(),
        source='ballot',
        write_only=True,
        required=False,
        allow_null=True
    )
    survey_id = serializers.PrimaryKeyRelatedField(
        queryset=Survey.objects.all(),
        source='survey',
        write_only=True,
        required=False,
        allow_null=True
    )
    petition_id = serializers.PrimaryKeyRelatedField(
        queryset=Petition.objects.all(),
        source='petition',
        write_only=True,
        required=False,
        allow_null=True
    )
    meeting_id = serializers.PrimaryKeyRelatedField(
        queryset=Meeting.objects.all(),
        source='meeting',
        write_only=True,
        required=False,
        allow_null=True
    )
    section_id = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.all(),
        source='section',
        write_only=True,
        required=False,
        allow_null=True
    )
    tags = TagSerializer(many=True, required=False, allow_null=True)
    community_note = serializers.SerializerMethodField(read_only=True)
    is_upvoted = serializers.SerializerMethodField(read_only=True)
    is_downvoted = serializers.SerializerMethodField(read_only=True)
    upvotes = serializers.SerializerMethodField(read_only=True)
    downvotes = serializers.SerializerMethodField(read_only=True)
    assets = AssetSerializer(many=True, default=[])

    class Meta:
        model = Post
        fields = (
            'id',
            'author',
            'status',
            'published_at',
            'body',
            'location',
            'is_deleted',
            'is_active',
            'likes',
            'is_liked',
            'bookmarks',
            'is_bookmarked',
            'tagged_users',
            'tags',
            'hashtags',
            'views',
            'is_muted',
            'replies',
            'reposts',
            'is_reposted',
            'is_quoted',
            'reply_to',
            'repost_of',
            'repost_type',
            'community_note_of',
            'ballot',
            'survey',
            'petition',
            'meeting',
            'section',
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
            'section_id',
            'assets',
        )
        extra_kwargs = {'is_active': {'read_only': True}}

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        if 'body' in data:
            ret['body'] = data['body']
        return ret

    def get_fields(self):
        fields = super(PostSerializer, self).get_fields()
        fields['reply_to'] = PostSerializer(read_only=True)
        fields['repost_of'] = PostSerializer(read_only=True)
        fields['community_note_of'] = PostSerializer(read_only=True)
        return fields

    @staticmethod
    def get_body(obj):
        # Use highlighted version if available (from search)
        if hasattr(obj, 'highlighted_body'):
            return obj.highlighted_body
        return obj.body

    @staticmethod
    def get_likes(obj):
        count = obj.likes.count()
        return count

    def get_is_liked(self, post):
        is_liked = PostLike.objects.filter(user=self.context['scope']['user'], post=post).exists()
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
        count = obj.replies.filter(is_active=True, status='published').count()
        return count

    @staticmethod
    def get_reposts(obj):
        return obj.get_reposts_count()

    def get_is_reposted(self, obj):
        is_reposted = obj.reposts.filter(is_active=True, author=self.context['scope']['user'], reply_to=None,
                                         body='').exists()
        return is_reposted

    def get_is_quoted(self, obj):
        is_quoted = obj.reposts.filter(is_active=True, author=self.context['scope']['user'], reply_to=None).exclude(
            body='').exists()
        return is_quoted

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
        if validated_data.get('repost_of_id'):
            # Author can only have one repost of a post without body or relevant fields
            if validated_data['repost_type'] == Post.RepostType.REPOST:
                validated_data['repost_of_id'].reposts.filter(author=self.context['scope']['user'],
                                                              repost_type=Post.RepostType.REPOST).delete()
            validated_data['repost_of'] = validated_data.pop('repost_of_id')

        # Tagged users
        tags = validated_data.pop('tags', None)
        if tags:
            users = []
            for tag in tags:
                if tag['id'].isdigit():
                    user_qs = User.objects.filter(id=tag['id'], username=tag['text'])
                    if user_qs.exists():
                        user = user_qs.first()
                        if not self.context['scope']['user'] in user.blocked.all():
                            users.append(user_qs.first())
            validated_data['tagged_users'] = users

        # Extract object if link is present in post body
        linked_object = extract_linked_object(text=validated_data['body'])
        if linked_object:
            if isinstance(linked_object, Post) and not validated_data.get('repost_of_id'):
                validated_data['repost_of_id'] = linked_object.pk
            if isinstance(linked_object, Ballot) and not validated_data.get('ballot_id'):
                validated_data['ballot_id'] = linked_object.pk
            if isinstance(linked_object, Survey) and not validated_data.get('survey_id'):
                validated_data['survey_id'] = linked_object.pk
            if isinstance(linked_object, Petition) and not validated_data.get('petition_id'):
                validated_data['petition_id'] = linked_object.pk
            if isinstance(linked_object, Meeting) and not validated_data.get('meeting_id'):
                validated_data['meeting_id'] = linked_object.pk
            if isinstance(linked_object, Section) and not validated_data.get('section_id'):
                validated_data['section_id'] = linked_object.pk

        # Calling create method with new validated data
        assets = validated_data.pop('assets')
        if len(assets) > 0:
            validated_data['is_active'] = False
        post = super().create(validated_data)

        # Hashtags
        if tags:
            hashtags = []
            for tag in tags:
                if f'#{tag["id"]}' in validated_data['body']:
                    hashtags.append(tag['id'])
            if hashtags:
                post.hashtags.add(*hashtags)

        # Assets
        for asset in assets:
            # Create a unique key for S3 to avoid collisions
            file_extension = asset['name'].split('.')[-1]
            unique_key = f"uploads/{post.author.id}/posts/{uuid.uuid4()}.{file_extension}"
            Asset.objects.create(post=post, file_key=unique_key, **asset)

        if post.reply_to:
            post_save.send(sender=Post, instance=post.reply_to, created=False)
        if post.repost_of:
            post_save.send(sender=Post, instance=post.repost_of, created=False)
        return post


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

    def get_thread(self, post):
        if post.reply_to:
            posts = get_reply_thread(post=post, author=post.reply_to.author)
        else:
            posts = get_reply_thread(post=post, author=post.author)
        serializer = PostSerializer(posts, many=True, context=self.context)
        return serializer.data

    class Meta(PostSerializer.Meta):
        fields = PostSerializer.Meta.fields + ('thread',)


def get_reply_thread(post: Post, author: User):
    """Recursive helper to get thread chain in list"""
    posts = []
    qs = post.replies.filter(author=author)
    if qs.exists():
        post = qs.first()
        posts.append(post)
        posts.extend(get_reply_thread(post, post.author))
    if len(posts) > 0:
        post = posts[-1]
        posts.extend(get_reply_thread(post, post.reply_to.author))
    return posts
