from django.contrib.auth import get_user_model
from rest_framework import serializers

from chat.models import Message, Room
from posts.serializers import SafeUserSerializer

User = get_user_model()


class MessageSerializer(serializers.ModelSerializer):
    user = SafeUserSerializer()

    class Meta:
        model = Message
        fields = ['id', 'room', 'user', 'text']


class RoomSerializer(serializers.ModelSerializer):
    last_message = serializers.SerializerMethodField()
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Room
        fields = ["id", "messages", "users", "last_message"]
        read_only_fields = ["messages", "last_message"]

    def get_last_message(self, obj: Room):
        serializer = MessageSerializer(obj.messages.order_by('created_at').last(), context=self.context)
        return serializer.data
