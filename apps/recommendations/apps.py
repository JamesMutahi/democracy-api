from django.apps import AppConfig


class RecommendationsConfig(AppConfig):
    name = 'apps.recommendations'

    def ready(self):
        import apps.recommendations.signals
