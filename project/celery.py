import os

from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')

app = Celery('project')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'refresh-follow-recommendations-every-hour': {
        'task': 'apps.recommendations.tasks.refresh_all_active_users',
        'schedule': crontab(hour='*/1'),  # Every 1 hour
    },
    'cleanup-broadcast-participants-every-5-min': {
        'task': 'apps.broadcast.tasks.cleanup_broadcast_participants',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
        # 'schedule': timedelta(minutes=5),       # Alternative
    },
    'daily-broadcast-cleanup': {
        'task': 'apps.broadcast.tasks.cleanup_broadcast_participants',
        'schedule': crontab(hour=3, minute=0),  # Every day at 3:00 AM
    },
}
