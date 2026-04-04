from rest_framework import serializers

from apps.constitution.models import Section


class SectionSerializer(serializers.ModelSerializer):
    parent_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Section
        fields = [
            'id',
            'position',
            'tag',
            'numeral',
            'text',
            'is_title',
            'parent_count',
        ]
        ordering = ['parent__position']

    @staticmethod
    def get_parent_count(obj):
        count = 0
        while obj.parent:
            count += 1
            obj = obj.parent
        return count
