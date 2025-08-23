from django.contrib.auth import get_user_model
from rest_framework import serializers

from petition.models import Petition
from users.serializers import UserSerializer

User = get_user_model()


class PetitionSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    image = serializers.SerializerMethodField()
    supporters = serializers.SerializerMethodField(read_only=True)
    recent_supporters = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Petition
        fields = [
            'id',
            'author',
            'title',
            'description',
            'image',
            'video',
            'supporters',
            'recent_supporters',
            'start_time',
            'end_time',
        ]

    def get_image(self, obj):
        if 'scope' in self.context:
            headers = dict(self.context['scope']['headers'])
            host = headers[b'host'].decode()
            return 'http://' + host + obj.image.url
        return self.context.get('request').build_absolute_uri(obj.image.url)

    @staticmethod
    def get_supporters(instance: Petition):
        return instance.supporters.count()

    def get_recent_supporters(self, instance: Petition):
        recent_supporters = []
        if instance.supporters.exists():
            # Using through only returns object id
            related = instance.supporters.through.objects.filter(petition_id=instance.id).order_by('-id')[:5]
            user_list = []
            for obj in related:
                user = User.objects.get(id=obj.customuser_id)
                user_list.append(user)
            serializer = UserSerializer(user_list, many=True, context=self.context)
            recent_supporters = serializer.data
        return recent_supporters
