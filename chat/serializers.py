from django.contrib.auth import get_user_model
from rest_framework import serializers

from chat.models import Message, Chat
from users.serializers import UserSerializer

User = get_user_model()


class MessageSerializer(serializers.ModelSerializer):
    user = UserSerializer()

    class Meta:
        model = Message
        fields = ['id', 'chat', 'user', 'text', 'is_read', 'is_edited', 'is_deleted', 'created_at', 'updated_at']


class ChatSerializer(serializers.ModelSerializer):
    users = UserSerializer(many=True, read_only=True)
    user = serializers.IntegerField(write_only=True)
    last_message = serializers.SerializerMethodField(read_only=True)
    messages = MessageSerializer(many=True, read_only=True)
    blocker = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = ["id", "messages", "users", "last_message", "user", "blocker"]
        read_only_fields = ["messages", "last_message"]

    def get_last_message(self, obj: Chat):
        if obj.messages.exists():
            serializer = MessageSerializer(obj.messages.order_by('created_at').last(), context=self.context)
            return serializer.data
        else:
            return None

    def get_blocker(self, instance: Chat):
        blocker = None
        user1 = instance.users.first()
        user2 = instance.users.last()
        if user1.blocked.all().contains(user2):
            blocker = user1
        if user2.blocked.all().contains(user1):
            blocker = user2
        if blocker is None:
            return None
        return UserSerializer(blocker, context=self.context).data

    def create(self, validated_data):
        user = User.objects.get(id=validated_data.pop('user'))
        chats = self.context['scope']['user'].chats.all()
        for chat in chats:
            if chat.users.all().contains(user):
                return chat
        validated_data['users'] = [self.context['scope']['user'], user]
        return super().create(validated_data)
