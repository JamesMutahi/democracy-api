from django.contrib.auth import get_user_model, authenticate
from django.contrib.sites.models import Site
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.users.models import ProfileVisit

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
    county = CountySerializer(read_only=True)
    constituency = ConstituencySerializer(read_only=True)
    ward = WardSerializer(read_only=True)
    is_visited = serializers.SerializerMethodField(read_only=True)

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
            'county',
            'constituency',
            'ward',
            'is_visited',
            'is_active',
            'date_joined',
            'is_muted',
            'is_blocked',
            'has_blocked',
            'is_followed',
            'is_notifying',
        )

    @staticmethod
    def get_image(obj):
        if obj.image:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.image.url
        return None

    @staticmethod
    def get_cover_photo(obj):
        if obj.cover_photo:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.cover_photo.url
        return None

    @staticmethod
    def get_following(user):
        return user.following.count()

    @staticmethod
    def get_followers(user):
        return user.followers.count()

    def get_is_muted(self, user):
        return self.context['scope']['user'].muted.contains(user)

    def get_is_blocked(self, user):
        return self.context['scope']['user'].blocked.contains(user)

    def get_has_blocked(self, user):
        return user.blocked.contains(self.context['scope']['user'])

    def get_is_followed(self, user):
        return self.context['scope']['user'].following.contains(user)

    def get_is_notifying(self, user):
        return self.context['scope']['user'].notifiers.contains(user)

    def get_is_visited(self, visited):
        is_visited = ProfileVisit.objects.filter(visitor=self.context['scope']['user'], visited=visited).exists()
        return is_visited


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
            'name',
            'image',
            'cover_photo',
            'bio',
        )
