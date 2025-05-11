from django.contrib.auth.models import update_last_login
from django.utils import timezone
from rest_framework import generics, status, permissions, viewsets
from rest_framework.compat import coreapi, coreschema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.schemas import ManualSchema, AutoSchema
from rest_framework.views import APIView

from users.serializers import *


class LoginView(APIView):
    serializer_class = LoginSerializer
    if coreapi is not None and coreschema is not None:
        schema = ManualSchema(
            fields=[
                coreapi.Field(
                    name="email",
                    required=True,
                    location='form',
                    description="Valid email for authentication",
                    type=str,
                    schema=coreschema.String(
                        title="Email",
                        description="Valid email for authentication",
                    ),
                ),
                coreapi.Field(
                    name="password",
                    required=True,
                    location='form',
                    description="Valid password for authentication",
                    type=str,
                    schema=coreschema.String(
                        title="Password",
                        description="Valid password for authentication",
                    ),
                ),
            ],
            encoding="application/json",
        )

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['users']
        Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        serializer = UserSerializer(user)
        return Response(serializer.data)


class CreateUserView(generics.CreateAPIView):
    serializer_class = UserSerializer

    if coreapi is not None and coreschema is not None:
        schema = ManualSchema(
            fields=[
                coreapi.Field(
                    name="first_name",
                    required=True,
                    location='form',
                    description="Valid first name for registration",
                    type=str,
                    schema=coreschema.String(
                        title="First Name",
                        description="First name for registration",
                    ),
                ),
                coreapi.Field(
                    name="last_name",
                    required=True,
                    location='form',
                    description="Valid last name for registration",
                    type=str,
                    schema=coreschema.String(
                        title="Last name",
                        description="Valid last name for registration",
                    ),
                ),
                coreapi.Field(
                    name="email",
                    required=True,
                    location='form',
                    description="Valid email for registration",
                    type=str,
                    schema=coreschema.String(
                        title="Email",
                        description="Valid email for registration",
                    ),
                ),
                coreapi.Field(
                    name="password",
                    required=True,
                    location='form',
                    description="Valid password for authentication",
                    type=str,
                    schema=coreschema.String(
                        title="Password",
                        description="Valid password for authentication",
                    ),
                ),
                coreapi.Field(
                    name="password2",
                    required=True,
                    location='form',
                    description="Confirm password",
                    type=str,
                    schema=coreschema.String(
                        title="Password2",
                        description="Confirm password",
                    ),
                ),
            ],
            encoding="application/json",
        )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def resend_code(request):
    if not Code.objects.filter(user=request.user).exists():
        Code.objects.get_or_create(user=request.user, code=generate_code())
    # send_code.delay(user_id=request.user.id, subject='Security Code', template_name='emails/index.html')
    return Response("A code has been sent to your email", status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def logout(request):
    request.user.auth_token.delete()
    return Response(status=status.HTTP_200_OK)


class RegistrationVerificationView(APIView):
    serializer_class = RegistrationVerificationSerializer
    permission_classes = (permissions.IsAuthenticated,)

    if coreapi is not None and coreschema is not None:
        schema = AutoSchema(
            manual_fields=[
                coreapi.Field(
                    "code",
                    required=True,
                    location="form",
                    description='4 digit code',
                    type=int,
                    schema=coreschema.Integer(
                        title="Code",
                        description="4 digit code sent via email",
                    ),
                ),
            ]
        )

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = self.request.user
        code_obj = Code.objects.get(user=user, code=serializer.validated_data['code'])
        code_obj.delete()
        user.is_verified = True
        user.save()
        serializer = UserSerializer(user)
        return Response(serializer.data)


class PasswordResetEmailVerification(APIView):
    serializer_class = EmailVerificationSerializer

    if coreapi is not None and coreschema is not None:
        schema = AutoSchema(
            manual_fields=[
                coreapi.Field(
                    "email",
                    required=True,
                    location="form",
                    description='4 digit code',
                    type=int,
                    schema=coreschema.String(
                        title="Email",
                        description="Valid email address",
                    ),
                ),
            ]
        )

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = User.objects.get(email=serializer.validated_data['email'])
        # send_code.delay(user_id=user.id, subject='Password Reset Code', template_name='emails/password-reset.html')
        code_qs = Code.objects.filter(user=user)
        if code_qs.exists():
            code_qs.delete()
        Code.objects.get_or_create(user=user, code=generate_code())
        return Response("A code has been sent to your email", status=status.HTTP_200_OK)


class PasswordResetCodeVerification(APIView):
    serializer_class = PasswordResetCodeVerificationSerializer

    if coreapi is not None and coreschema is not None:
        schema = AutoSchema(
            manual_fields=[
                coreapi.Field(
                    "email",
                    required=True,
                    location="form",
                    description='Valid email',
                    type=int,
                    schema=coreschema.Integer(
                        title="Email",
                        description="Email to send verification code",
                    ),
                ),
                coreapi.Field(
                    "code",
                    required=True,
                    location="form",
                    description='4 digit code',
                    type=int,
                    schema=coreschema.Integer(
                        title="Code",
                        description="4 digit code sent via email",
                    ),
                ),
            ]
        )

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        code_obj = Code.objects.get(code=serializer.validated_data['code'])
        user = code_obj.user
        code_obj.delete()
        Token.objects.get_or_create(user=user)
        serializer = UserSerializer(user)
        return Response(serializer.data)


class PasswordResetView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = PasswordResetSerializer

    if coreapi is not None and coreschema is not None:
        schema = ManualSchema(
            fields=(
                coreapi.Field(
                    name="new_password1",
                    required=True,
                    location='form',
                    schema=coreschema.String(
                        title="new_password1",
                        description="Enter new password",
                    ),
                ),
                coreapi.Field(
                    name="new_password2",
                    required=True,
                    location='form',
                    schema=coreschema.String(
                        title="new_password2",
                        description="Confirm new password",
                    ),
                ),
            ),
            encoding="application/json",
        )

    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = self.request.user
        user.set_password(serializer.validated_data['new_password1'])
        user.save()
        return Response("New password has been saved.")


class PasswordChangeView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = PasswordChangeSerializer

    if coreapi is not None and coreschema is not None:
        schema = ManualSchema(
            fields=(
                coreapi.Field(
                    name="old_password",
                    required=True,
                    location='form',
                    schema=coreschema.String(
                        title="old_password",
                        description="Enter old password",
                    ),
                ),
                coreapi.Field(
                    name="new_password1",
                    required=True,
                    location='form',
                    schema=coreschema.String(
                        title="new_password1",
                        description="Enter new password",
                    ),
                ),
                coreapi.Field(
                    name="new_password2",
                    required=True,
                    location='form',
                    schema=coreschema.String(
                        title="new_password2",
                        description="Confirm new password",
                    ),
                ),
            ),
            encoding="application/json",
        )

    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = self.request.user
        user.set_password(serializer.validated_data['new_password1'])
        user.password_change_required = False
        user.password_change_time = timezone.now()
        user.save()
        return Response("New password has been saved.")


class UserView(viewsets.ModelViewSet):
    permission_classes = (permissions.IsAuthenticated,)

    serializer_classes = {
        'update': UserUpdateSerializer,
        'retrieve': UserSerializer,
    }
    default_serializer_class = UserSerializer

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, self.default_serializer_class)

    def get_object(self):
        return self.request.user
