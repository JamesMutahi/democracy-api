from django.utils.translation import gettext_lazy as _
from grappelli.dashboard import modules, Dashboard


class CustomIndexDashboard(Dashboard):
    def __init__(self, **kwargs):
        Dashboard.__init__(self, **kwargs)
        self.children.append(modules.ModelList(
            title=_('Social media'),
            column=1,
            collapsible=True,
            models=(
                'apps.users.models.User', 'apps.posts.models.Post', 'apps.posts.models.Report',
                'apps.chat.models.Chat', 'apps.notification.models.Notification',
                'apps.notification.models.Preferences',),
        ))
        self.children.append(modules.ModelList(
            title=_('Hub'),
            column=1,
            collapsible=True,
            models=('apps.ballot.models.Ballot', 'apps.petition.models.Petition', 'apps.broadcast.models.Broadcast',
                    'apps.survey.models.Survey', 'apps.survey.models.Response',),
        ))
        self.children.append(modules.ModelList(
            title=_('Miscellaneous'),
            column=1,
            collapsible=True,
            models=('django.contrib.sites.models.Site', 'apps.geo.models.County', 'apps.geo.models.Constituency',
                    'apps.geo.models.Ward', 'apps.constitution.models.Section',),
        ))
        self.children.append(modules.ModelList(
            title=_('Recommendations'),
            column=1,
            collapsible=True,
            models=('apps.recommendations.models.UserInteraction',
                    'apps.recommendations.models.PostRecommendationCache',
                    'apps.recommendations.models.FollowRecommendationCache'),
        ))
        self.children.append(modules.ModelList(
            _('Task Scheduling & Results'),
            column=1,
            css_classes=('collapse closed',),
            models=(
                'django_celery_beat.models.PeriodicTask',
                'django_celery_beat.models.IntervalSchedule',
                'django_celery_beat.models.CrontabSchedule',
                'django_celery_results.models.TaskResult',
            ),
        ))

        # append a recent actions module
        self.children.append(modules.RecentActions(
            title=_('Recent actions'),
            column=2,
            collapsible=True,
            limit=5,
        ))

    # class Media:
    #     css = {
    #         'all': (
    #             'css/dashboard.css',
    #             'css/styles.css',
    #         ),
    #     }
    #     js = (
    #         'js/dashboard.js',
    #         'js/script.js',
    #     )
