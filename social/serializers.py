from django.contrib.auth import get_user_model
from rest_framework import serializers

from social.models import Post

User = get_user_model()


class SafeUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'is_verified', 'is_staff', 'is_active',)


class PostSerializer(serializers.ModelSerializer):
    author = SafeUserSerializer(read_only=True)
    likes = serializers.SerializerMethodField()
    replies = serializers.SerializerMethodField()
    views = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            'id',
            'author',
            'created_at',
            'updated_at',
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
            'views',
            'reply_to',
            'repost_of',
            'replies',
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

    @staticmethod
    def get_replies(obj):
        count = obj.replies.count() + obj.reposts.count()
        return count

    @staticmethod
    def get_views(obj):
        count = obj.ip_views.count()
        return count
