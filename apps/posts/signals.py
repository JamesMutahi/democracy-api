import logging

from django.core.files.storage import default_storage
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Asset

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Asset)
def delete_asset_from_s3(sender, instance, **kwargs):
    """
    Deletes the physical file from S3 when the Asset model instance is deleted.
    """
    if instance.file_key:
        try:
            # default_storage automatically routes to your S3 backend
            # configured in settings.STORAGES (or AWS_S3...)
            if default_storage.exists(instance.file_key):
                default_storage.delete(instance.file_key)
                logger.info(f"Successfully deleted S3 object: {instance.file_key}")
        except Exception as e:
            # Log the error, but DO NOT raise it.
            # We don't want a failed S3 delete to roll back the DB transaction.
            logger.error(f"Failed to delete S3 object {instance.file_key}: {e}")
