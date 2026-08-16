import os

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

app = Celery("project")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# ─────────────────────────────────────────────
# Queue topology
# ─────────────────────────────────────────────

app.conf.task_default_queue = "celery"

# Explicitly declare the queues we use.
app.conf.task_queues = (
    Queue("celery"),
    Queue("summarization"),
)

# If you later add more queues dynamically, keep this enabled.
app.conf.task_create_missing_queues = True

# If a task runs longer than the visibility timeout, Redis may redeliver it to another worker.
# For LLM summarization, this is important.
app.conf.broker_transport_options = {
    "visibility_timeout": 60 * 60 * 12,  # 12 hours
}

# ─────────────────────────────────────────────
# Task routing
# ─────────────────────────────────────────────

app.conf.task_routes = {
    # Cheap scanner task stays on the normal queue.
    # It only finds ended ballots and enqueues summarization jobs.
    "apps.ballot.tasks.scan_ended_ballots_for_summarization": {
        "queue": "celery",
    },

    # Expensive local Qwen LLM summarization task goes to its own queue.
    "apps.ballot.tasks.summarize_ballot_task": {
        "queue": "summarization",
    },
}

# ─────────────────────────────────────────────
# Long-running task safety
# ─────────────────────────────────────────────

# If a worker dies, late-ack tasks can be redelivered.
app.conf.task_acks_late = True

# Avoid workers prefetching many long summarization tasks at once.
app.conf.worker_prefetch_multiplier = 1

# ─────────────────────────────────────────────
# Beat schedule
# ─────────────────────────────────────────────

app.conf.beat_schedule = {
    "refresh-follow-recommendations-every-hour": {
        "task": "apps.recommendations.tasks.refresh_all_active_users",
        "schedule": crontab(hour="*/1"),
    },

    "cleanup-broadcast-participants-every-5-min": {
        "task": "apps.broadcast.tasks.cleanup_broadcast_participants",
        "schedule": crontab(minute="*/5"),
    },

    "daily-broadcast-cleanup": {
        "task": "apps.broadcast.tasks.cleanup_broadcast_participants",
        "schedule": crontab(hour=3, minute=0),
    },

    "check-recording-status-every-1-min": {
        "task": "apps.broadcast.tasks.check_recording_status",
        "schedule": crontab(minute="*/1"),
    },

    # Scan for ended ballots and enqueue summarization jobs.
    "scan-ended-ballots-for-summarization-every-1-min": {
        "task": "apps.ballot.tasks.scan_ended_ballots_for_summarization",
        "schedule": crontab(minute="*/1"),
    },
}
