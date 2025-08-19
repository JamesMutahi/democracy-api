from rest_framework import serializers

from petition.models import Petition
from users.serializers import UserSerializer


class PetitionSerializer(serializers.ModelSerializer):
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

    @staticmethod
    def get_supporters(instance: Petition):
        return instance.supporters.count()

    def get_recent_supporters(self, instance: Petition):
        recent_supporters = None
        if instance.supporters.exists():
            related = instance.supporters.through.objects.all().order_by('-id')[:5]
            petition_list = []
            for obj in related:
                petition = Petition.objects.get(id=obj.petition_id) # Using through only returns object id
                petition_list.append(petition)
            serializer = UserSerializer(petition_list, context=self.context)
            recent_supporters = serializer.data
        return recent_supporters
