from django.utils.translation import gettext_lazy as _
from grappelli.dashboard import modules, Dashboard


class CustomIndexDashboard(Dashboard):
    def __init__(self, **kwargs):
        Dashboard.__init__(self, **kwargs)
        # self.children.append(modules.AppList(
        #     title=_(''),
        #     column=1,
        #     collapsible=True,
        #     models=('django.contrib.sites.models.Site',),
        # ))
        self.children.append(modules.ModelList(
            title=_('General'),
            column=1,
            collapsible=True,
            models=('ballot.models.Ballot', 'survey.models.Survey', 'users.models.CustomUser', 'posts.models.Report'),
        ))
        self.children.append(modules.ModelList(
            title=_('User data'),
            column=1,
            collapsible=True,
            models=(
            'posts.models.Post', 'petition.models.Petition', 'chat.models.Chat', 'notification.models.Notification',),
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
