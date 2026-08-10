from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.posts.models import Post, Asset
from apps.posts.querysets import annotate_post_metrics
from apps.posts.serializers import PostSerializer, PostIdSerializer, AssetUploadCompleteSerializer
from apps.utils.presigned_url import generate_presigned_url, s3_client


class PostCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PostSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            post = serializer.save()

            post = annotate_post_metrics(
                Post.objects.filter(pk=post.pk),
                request.user,
            ).get()

            upload_data = []
            for asset in post.assets.all():
                # Generate the upload link for this specific file
                link = generate_presigned_url(asset.file_key, asset.content_type)
                upload_data.append({"asset_id": asset.id, "name": asset.name, "url": link})
        except Exception:
            # Ideally log this exception.
            return Response(
                {"detail": "Could not create post or generate upload URLs."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {"post": self.get_serializer(post).data, "uploads": upload_data},
            status=status.HTTP_201_CREATED,
        )


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def generate_upload_urls(request):
    serializer = PostIdSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    post_id = serializer.validated_data["post_id"]

    post = get_object_or_404(
        Post,
        id=post_id,
        author=request.user,
        is_deleted=False,
    )

    upload_data = []

    for asset in post.assets.filter(is_completed=False):
        try:
            link = generate_presigned_url(asset.file_key, asset.content_type)
        except Exception:
            return Response(
                {"detail": "Could not generate upload URL."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        upload_data.append(
            {
                "asset_id": asset.id,
                "name": asset.name,
                "url": link,
            }
        )

    return Response(
        {"uploads": upload_data},
        status=status.HTTP_200_OK,
    )


class AssetUploadCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = AssetUploadCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Remove duplicates safely
        asset_ids = list(set(serializer.validated_data["asset_id_list"]))

        assets = list(
            Asset.objects.filter(
                id__in=asset_ids,
                post__author=request.user,
                post__is_deleted=False,
            ).select_related("post")
        )

        if len(assets) != len(asset_ids):
            return Response(
                {"error": "Asset not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        post_ids = {asset.post_id for asset in assets}

        if len(post_ids) != 1:
            return Response(
                {"error": "All assets must belong to the same post."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify all files first before changing DB state
        for asset in assets:
            try:
                s3_response = s3_client.head_object(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Key=asset.file_key,
                )
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code")

                if error_code in {"404", "NoSuchKey"}:
                    return Response(
                        {"error": "File not found in S3."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                return Response(
                    {"error": "Unable to verify uploaded file."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            # Verify content type and size match what the client declared.
            if asset.content_type and s3_response.get("ContentType") != asset.content_type:
                return Response(
                    {"error": "Uploaded file content type does not match."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if asset.file_size is not None and s3_response.get("ContentLength") != asset.file_size:
                return Response(
                    {"error": "Uploaded file size does not match."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            post = assets[0].post

            Asset.objects.filter(
                id__in=[asset.id for asset in assets],
                post=post,
            ).update(is_completed=True)

            has_incomplete_assets = post.assets.filter(is_completed=False).exists()

            if not has_incomplete_assets and not post.is_active:
                post.is_active = True
                post.save(update_fields=["is_active"])

        return Response(
            {"status": "verified"},
            status=status.HTTP_200_OK,
        )


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of a post to edit it.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author == request.user
