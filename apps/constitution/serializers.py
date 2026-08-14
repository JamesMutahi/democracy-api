from rest_framework import serializers

from apps.constitution.models import Section


class SectionSerializer(serializers.ModelSerializer):
    parent_count = serializers.SerializerMethodField()

    class Meta:
        model = Section
        fields = [
            "id",
            "numeral",
            "text",
            "is_title",
            "parent",
            "parent_count",
        ]
        read_only_fields = [
            "id",
            "parent_count",
        ]

    def get_parent_count(self, obj: Section) -> int:
        """
        Prefer the precomputed depth map provided by the consumer.

        This avoids repeated parent traversal queries during list serialization.
        """
        depth_map = self.context.get("section_depth")

        if depth_map is not None:
            return depth_map.get(obj.pk, 0)

        # Fallback for retrieve or cases where the depth map was not provided.
        # This is cycle-safe, but it can still cause DB queries if parents are
        # not already loaded.
        count = 0
        seen = set()
        current = obj

        while current.parent_id:
            if current.pk in seen:
                break

            seen.add(current.pk)

            try:
                current = current.parent
            except Section.DoesNotExist:
                break

            count += 1

        return count
