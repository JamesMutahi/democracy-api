from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.users.models import ProfileVisit
from apps.utils.serializer_user import get_current_user

User = get_user_model()


def get_authenticated_user(context):
    """
    Safely return the authenticated user from serializer context.
    Returns None for anonymous users.
    """
    user = get_current_user(context)
    if user and getattr(user, "is_authenticated", False):
        return user
    return None


def annotate_user_queryset(queryset, user):
    """
    Annotate queryset with counts and current-user relation flags.

    This dramatically reduces N+1 queries when serializing user lists.
    """
    queryset = queryset.annotate(
        following_count=Count("following", distinct=True),
        followers_count=Count("followers", distinct=True),
    )

    if user and getattr(user, "is_authenticated", False):
        queryset = queryset.annotate(
            is_followed=Exists(
                User.objects.filter(pk=user.pk, following__pk=OuterRef("pk"))
            ),
            is_muted=Exists(
                User.objects.filter(pk=user.pk, muted__pk=OuterRef("pk"))
            ),
            is_blocked=Exists(
                User.objects.filter(pk=user.pk, blocked__pk=OuterRef("pk"))
            ),
            has_blocked=Exists(
                User.objects.filter(pk=OuterRef("pk"), blocked__pk=user.pk)
            ),
            is_notifying=Exists(
                User.objects.filter(pk=user.pk, notifiers__pk=OuterRef("pk"))
            ),
            is_visited=Exists(
                ProfileVisit.objects.filter(
                    visitor_id=user.pk,
                    visited_id=OuterRef("pk"),
                )
            ),
        )

    return queryset


class UserSerializer(serializers.ModelSerializer):
    following = serializers.SerializerMethodField(read_only=True)
    followers = serializers.SerializerMethodField(read_only=True)

    is_muted = serializers.SerializerMethodField(read_only=True)
    is_blocked = serializers.SerializerMethodField(read_only=True)
    has_blocked = serializers.SerializerMethodField(read_only=True)
    is_followed = serializers.SerializerMethodField(read_only=True)
    is_notifying = serializers.SerializerMethodField(read_only=True)
    is_visited = serializers.SerializerMethodField(read_only=True)

    email = serializers.SerializerMethodField(read_only=True)

    county = CountySerializer(read_only=True)
    constituency = ConstituencySerializer(read_only=True)
    ward = WardSerializer(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "email",
            "image",
            "cover_photo",
            "bio",
            "county",
            "constituency",
            "ward",
            "date_joined",
            "is_active",
            "following",
            "followers",
            "is_muted",
            "is_blocked",
            "has_blocked",
            "is_followed",
            "is_notifying",
            "is_visited",
        )
        read_only_fields = fields

    def get_following(self, obj):
        value = getattr(obj, "following_count", None)
        if value is not None:
            return value
        return obj.following.count()

    def get_followers(self, obj):
        value = getattr(obj, "followers_count", None)
        if value is not None:
            return value
        return obj.followers.count()

    def get_email(self, obj):
        """
        Only expose email to the owner or staff users.
        """
        current_user = get_authenticated_user(self.context)

        if not current_user:
            return None

        if current_user.pk == obj.pk or current_user.is_staff:
            return obj.email

        return None

    def get_is_muted(self, obj):
        current_user = get_authenticated_user(self.context)

        if not current_user or current_user.pk == obj.pk:
            return False

        annotated = getattr(obj, "is_muted", None)
        if annotated is not None:
            return bool(annotated)

        return current_user.muted.filter(pk=obj.pk).exists()

    def get_is_blocked(self, obj):
        current_user = get_authenticated_user(self.context)

        if not current_user or current_user.pk == obj.pk:
            return False

        annotated = getattr(obj, "is_blocked", None)
        if annotated is not None:
            return bool(annotated)

        return current_user.blocked.filter(pk=obj.pk).exists()

    def get_has_blocked(self, obj):
        current_user = get_authenticated_user(self.context)

        if not current_user or current_user.pk == obj.pk:
            return False

        annotated = getattr(obj, "has_blocked", None)
        if annotated is not None:
            return bool(annotated)

        return obj.blocked.filter(pk=current_user.pk).exists()

    def get_is_followed(self, obj):
        current_user = get_authenticated_user(self.context)

        if not current_user or current_user.pk == obj.pk:
            return False

        annotated = getattr(obj, "is_followed", None)
        if annotated is not None:
            return bool(annotated)

        return current_user.following.filter(pk=obj.pk).exists()

    def get_is_notifying(self, obj):
        current_user = get_authenticated_user(self.context)

        if not current_user or current_user.pk == obj.pk:
            return False

        annotated = getattr(obj, "is_notifying", None)
        if annotated is not None:
            return bool(annotated)

        return current_user.notifiers.filter(pk=obj.pk).exists()

    def get_is_visited(self, obj):
        current_user = get_authenticated_user(self.context)

        if not current_user or current_user.pk == obj.pk:
            return False

        annotated = getattr(obj, "is_visited", None)
        if annotated is not None:
            return bool(annotated)

        return ProfileVisit.objects.filter(
            visitor_id=current_user.pk,
            visited_id=obj.pk,
        ).exists()


class UserUpdateSerializer(serializers.ModelSerializer):
    MAX_IMAGE_SIZE = 5 * 1024 * 1024
    MAX_COVER_SIZE = 8 * 1024 * 1024

    class Meta:
        model = User
        fields = (
            "name",
            "image",
            "cover_photo",
            "bio",
        )

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise ValidationError("Name cannot be blank.")

        return value

    def validate_image(self, value):
        if value and hasattr(value, "size") and value.size > self.MAX_IMAGE_SIZE:
            raise ValidationError("Profile image must be less than 5MB.")

        return value

    def validate_cover_photo(self, value):
        if value and hasattr(value, "size") and value.size > self.MAX_COVER_SIZE:
            raise ValidationError("Cover photo must be less than 8MB.")

        return value
