import re
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone
from rest_framework import serializers
from taggit.serializers import TagListSerializerField

from apps.ballot.models import Ballot
from apps.ballot.serializers import BallotSerializer
from apps.broadcast.models import Broadcast
from apps.broadcast.serializers import BroadcastSerializer
from apps.constitution.models import Section
from apps.constitution.serializers import SectionSerializer
from apps.petition.models import Petition
from apps.petition.serializers import PetitionSerializer
from apps.posts.models import Post, Report, Asset
from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer
from apps.users.serializers import UserSerializer
from apps.utils.link_extractor import extract_linked_object
from apps.utils.presigned_url import s3_client
from apps.utils.serializer_user import get_current_user

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
        if not obj.file_key or not obj.is_completed:
            return None

        try:
            return s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': obj.file_key,
                },
                ExpiresIn=3600,
            )
        except Exception:
            return None


class PostSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    published_at = serializers.DateTimeField(default=timezone.now, read_only=True)
    body = serializers.SerializerMethodField()
    likes = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    bookmarks = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    replies = serializers.SerializerMethodField()
    reposts = serializers.SerializerMethodField()
    is_reposted = serializers.SerializerMethodField()
    is_quoted = serializers.SerializerMethodField()
    ballot = BallotSerializer(read_only=True)
    survey = SurveySerializer(read_only=True)
    petition = PetitionSerializer(read_only=True)
    broadcast = BroadcastSerializer(read_only=True)
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
    broadcast_id = serializers.PrimaryKeyRelatedField(
        queryset=Broadcast.objects.all(),
        source='broadcast',
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
    assets = AssetSerializer(many=True, default=list)

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
            'is_pinned',
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
            'broadcast',
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
            'broadcast_id',
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
        # Fallback to .count() if the annotation is missing
        if hasattr(obj, "likes_count"):
            return obj.likes_count
        return obj.likes.count()

    def get_is_liked(self, obj):
        if hasattr(obj, "is_liked"):
            return obj.is_liked
        user = get_current_user(self.context)
        return obj.likes.filter(pk=user.pk).exists()

    @staticmethod
    def get_bookmarks(obj):
        if hasattr(obj, "bookmarks_count"):
            return obj.bookmarks_count
        return obj.bookmarks.count()

    def get_is_bookmarked(self, obj):
        if hasattr(obj, "is_bookmarked"):
            return obj.is_bookmarked
        user = get_current_user(self.context)
        return obj.bookmarks.filter(pk=user.pk).exists()

    @staticmethod
    def get_replies(obj):
        if hasattr(obj, "replies_count"):
            return obj.replies_count
        return obj.replies.filter(is_active=True, status='published').count()

    @staticmethod
    def get_reposts(obj):
        if hasattr(obj, "reposts_count"):
            return obj.reposts_count
        return obj.get_reposts_count()

    def get_is_reposted(self, obj):
        if hasattr(obj, "is_reposted"):
            return obj.is_reposted
        user = get_current_user(self.context)
        return obj.reposts.filter(is_active=True, author=user, repost_type=Post.RepostType.REPOST).exists()

    def get_is_quoted(self, obj):
        if hasattr(obj, "is_quoted"):
            return obj.is_quoted
        user = get_current_user(self.context)
        return obj.reposts.filter(is_active=True, author=user, repost_type=Post.RepostType.QUOTE).exists()

    def get_is_upvoted(self, obj):
        if hasattr(obj, "is_upvoted"):
            return obj.is_upvoted
        user = get_current_user(self.context)
        return obj.upvotes.filter(pk=user.pk).exists()

    def get_is_downvoted(self, obj):
        if hasattr(obj, "is_downvoted"):
            return obj.is_downvoted
        user = get_current_user(self.context)
        return obj.downvotes.filter(pk=user.pk).exists()

    @staticmethod
    def get_upvotes(obj):
        if not obj.community_note_of:
            return 0
        if hasattr(obj, "upvotes_count"):
            return obj.upvotes_count
        return obj.upvotes.count()

    @staticmethod
    def get_downvotes(obj):
        if not obj.community_note_of:
            return 0
        if hasattr(obj, "downvotes_count"):
            return obj.downvotes_count
        return obj.downvotes.count()

    @staticmethod
    def get_community_note(obj: Post):
        if hasattr(obj, "top_community_note_body"):
            return obj.top_community_note_body
        return obj.get_top_note()

    @transaction.atomic
    def create(self, validated_data):
        current_user = get_current_user(self.context)
        validated_data['author'] = current_user

        repost_of = validated_data.get('repost_of')
        if repost_of:
            if repost_of.author.blocked.filter(id=current_user.id).exists():
                raise serializers.ValidationError("You have been blocked by this user.")

        reply_to = validated_data.get('reply_to')
        if reply_to:
            if reply_to.author.blocked.filter(id=current_user.id).exists():
                raise serializers.ValidationError("You have been blocked by this user.")

        if validated_data.get('repost_of_id'):
            # Author can only have one repost of a post without body or relevant fields
            if validated_data.get('repost_type') == Post.RepostType.REPOST:
                validated_data['repost_of_id'].reposts.filter(author=current_user,
                                                              repost_type=Post.RepostType.REPOST).delete()
            validated_data['repost_of'] = validated_data.pop('repost_of_id')

        # Tagged users
        usernames = set(re.findall(r'@([\w.-]+)', validated_data.get('body', '')))
        users = User.objects.filter(username__in=usernames).exclude(blocked=current_user)
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
            if isinstance(linked_object, Broadcast) and not validated_data.get('broadcast_id'):
                validated_data['broadcast_id'] = linked_object.pk
            if isinstance(linked_object, Section) and not validated_data.get('section_id'):
                validated_data['section_id'] = linked_object.pk

        # Calling create method with new validated data
        assets = validated_data.pop('assets')
        if len(assets) > 0:
            validated_data['is_active'] = False
        post = super().create(validated_data)

        # Hashtags
        hashtags = set(re.findall(r'#(\w+)', validated_data.get('body', '')))
        post.hashtags.add(*hashtags)

        # Assets
        for asset in assets:
            # Create a unique key for S3 to avoid collisions
            name = asset.get('name') or ''
            extension = Path(name).suffix.lstrip('.').lower()
            ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'mp4', 'pdf'}

            if extension not in ALLOWED_EXTENSIONS:
                raise serializers.ValidationError("Unsupported file type.")

            if extension:
                unique_key = f"uploads/{post.author_id}/posts/{uuid.uuid4()}.{extension}"
            else:
                unique_key = f"uploads/{post.author_id}/posts/{uuid.uuid4()}"

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
        user = get_current_user(self.context)
        validated_data['user'] = user

        if Report.objects.filter(
                user=user,
                post=validated_data['post'],
                issue=validated_data['issue'],
        ).exists():
            raise serializers.ValidationError("You have already reported this post.")

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


def get_reply_thread(post: Post, author: User, depth: int = 0, visited=None):
    if visited is None:
        visited = set()

    if depth > 20:
        return []

    if post.pk in visited:
        return []

    visited.add(post.pk)

    child = post.replies.filter(
        author=author,
        is_active=True,
        status='published',
    ).order_by('published_at', 'id').first()

    if not child:
        return []

    return [child] + get_reply_thread(child, child.author, depth + 1, visited)


class PostIdSerializer(serializers.Serializer):
    post_id = serializers.IntegerField()


class AssetUploadCompleteSerializer(serializers.Serializer):
    asset_id_list = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        max_length=20,
    )
