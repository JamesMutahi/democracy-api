from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.chat.serializers import MessageSerializer, ChatSerializer, get_or_create_direct_chat

User = get_user_model()


class MessageCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['scope'] = {'user': self.request.user}
        return context


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

