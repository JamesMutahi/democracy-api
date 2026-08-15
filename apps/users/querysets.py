from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef

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
    )

    return queryset
