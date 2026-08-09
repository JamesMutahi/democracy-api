from rest_framework import viewsets

from apps.users.serializers import *


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
