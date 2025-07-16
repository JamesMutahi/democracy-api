from django.contrib.auth import get_user_model, authenticate
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    cover_photo = serializers.SerializerMethodField()
    following = serializers.SerializerMethodField(read_only=True)
    followers = serializers.SerializerMethodField(read_only=True)
    is_muted = serializers.SerializerMethodField(read_only=True)
    is_blocked = serializers.SerializerMethodField(read_only=True)
    is_followed = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'name',
            'email',
            'image',
            'cover_photo',
            'status',
            'muted',
            'blocked',
            'following',
            'followers',
            'is_active',
            'date_joined',
            'is_muted',
            'is_blocked',
            'is_followed',
        )

    def get_image(self, obj):
        if 'scope' in self.context:
            headers = dict(self.context['scope']['headers'])
            host = headers[b'host'].decode()
            return 'http://' + host + obj.image.url
        return self.context.get('request').build_absolute_uri(obj.image.url)

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

    def get_is_followed(self, user):
        if 'scope' in self.context:
            return self.context['scope']['user'].following.contains(user)
        return False


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
            'status',
        )
