import uuid

import boto3
from django.conf import settings
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.posts.models import Post, Asset
from apps.posts.serializers import PostSerializer
from apps.utils.presigned_url import generate_presigned_url


class PostCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PostSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['scope'] = {'user': self.request.user}
        return context

    def create(self, request, *args, **kwargs):
        # 1. Validate and save the Post first
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save(author=self.request.user)

        # 2. Get the list of files the user wants to upload
        # Expected format: [{"name": "cat.jpg", "type": "image/jpeg", "size": 1024}, ...]
        files_data = request.data.get('assets', [])
        upload_data = []

        for file_info in files_data:
            # Create a unique key for S3 to avoid collisions
            file_extension = file_info['name'].split('.')[-1]
            unique_key = f"posts/{post.id}/{uuid.uuid4()}.{file_extension}"

            # 3. Create the Asset record in the database
            Asset.objects.create(
                post=post,
                file_key=unique_key,
                name=file_info['name'],
                file_size=file_info['size'],
                content_type=file_info['type']
            )

            # 4. Generate the upload link for this specific file
            presigned_post = generate_presigned_url(unique_key, file_info['type'])
            upload_data.append({
                "name": file_info['name'],
                "instructions": presigned_post
            })

        # 5. Return the Post data + all upload instructions
        return Response({
            "post": serializer.data,
            "uploads": upload_data
        }, status=status.HTTP_201_CREATED)


class AssetUploadCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, asset_id):
        try:
            asset = Asset.objects.get(id=asset_id, post__author=request.user)

            # Verify file exists in S3 using boto3
            s3 = boto3.client('s3')
            try:
                s3.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=asset.file_key)

                # If no error, file exists
                asset.is_completed = True
                asset.uploaded_at = timezone.now()
                asset.save()

                return Response({"status": "verified"}, status=200)
            except:
                return Response({"error": "File not found in S3"}, status=404)

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
    queryset = Post.objects.filter(status='draft')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['scope'] = {'user': self.request.user}
        return context
