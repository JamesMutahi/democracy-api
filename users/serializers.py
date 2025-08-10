from django.contrib.auth import get_user_model, authenticate
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

User = get_user_model()


class Base64ImageField(serializers.ImageField):
    """
    A Django REST framework field for handling image-uploads through raw post data.
    It uses base64 for encoding and decoding the contents of the file.

    Heavily based on
    https://github.com/tomchristie/django-rest-framework/pull/1268

    Updated for Django REST framework 3.
    """

    def to_internal_value(self, data):
        from django.core.files.base import ContentFile
        import base64
        import six
        import uuid

        # Check if this is a base64 string
        if isinstance(data, six.string_types):
            # Check if the base64 string is in the "data:" format
            if 'data:' in data and ';base64,' in data:
                # Break out the header from the base64 content
                header, data = data.split(';base64,')

            # Try to decode the file. Return validation error if it fails.
            try:
                decoded_file = base64.b64decode(data)
            except TypeError:
                self.fail('invalid_image')

            # Generate file name:
            file_name = str(uuid.uuid4())[:12]  # 12 characters are more than enough.
            # Get the file name extension:
            file_extension = self.get_file_extension(file_name, decoded_file)

            complete_file_name = "%s.%s" % (file_name, file_extension,)

            data = ContentFile(decoded_file, name=complete_file_name)

        return super(Base64ImageField, self).to_internal_value(data)

    def get_file_extension(self, file_name, decoded_file):
        import imghdr

        extension = imghdr.what(file_name, decoded_file)
        extension = "jpg" if extension == "jpeg" else extension

        return extension



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
            'status',
            'muted',
            'blocked',
            'following',
            'followers',
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
            'status',
        )
