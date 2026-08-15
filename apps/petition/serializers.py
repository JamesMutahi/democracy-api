from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.geo.models import County, Constituency, Ward
from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.petition.models import Petition, PetitionSupport
from apps.users.serializers import UserSerializer, SimpleUserSerializer
from apps.utils.serializer_user import get_current_user

User = get_user_model()


class PetitionSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)

    supporters = serializers.SerializerMethodField()
    recent_supporters = serializers.SerializerMethodField()
    is_supported = serializers.SerializerMethodField()

    county = CountySerializer(read_only=True)
    county_id = serializers.PrimaryKeyRelatedField(
        queryset=County.objects.all(),
        write_only=True,
        source="county",
        required=False,
    )

    constituency = ConstituencySerializer(read_only=True)
    constituency_id = serializers.PrimaryKeyRelatedField(
        queryset=Constituency.objects.all(),
        write_only=True,
        source="constituency",
        required=False,
    )

    ward = WardSerializer(read_only=True)
    ward_id = serializers.PrimaryKeyRelatedField(
        queryset=Ward.objects.all(),
        write_only=True,
        source="ward",
        required=False,
    )

    class Meta:
        model = Petition
        fields = [
            "id",
            "author",
            "title",
            "description",
            "county",
            "county_id",
            "constituency",
            "constituency_id",
            "ward",
            "ward_id",
            "image",
            "video",
            "views",
            "supporters",
            "recent_supporters",
            "is_supported",
            "is_open",
            "created_at",
            "is_active",
        ]
        extra_kwargs = {
            "is_open": {"read_only": True},
            "is_active": {"read_only": True},
            "views": {"read_only": True},
        }

    @staticmethod
    def get_supporters(instance: Petition) -> int:
        """
        Prefer annotated supporters_count if present.
        This avoids an extra COUNT query per petition in list views.
        """
        if hasattr(instance, "supporters_count"):
            return instance.supporters_count
        return instance.supporters.count()

    @staticmethod
    def get_recent_supporters(instance: Petition):
        """
        Efficiently fetch the latest 5 supporters using the through model.
        """
        return recent_supporters(petition_id=instance.pk)

    def get_has_supported(self, instance: Petition) -> bool:
        """
        Prefer annotated value if present.
        Falls back to a safe DB check.
        """
        if hasattr(instance, "is_supported"):
            return instance.is_supported

        user = get_current_user(self.context)

        return instance.supporters.filter(pk=user.pk).exists()

    def validate(self, attrs):
        """
        Basic geographic hierarchy validation.

        If a ward is provided, it should have a constituency.
        If a constituency is provided, it should have a county.
        Also validate relationships where the FK fields exist.
        """
        county = attrs.get("county", getattr(self.instance, "county", None))
        constituency = attrs.get(
            "constituency",
            getattr(self.instance, "constituency", None),
        )
        ward = attrs.get("ward", getattr(self.instance, "ward", None))

        if ward and not constituency:
            raise serializers.ValidationError({
                "ward": "Constituency is required when a ward is provided.",
            })

        if constituency and not county:
            raise serializers.ValidationError({
                "constituency": "County is required when a constituency is provided.",
            })

        if county and constituency:
            constituency_county_id = getattr(constituency, "county_id", None)
            if constituency_county_id and constituency_county_id != county.pk:
                raise serializers.ValidationError({
                    "constituency": "Constituency must belong to the selected county.",
                })

        if constituency and ward:
            ward_constituency_id = getattr(ward, "constituency_id", None)
            if ward_constituency_id and ward_constituency_id != constituency.pk:
                raise serializers.ValidationError({
                    "ward": "Ward must belong to the selected constituency.",
                })

        return attrs

    def create(self, validated_data):
        user = get_current_user(self.context)
        validated_data["author"] = user
        validated_data["is_open"] = True
        return super().create(validated_data)


def recent_supporters(petition_id: int):
    """
    Efficiently fetch the latest 5 supporters using the through model.
    """
    supports = (
        PetitionSupport.objects.filter(petition_id=petition_id)
        .select_related("user")
        .order_by("-supported_at", "-id")[:5]
    )

    users = [support.user for support in supports]
    return SimpleUserSerializer(users, many=True).data
