from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.chat.models import Asset


@shared_task
def cleanup_incomplete_uploads():
    cutoff = timezone.now() - timedelta(hours=24)

    # Finds incomplete assets created more than 24 hours ago
    orphaned_assets = Asset.objects.filter(
        is_completed=False,
        created_at__lt=cutoff
    )

    # Calling .delete() triggers the post_delete signal, cleaning up S3!
    orphaned_assets.delete()
