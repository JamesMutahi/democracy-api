import logging

from celery import shared_task

from .services import BroadcastParticipantService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def cleanup_broadcast_participants(self):
    """
    Periodic task to clean up empty broadcast participant sets with distributed locking.
    """
    lock_acquired = False
    lock_value = None

    try:
        lock_acquired, lock_value = BroadcastParticipantService.acquire_cleanup_lock(
            timeout_seconds=300
        )

        if not lock_acquired:
            logger.info("⏭️ Cleanup task skipped - another instance is already running")
            return "skipped"

        logger.info("🔒 Cleanup lock acquired - starting participant cleanup")

        cleaned = BroadcastParticipantService.cleanup_all_inactive()

        if cleaned > 0:
            logger.info(f"✅ Cleaned {cleaned} empty broadcast participant sets")
        else:
            logger.debug("No empty participant sets found")

        return {"status": "success", "cleaned": cleaned}

    except Exception as exc:
        logger.error(f"❌ Participant cleanup task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)

    finally:
        if lock_acquired:
            BroadcastParticipantService.release_cleanup_lock(lock_value)
            logger.debug("🔓 Cleanup lock released")


@shared_task
def cleanup_user_from_all_broadcasts(user_id: int):
    """Task version for manual triggering if needed"""
    try:
        BroadcastParticipantService.cleanup_user_from_all_broadcasts(user_id)
        logger.info(f"Cleaned up user {user_id} from all broadcasts via task")
    except Exception as e:
        logger.error(f"Failed to cleanup user {user_id}: {e}", exc_info=True)


@shared_task(bind=True, max_retries=1)
def check_recording_status(self):
    import requests
    from django.utils import timezone
    from django.conf import settings
    from apps.broadcast.models import RecordingSession
    from apps.broadcast.views import get_agora_headers

    sessions = RecordingSession.objects.filter(stopped_at__isnull=True)  # Only active sessions

    # Query Agora
    for session in sessions:
        try:
            query_url = f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{session.resource_id}/sid/{session.sid}/mode/mix/query"

            query_resp = requests.get(query_url, headers=get_agora_headers())
            query_resp.raise_for_status()
            result = query_resp.json()

            logger.info(result)

            # Update local record with latest info
            session.file_list = result['serverResponse']['fileList']
            if result.get('status') == 'stopped':  # or check serverResponse
                session.stopped_at = timezone.now()
                session.status = RecordingSession.Status.STOPPED
            session.save()

        except Exception as exc:
            logger.error(f"❌ Check recording status task failed: {exc}", exc_info=True)
            raise self.retry(exc=exc)
