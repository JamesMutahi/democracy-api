from django.contrib.auth import get_user_model
from django.db.models import Case, Count, Exists, OuterRef, Value, When
from django.db.models import BooleanField, CharField

from apps.notification.models import MessagingPreference
from apps.users.models import ProfileVisit

User = get_user_model()


def annotate_user_queryset(queryset, user):
    """
    Annotate queryset with counts and current-user relation flags.
    This dramatically reduces N+1 queries when serializing user lists.
    """
    queryset = queryset.annotate(
        following_count=Count("following", distinct=True),
        followers_count=Count("followers", distinct=True),
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
        messaging_preference=Case(
            When(
                preferences__messaging_preference__isnull=False,
                then="preferences__messaging_preference",
            ),
            default=Value(MessagingPreference.FOLLOWING),
            output_field=CharField(),
        ),
        can_message_directly=Case(
            # Self or staff can always message
            When(pk=user.pk, then=Value(True)),
            When(
                preferences__messaging_preference=MessagingPreference.ANYONE,
                then=Value(True),
            ),
            When(
                preferences__messaging_preference__isnull=True,
                then=Value(True),
            ),
            # For 'following' preference, check if current user follows them
            When(
                preferences__messaging_preference="following",
                then=Exists(
                    User.objects.filter(
                        pk=user.pk,
                        following__pk=OuterRef("pk"),
                    )
                ),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )
    return queryset