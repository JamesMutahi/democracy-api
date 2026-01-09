from django.contrib.auth.models import update_last_login
from rest_framework import status, permissions, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.compat import coreapi, coreschema
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.schemas import ManualSchema
from rest_framework.views import APIView

from users.serializers import *


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
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
        return Response({'token': user.auth_token.key}, status=200)


@api_view(['DELETE'])
def logout(request):
    # TODO: Prototype cannot have tokens deleted
    # request.user.auth_token.delete()
    return Response(status=status.HTTP_200_OK)


class UserView(viewsets.ModelViewSet):
    serializer_classes = {
        'update': UserUpdateSerializer,
        'retrieve': UserSerializer,
    }
    default_serializer_class = UserSerializer

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, self.default_serializer_class)

    def get_object(self):
        return self.request.user
