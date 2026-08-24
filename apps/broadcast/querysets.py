def annotate_broadcast_metrics(queryset, user):
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
        "recording_sessions",
    ).order_by(
        "-created_at",
        "-id",
    )
