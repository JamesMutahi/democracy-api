from django.contrib.auth import get_user_model
from rest_framework import serializers

from chat.models import Message, Room
from users.serializers import UserSerializer

User = get_user_model()


class MessageSerializer(serializers.ModelSerializer):
    user = UserSerializer()

    class Meta:
        model = Message
        fields = ['id', 'room', 'user', 'text', 'is_read', 'is_edited', 'is_deleted', 'created_at', 'updated_at']


class RoomSerializer(serializers.ModelSerializer):
    users = UserSerializer(many=True)
    last_message = serializers.SerializerMethodField()
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Room
        fields = ["id", "messages", "users", "last_message"]
        read_only_fields = ["messages", "last_message"]

    def get_last_message(self, obj: Room):
        if obj.messages.exists():
            serializer = MessageSerializer(obj.messages.order_by('created_at').last(), context=self.context)
            return serializer.data
        else:
            return None
