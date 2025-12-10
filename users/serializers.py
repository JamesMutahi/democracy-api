from django.contrib.auth import get_user_model, authenticate
from django.contrib.sites.models import Site
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from users.utils.base64_image_field import Base64ImageField

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    cover_photo = serializers.SerializerMethodField()
    following = serializers.SerializerMethodField(read_only=True)
    followers = serializers.SerializerMethodField(read_only=True)
    is_muted = serializers.SerializerMethodField(read_only=True)
    is_blocked = serializers.SerializerMethodField(read_only=True)
    has_blocked = serializers.SerializerMethodField(read_only=True)
    is_followed = serializers.SerializerMethodField(read_only=True)
    is_notifying = serializers.SerializerMethodField(read_only=True)
    image_base64 = Base64ImageField(write_only=True, max_length=None, use_url=True, allow_null=True)
    cover_photo_base64 = Base64ImageField(write_only=True, max_length=None, use_url=True, allow_null=True)

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'name',
            'email',
            'image',
            'cover_photo',
            'bio',
            'muted',
            'blocked',
            'following',
            'followers',
            'is_representative',
            'is_active',
            'date_joined',
            'is_muted',
            'is_blocked',
            'has_blocked',
            'is_followed',
            'is_notifying',
            'image_base64',
            'cover_photo_base64',
        )

    @staticmethod
    def get_image(obj):
        if obj.image:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.image.url
        return None

    def get_cover_photo(self, obj):
        if 'scope' in self.context:
            headers = dict(self.context['scope']['headers'])
            host = headers[b'host'].decode()
            return 'http://' + host + obj.cover_photo.url
        return self.context.get('request').build_absolute_uri(obj.cover_photo.url)

    @staticmethod
    def get_following(user):
        return user.following.count()

    @staticmethod
    def get_followers(user):
        return user.followers.count()

    def get_is_muted(self, user):
        if 'scope' in self.context:
            return self.context['scope']['user'].muted.contains(user)
        return False

    def get_is_blocked(self, user):
        if 'scope' in self.context:
            return self.context['scope']['user'].blocked.contains(user)
        return False

    def get_has_blocked(self, user):
        if 'scope' in self.context:
            return user.blocked.contains(self.context['scope']['user'])
        return False

    def get_is_followed(self, user):
        if 'scope' in self.context:
            return self.context['scope']['user'].following.contains(user)
        return False

    def get_is_notifying(self, user):
        if 'scope' in self.context:
            return self.context['scope']['user'].preferences.allowed_users.contains(user)
        return False

    def update(self, instance, validated_data):
        if 'image_base64' in validated_data:
            validated_data['image'] = validated_data['image_base64']
        if 'cover_photo_base64' in validated_data:
            validated_data['cover_photo'] = validated_data['cover_photo_base64']
        return super().update(instance, validated_data)


class LoginSerializer(serializers.Serializer):
    email = serializers.CharField(label=_("Email"), write_only=True)
    password = serializers.CharField(
        label=_("Password"),
        style={'input_type': 'password'},
        trim_whitespace=False, write_only=True
    )

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')

        user = authenticate(request=self.context.get('request'), username=email, password=password)
        if not user:
            user_qs = User.objects.filter(email=email)
            if user_qs.exists():
                if not user_qs.first().check_password(password):
                    msg = _('Unable to log in with provided credentials.')
                    raise serializers.ValidationError({'error': msg}, code='authorization')
                else:
                    user = user_qs.first()
            else:
                msg = _('Unable to log in with provided credentials.')
                raise serializers.ValidationError({'error': msg}, code='authorization')
        attrs['users'] = user
        return attrs

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'bio',
        )
