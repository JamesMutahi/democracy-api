from django.utils.translation import gettext_lazy as _
from grappelli.dashboard import modules, Dashboard


class CustomIndexDashboard(Dashboard):
    def __init__(self, **kwargs):
        Dashboard.__init__(self, **kwargs)
        self.children.append(modules.AppList(
            title=_(''),
            column=1,
            collapsible=True,
            models=('django.contrib.sites.models.Site',),
        ))
        self.children.append(modules.AppList(
            title=_(''),
            column=1,
            collapsible=True,
            models=('poll.models.Poll',),
        ))
        self.children.append(modules.AppList(
            title=_(''),
            column=1,
            collapsible=True,
            models=('users.models.CustomUser',),
        ))
        self.children.append(modules.AppList(
            title=_(''),
            column=1,
            collapsible=True,
            models=('posts.models.Post',),
        ))
        self.children.append(modules.AppList(
            title=_(''),
            column=1,
            collapsible=True,
            models=('chat.models.Room',),
        ))
        self.children.append(modules.AppList(
            title=_(''),
            column=1,
            collapsible=True,
            models=('survey.models.Survey', 'survey.models.Question', 'survey.models.Choice', 'survey.models.Response',
                    'survey.models.TextAnswer', 'survey.models.ChoiceAnswer'),
        ))
        # append a recent actions module
        self.children.append(modules.RecentActions(
            title=_('Recent actions'),
            column=2,
            collapsible=False,
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
