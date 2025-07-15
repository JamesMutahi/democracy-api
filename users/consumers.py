from django.contrib.auth import get_user_model
from django.db.models import Q
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer.generics import action

from users.serializers import UserSerializer

User = get_user_model()


class UsersConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    serializer_class = UserSerializer
    queryset = User.objects.all()
    lookup_field = "pk"

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @action()
    def search_users(self, search_term: str, **kwargs):
        if search_term == '':
            # TODO: Customize to have user's following and chats
            users = User.objects.all().order_by('name')
            serializer = UserSerializer(users, many=True, context={'scope': self.scope})
            return serializer.data, 200
        else:
            users = User.objects.filter(
                Q(username__icontains=search_term) |
                Q(name__icontains=search_term)
            ).distinct()
            serializer = UserSerializer(users, many=True, context={'scope': self.scope})
            return serializer.data, 200
