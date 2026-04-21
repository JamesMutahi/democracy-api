import uuid

import boto3
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Asset
from apps.chat.serializers import MessageSerializer, ChatSerializer, get_or_create_direct_chat
from apps.utils.presigned_url import generate_presigned_url

User = get_user_model()


class MessageCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['scope'] = {'user': self.request.user}
        return context

    def create(self, request, *args, **kwargs):
        # 1. Validate and save the Message first
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


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def direct_message(request):
    data = request.data.copy()
    user_ids = data.pop('user_ids', [])

    if not user_ids:
        return Response({"user_ids": "This field is required"},
                        status=status.HTTP_400_BAD_REQUEST)

    user = request.user
    context = {'scope': {'user': user}}
    created_chats = []  # Store Chat model instances

    for user_id in user_ids:
        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": f"User with id {user_id} does not exist"},
                            status=status.HTTP_404_NOT_FOUND)

        # Get or create chat (now supports self-chat)
        chat = get_or_create_direct_chat(user, target_user)

        # Prepare message data
        message_data = data.copy()
        message_data['chat'] = chat.id

        # Create the message
        serializer = MessageSerializer(data=message_data, context=context)
        serializer.is_valid(raise_exception=True)
        serializer.save()  # user is automatically set in MessageSerializer.create()

        created_chats.append(chat)

    # Return serialized chats
    chat_serializer = ChatSerializer(created_chats, many=True, context=context)
    return Response(chat_serializer.data, status=status.HTTP_201_CREATED)
