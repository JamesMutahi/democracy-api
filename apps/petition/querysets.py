from django.db.models import Count, Exists, OuterRef

from apps.petition.models import PetitionSupport


def annotate_petition_metrics(queryset, user):
    """
    Annotate petition queryset with counts and user-specific flags.
    """

    return queryset.select_related(
        "author",
        "county",
        "constituency",
        "ward",
    ).annotate(
        supporters_count=Count("supporters", distinct=True),
        is_supported=Exists(
            PetitionSupport.objects.filter(
                petition_id=OuterRef("pk"),
                user_id=user.pk,
            )
        ),
    ).order_by(
        "-created_at",
        "-id",
    )
