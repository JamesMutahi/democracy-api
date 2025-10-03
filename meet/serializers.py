from django.contrib.auth import get_user_model
from rest_framework import serializers

from meet.models import Meeting
from users.serializers import UserSerializer

User = get_user_model()


class MeetingSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)
    listeners = serializers.SerializerMethodField(read_only=True)
    recent_listeners = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Meeting
        fields = [
            'id',
            'host',
            'title',
            'description',
            'listeners',
            'recent_listeners',
            'start_time',
            'end_time',
            'is_active',
        ]

    @staticmethod
    def get_listeners(instance: Meeting):
        return instance.listeners.count()

    def get_recent_listeners(self, instance: Meeting):
        recent_listeners = []
        if instance.listeners.exists():
            # Using through only returns object id
            related = instance.listeners.through.objects.filter(meeting_id=instance.pk).order_by('-id')[:5]
            user_list = []
            for obj in related:
                user = User.objects.get(id=obj.customuser_id)
                user_list.append(user)
            serializer = UserSerializer(user_list, many=True, context=self.context)
            recent_listeners = serializer.data
        return recent_listeners
