from django.contrib.auth import get_user_model
from rest_framework.serializers import ModelSerializer, SerializerMethodField

from social.models import Post

User = get_user_model()


class SafeUserSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'is_verified', 'is_staff', 'is_active',)


class PostSerializer(ModelSerializer):
    author = SafeUserSerializer(read_only=True)
    likes = SerializerMethodField()
    replies = SerializerMethodField()
    views = SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            'id',
            'author',
            'created_at',
            'updated_at',
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
            'views',
            'parent',
            'is_repost',
            'replies',
        )

    def get_fields(self):
        fields = super(PostSerializer, self).get_fields()
        fields['parent'] = PostSerializer(read_only=True)
        return fields

    @staticmethod
    def get_likes(obj):
        count = obj.likes.count()
        return count

    @staticmethod
    def get_replies(obj):
        count = obj.replies_and_reposts.count()
        return count

    @staticmethod
    def get_views(obj):
        count = obj.ip_views.count()
        return count

    def create(self, validated_data):
        validated_data['author'] = self.context.get('scope').get('user')
        return super().create(validated_data)
