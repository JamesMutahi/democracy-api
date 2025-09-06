from rest_framework import serializers

from constitution.models import Section


class SectionSerializer(serializers.ModelSerializer):
    is_bookmarked = serializers.SerializerMethodField(read_only=True)
    subsections = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Section
        fields = [
            'id',
            'position',
            'tag',
            'text',
            'is_title',
            'subsections',
            'is_bookmarked',
        ]

    def get_is_bookmarked(self, obj):
        is_bookmarked = obj.bookmarks.contains(self.context['scope']['user'])
        return is_bookmarked

    def get_subsections(self, obj):
        serializer = SectionSerializer(obj.subsections.all(), many=True, context=self.context)
        return serializer.data
