from rest_framework import serializers

from petition.models import Petition
from users.serializers import UserSerializer


class PetitionSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    supporters = serializers.SerializerMethodField(read_only=True)
    recent_supporters = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Petition
        fields = [
            'id',
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
            related = instance.supporters.through.objects.all().order_by('-id')[:5]
            petition_list = []
            for obj in related:
                petition = Petition.objects.get(id=obj.petition_id) # Using through only returns object id
                petition_list.append(petition)
            serializer = UserSerializer(petition_list, context=self.context)
            recent_supporters = serializer.data
        return recent_supporters
