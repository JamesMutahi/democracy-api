from django.db.models import Count, IntegerField, OuterRef, Prefetch, Subquery

from apps.ballot.models import OptionVote, Option, Reason


def annotate_ballot_metrics(queryset, user):
    """
    Annotate ballot queryset with counts and user-specific flags.
    """
    queryset = queryset.annotate(
        total_votes=Count("options__votes_through", distinct=True),
        voted_option_id=Subquery(
            OptionVote.objects.filter(
                user=user,
                option__ballot=OuterRef("pk"),
            )
            .order_by("-voted_at")
            .values("option_id")[:1],
            output_field=IntegerField(),
        ),
    ).prefetch_related(
        Prefetch(
            "options",
            queryset=Option.objects.annotate(vote_count=Count("votes")),
        ),
        Prefetch(
            "options__votes_through",
            queryset=OptionVote.objects.select_related("user"),
        ),
        Prefetch(
            "reasons",
            queryset=Reason.objects.filter(user=user).select_related("user"),
            to_attr="user_reason",
        ),
    ).select_related(
        "county", "constituency", "ward",
    ).order_by(
        "-start_time", "-id",
    )

    return queryset
