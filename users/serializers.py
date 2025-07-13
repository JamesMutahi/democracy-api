from django.contrib.auth import get_user_model, authenticate
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField(read_only=True)
    display_name = serializers.SerializerMethodField(read_only=True)
    image = serializers.SerializerMethodField()
    following = serializers.SerializerMethodField(read_only=True)
    followers = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = (
            'id',
            'name',
            'display_name',
            'email',
            'image',
            'status',
            'muted',
            'blocked',
            'following',
            'followers',
            'is_active',
            'date_joined',
        )

    @staticmethod
    def get_name(user):
        return user.get_full_name()

    @staticmethod
    def get_display_name(user):
        if user.display_name == '':
            return user.get_full_name()
        return user.display_name

    def get_image(self, obj):
        if 'scope' in self.context:
            headers = dict(self.context['scope']['headers'])
            host = headers[b'host'].decode()
            return 'http://' + host + obj.image.url
        return self.context.get('request').build_absolute_uri(obj.image.url)

    @staticmethod
    def get_following(user):
        return user.following.count()

    @staticmethod
    def get_followers(user):
        return user.followers.count()


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
