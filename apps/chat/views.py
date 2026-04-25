import botocore
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Asset, Message
from apps.chat.serializers import MessageSerializer, ChatSerializer, get_or_create_direct_chat, AssetSerializer
from apps.utils.presigned_url import generate_presigned_url, s3_client

User = get_user_model()


class MessageCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['scope'] = {'user': self.request.user}
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save()

        upload_data = []

        for asset in message.assets.all():
            # Generate the upload link for this specific file
            link = generate_presigned_url(asset.file_key, asset.content_type)
            upload_data.append({
                "asset_id": asset.id,
                "name": asset.name,
                "url": link
            })

        return Response({
            "message": serializer.data,
            "uploads": upload_data
        }, status=status.HTTP_201_CREATED)


class AssetUploadCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        asset_id_list = request.data.get('asset_id_list', None)
        if not asset_id_list:
            return Response({'asset_id_list': 'This field is required'}, status=status.HTTP_400_BAD_REQUEST)

        if len(asset_id_list) == 0:
            return Response({'asset_id_list': 'No ids in list'}, status=status.HTTP_400_BAD_REQUEST)

        assets = []
        try:
            for index, asset_id in enumerate(asset_id_list):
                asset = Asset.objects.get(id=asset_id, message__author=request.user)

                try:
                    s3_client.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=asset.file_key)

                    # If no error, file exists
                    asset.is_completed = True
                    asset.save()
                    assets.append(asset)

                except botocore.exceptions.ClientError:
                    return Response({"error": "File not found in S3"}, status=404)
            context = {'scope': {'user': request.user}}
            post_save.send(sender=Message, instance=assets[0].message, created=False)
            serializer = AssetSerializer(assets, many=True, context=context)
            return Response(serializer.data, status=200)
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
    upload_data = []

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
        message = serializer.save()  # user is automatically set in MessageSerializer.create()

        created_chats.append(chat)

        for asset in message.assets.all():
            # Generate the upload link
            link = generate_presigned_url(asset.file_key, asset.content_type)
            upload_data.append({
                "asset_id": asset.id,
                "name": asset.name,
                "url": link
            })

    # Return serialized chats
    chat_serializer = ChatSerializer(created_chats, many=True, context=context)
    return Response({
        "chats": chat_serializer.data,
        "uploads": upload_data
    }, status=status.HTTP_201_CREATED)
