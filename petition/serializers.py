from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from rest_framework import serializers

from geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from petition.models import Petition
from users.serializers import UserSerializer
from users.utils.base64_image_field import Base64ImageField

User = get_user_model()


class PetitionSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    image = serializers.SerializerMethodField()
    supporters = serializers.SerializerMethodField(read_only=True)
    recent_supporters = serializers.SerializerMethodField(read_only=True)
    is_supported = serializers.SerializerMethodField(read_only=True)
    image_base64 = Base64ImageField(write_only=True, max_length=None, use_url=True, allow_null=True)
    county = CountySerializer(read_only=True)
    county_id = serializers.IntegerField(write_only=True, allow_null=True)
    constituency = ConstituencySerializer(read_only=True)
    constituency_id = serializers.IntegerField(write_only=True, allow_null=True)
    ward = WardSerializer(read_only=True)
    ward_id = serializers.IntegerField(write_only=True, allow_null=True)

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
            'supporters',
            'recent_supporters',
            'is_supported',
            'is_open',
            'created_at',
            'is_active',
            'image_base64',
        ]

    @staticmethod
    def get_image(obj):
        if obj.image:
            current_site = Site.objects.get_current()
            return current_site.domain + obj.image.url
        return None

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
                user = User.objects.get(id=obj.customuser_id)
                user_list.append(user)
            serializer = UserSerializer(user_list, many=True, context=self.context)
            recent_supporters = serializer.data
        return recent_supporters

    def get_is_supported(self, instance: Petition):
        is_supported = instance.supporters.contains(self.context['scope']['user'])
        return is_supported

    def create(self, validated_data):
        validated_data['author'] = self.context['scope']['user']
        if 'image_base64' in validated_data:
            validated_data['image'] = validated_data.pop('image_base64')
        return super().create(validated_data)
