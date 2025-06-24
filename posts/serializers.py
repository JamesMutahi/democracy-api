from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.serializers import ModelSerializer, SerializerMethodField

from posts.models import Post

User = get_user_model()


class SafeUserSerializer(ModelSerializer):
    image = SerializerMethodField()
    following = SerializerMethodField(read_only=True)
    followers = SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'first_name',
            'last_name',
            'image',
            'status',
            'is_staff',
            'is_active',
            'following',
            'followers',
            'date_joined'
        )

    @staticmethod
    def get_image(obj):
        return obj.image.url

    @staticmethod
    def get_following(user):
        return user.following.count()

    @staticmethod
    def get_followers(user):
        return user.followers.count()


class PostSerializer(ModelSerializer):
    author = SafeUserSerializer(read_only=True)
    likes = SerializerMethodField()
    is_liked = SerializerMethodField()
    bookmarks = SerializerMethodField()
    is_bookmarked = SerializerMethodField()
    replies = SerializerMethodField()
    reposts = SerializerMethodField()
    views = SerializerMethodField()

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
            'views',
            'replies',
            'reposts',
            'reply_to',
            'repost_of',
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
        is_liked = obj.likes.filter(id=self.context['scope']['user'].id).exists()
        return is_liked

    @staticmethod
    def get_bookmarks(obj):
        count = obj.bookmarks.count()
        return count

    def get_is_bookmarked(self, obj):
        is_bookmarked = obj.bookmarks.filter(id=self.context['scope']['user'].id).exists()
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
        return super().create(validated_data)
