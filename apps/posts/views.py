from rest_framework import generics, permissions

from apps.posts.models import Post
from apps.posts.serializers import PostSerializer


class PostCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PostSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['scope'] = {'user': self.request.user}
        return context


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of a post to edit it.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author == request.user


class PostUpdateView(generics.UpdateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    serializer_class = PostSerializer
    queryset = Post.objects.filter(status='draft')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['scope'] = {'user': self.request.user}
        return context
