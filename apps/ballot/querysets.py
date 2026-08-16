from django.db.models import (
    Count,
    IntegerField,
    OuterRef,
    Prefetch,
    Subquery,
    TextField,
)

from apps.ballot.models import BallotVote, Option, Reason


def annotate_ballot_metrics(queryset, user):
    """
    Annotate ballot queryset with counts and user-specific flags.
    """
    voted_option_id = Subquery(
        BallotVote.objects.filter(
            user=user,
            ballot=OuterRef("pk"),
        )
        .order_by("-voted_at", "-id")
        .values("option_id")[:1],
        output_field=IntegerField(),
    )

    user_reason = Subquery(
        Reason.objects.filter(
            user=user,
            ballot=OuterRef("pk"),
        )
        .order_by("-id")
        .values("text")[:1],
        output_field=TextField(),
    )

    return queryset.annotate(
        total_votes=Count("votes", distinct=True),
        voted_option_id=voted_option_id,
        user_reason=user_reason,
    ).prefetch_related(
        Prefetch(
            "options",
            queryset=Option.objects.annotate(
                vote_count=Count("votes", distinct=True),
            ).order_by("number", "id"),
        ),
    ).select_related(
        "county",
        "constituency",
        "ward",
        "summary",
    ).order_by(
        "-start_time",
        "-id",
    )
