from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.geo.models import County, Constituency, Ward
from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.petition.models import Petition
from apps.users.serializers import UserSerializer
from apps.utils.serializer_user import get_current_user

User = get_user_model()


class PetitionSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    supporters = serializers.SerializerMethodField(read_only=True)
    recent_supporters = serializers.SerializerMethodField(read_only=True)
    is_supported = serializers.SerializerMethodField(read_only=True)
    county = CountySerializer(read_only=True)
    county_id = serializers.PrimaryKeyRelatedField(
        queryset=County.objects.all(),
        write_only=True,
        source='county',
        required=False
    )
    constituency = ConstituencySerializer(read_only=True)
    constituency_id = serializers.PrimaryKeyRelatedField(
        queryset=Constituency.objects.all(),
        write_only=True,
        source='constituency',
        required=False
    )
    ward = WardSerializer(read_only=True)
    ward_id = serializers.PrimaryKeyRelatedField(
        queryset=Ward.objects.all(),
        write_only=True,
        source='ward',
        required=False
    )

    class Meta:
        model = Petition
        fields = [
            'id',
            'author',
            'title',
            'description',
            'county',
            'county_id',
            'constituency',
            'constituency_id',
            'ward',
            'ward_id',
            'image',
            'video',
            'views',
            'supporters',
            'recent_supporters',
            'is_supported',
            'is_open',
            'created_at',
            'is_active',
        ]
        extra_kwargs = {'is_active': {'read_only': True}}

    @staticmethod
    def get_supporters(instance: Petition):
        return instance.supporters.count()

    def get_recent_supporters(self, instance: Petition):
        recent_supporters = []
        if instance.supporters.exists():
            # Using through only returns object id
            related = instance.supporters.through.objects.filter(petition_id=instance.pk).order_by('-id')[:5]
            user_list = []
            for obj in related:
                user = User.objects.get(id=obj.user.id)
                user_list.append(user)
            serializer = UserSerializer(user_list, many=True, context=self.context)
            recent_supporters = serializer.data
        return recent_supporters

    def get_is_supported(self, instance: Petition):
        user = get_current_user(self.context)
        is_supported = instance.supporters.contains(user)
        return is_supported

    def create(self, validated_data):
        validated_data['author'] = get_current_user(self.context)
        validated_data['is_open'] = True
        return super().create(validated_data)
