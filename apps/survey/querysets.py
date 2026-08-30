from django.db.models import Count, Prefetch

from apps.survey.models import Response


def annotate_survey_metrics(queryset, user):
    """
    Annotate survey queryset with counts and user-specific flags.
    """

    return queryset.select_related(
        'county', 'constituency', 'ward', 'summary'
    ).prefetch_related(
        'pages__questions__choices',
        Prefetch(
            'responses',
            queryset=Response.objects.filter(user=user),
            to_attr='user_response'
        )
    ).annotate(
        total_responses_count=Count('responses', distinct=True)
    )
