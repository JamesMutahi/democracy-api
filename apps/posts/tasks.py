from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.posts.models import Asset, Post


@shared_task
def cleanup_incomplete_uploads():
    """
    Cleans up incomplete uploads and the empty posts they belong to.
    """
    cutoff = timezone.now() - timedelta(hours=24)

    # 1. Find incomplete assets older than 24 hours
    orphaned_assets = Asset.objects.filter(
        is_completed=False,
        created_at__lt=cutoff
    )

    # Get the IDs of the posts these orphaned assets belong to
    abandoned_post_ids = list(orphaned_assets.values_list('post_id', flat=True).distinct())

    # 2. Delete the assets.
    # This triggers the post_delete signal, which safely wipes the partial files from S3!
    orphaned_assets.delete()

    # 3. Delete the empty, inactive posts associated with those assets
    # We only delete them if they have NO completed assets and are still inactive.
    Post.objects.filter(
        id__in=abandoned_post_ids,
        is_active=False,
        assets__isnull=True  # Ensure all assets were deleted
    ).delete()
