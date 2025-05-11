import django.contrib.auth.password_validation as validators
from django.contrib.auth import get_user_model, authenticate
from django.core import exceptions
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError

from users.models import Code
from users.utils.code import generate_code

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    token = serializers.CharField(source='auth_token.key', read_only=True)
    password = serializers.CharField(write_only=True, required=True)
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = (
            'id', 'token', 'email', 'first_name', 'last_name', 'password', 'password2',
            'is_verified', 'is_active', 'is_staff')
        extra_kwargs = {'first_name': {'required': True}, 'last_name': {'required': True},
                        'is_verified': {'read_only': True}, 'is_active': {'read_only': True},
                        'is_staff': {'read_only': True}}

    def validate(self, attrs):
        if attrs.get('password') != attrs.pop('password2'):
            raise ValidationError({"error": "Passwords don't match"})
        errors = dict()
        try:
            validators.validate_password(password=attrs.get('password'), user=self.context['request'].user)
        except exceptions.ValidationError as e:
            errors['error'] = list(e.messages)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        user_qs = User.objects.filter(email=validated_data['email'])
        if user_qs.exists():
            user = user_qs.first()
            if user.is_verified:
                raise serializers.ValidationError({'error': ['User with this email already exists.']})
            else:
                if not Code.objects.filter(user=user_qs.first()).exists():
                    Code.objects.get_or_create(user=user_qs.first(), code=generate_code())
                # send_code.delay(user_id=user_qs.first().id, subject='Registration Code',
                #                 template_name='emails/index.html')
                return user
        user = User(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            is_verified=False,
        )
        user.set_password(validated_data['password'])
        user.save()
        Token.objects.create(user=user)
        if not Code.objects.filter(user=user_qs.first()).exists():
            Code.objects.get_or_create(user=user_qs.first(), code=generate_code())
        # send_code.delay(user_id=user.id, subject='Registration Code', template_name='emails/index.html')
        return user


class RegistrationVerificationSerializer(serializers.Serializer):
    code = serializers.IntegerField()

    def validate(self, attrs):
        code = attrs.get('code')
        user = self.context['request'].user
        if len(str(code)) != 4:
            raise serializers.ValidationError({'error': 'Ensure this value has 4 digits'})
        if not Code.objects.filter(user=user, code=code).exists():
            raise serializers.ValidationError({'error': 'Invalid code.'})
        return attrs

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


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
        if not user.is_verified:
            # send_code.delay(user_id=user.id, subject='Registration Code', template_name='emails/index.html')
            if not Code.objects.filter(user=user_qs.first()).exists():
                Code.objects.get_or_create(user=user_qs.first(), code=generate_code())

        attrs['users'] = user
        return attrs

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class PasswordResetCodeVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.IntegerField()

    def validate(self, attrs):
        email = attrs.get('email')
        code = attrs.get('code')
        user = User.objects.get(email=email)
        if len(str(code)) != 4:
            raise serializers.ValidationError({'error': 'Ensure this value has 4 digits'})
        if not Code.objects.filter(user=user, code=code).exists():
            raise serializers.ValidationError({'error': 'Invalid code.'})
        return attrs

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class EmailVerificationSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = (
            'email',
        )

    def validate(self, attrs):
        if not User.objects.filter(email=attrs['email']).exists():
            raise ValidationError({'error': 'This email does not exist.'})
        return attrs


class PasswordResetSerializer(serializers.Serializer):
    new_password1 = serializers.CharField(max_length=255, required=True, write_only=True)
    new_password2 = serializers.CharField(max_length=255, required=True, write_only=True)

    def validate(self, attrs):
        if attrs.get('new_password1') != attrs.pop('new_password2'):
            raise ValidationError({"error": "Passwords don't match"})
        errors = dict()
        try:
            validators.validate_password(password=attrs.get('new_password1'), user=self.context['request'].user)
        except exceptions.ValidationError as e:
            errors['error'] = list(e.messages)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(max_length=255, required=True, write_only=True)
    new_password1 = serializers.CharField(max_length=255, required=True, write_only=True)
    new_password2 = serializers.CharField(max_length=255, required=True, write_only=True)

    def validate(self, attrs):
        if not self.context['request'].user.check_password(attrs.pop('old_password')):
            raise ValidationError({"error": "Wrong password"})
        if attrs.get('new_password1') != attrs.pop('new_password2'):
            raise ValidationError({"error": "Passwords don't match"})
        errors = dict()
        try:
            validators.validate_password(password=attrs.get('new_password1'), user=self.context['request'].user)
        except exceptions.ValidationError as e:
            errors['error'] = list(e.messages)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'first_name',
            'last_name',
        )
