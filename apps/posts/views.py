import botocore
from django.conf import settings
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.posts.models import Post, Asset
from apps.posts.serializers import PostSerializer
from apps.utils.presigned_url import generate_presigned_url, s3_client


class PostCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PostSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["scope"] = {"user": self.request.user}
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save()

        upload_data = []

        for asset in post.assets.all():
            # Generate the upload link for this specific file
            link = generate_presigned_url(asset.file_key, asset.content_type)
            upload_data.append({"asset_id": asset.id, "name": asset.name, "url": link})

        return Response(
            {"post": serializer.data, "uploads": upload_data},
            status=status.HTTP_201_CREATED,
        )


class AssetUploadCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data.copy()
        asset_id_list = data.pop("asset_id_list", [])
        if not asset_id_list:
            return Response(
                {"asset_id_list": "This field is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            for index, asset_id in enumerate(asset_id_list):
                asset = Asset.objects.get(id=asset_id, post__author=request.user)

                try:
                    s3_client.head_object(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=asset.file_key
                    )

                    # If no error, file exists
                    asset.is_completed = True
                    asset.save()

                    if index == len(asset_id_list) - 1:
                        post = asset.post
                        post.is_active = True
                        post.save()

                except botocore.exceptions.ClientError:
                    return Response({"error": "File not found in S3"}, status=404)
            return Response({"status": "verified"}, status=200)
        except Asset.DoesNotExist:
            return Response({"error": "Asset not found"}, status=404)


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
    queryset = Post.objects.filter(status="draft")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["scope"] = {"user": self.request.user}
        return context
