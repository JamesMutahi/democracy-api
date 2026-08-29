from django.db.models import Count


def annotate_broadcast_metrics(queryset):
    """
    Annotate broadcast queryset with counts and user-specific flags.
    """

    return queryset.select_related(
        "host",
        "county",
        "constituency",
        "ward",
    ).prefetch_related(
        "speaker_invites",
        "co_hosts",
        "speakers",
        "comments",
        "recording_sessions",
    ).annotate(
        comments_count=Count("comments", distinct=True),
    ).order_by(
        "-created_at",
        "-id",
    )
