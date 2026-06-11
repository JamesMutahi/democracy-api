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
