import logging
from typing import List

import redis
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.meeting.models import Meeting

logger = logging.getLogger(__name__)

# ====================== CONNECTION POOL ======================
# Create a connection pool at module level
redis_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    max_connections=50,  # Adjust based on traffic
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
)

# Global Redis client using the pool
redis_client = redis.Redis(connection_pool=redis_pool)


class MeetingParticipantService:
    """
    High-performance participant tracking using Redis Sets + Connection Pooling.
    """

    PREFIX = "meeting:participants:"
    USER_MEETINGS_PREFIX = "user:meetings:"

    @staticmethod
    def _get_key(meeting_id: int) -> str:
        return f"{MeetingParticipantService.PREFIX}{meeting_id}"

    @staticmethod
    def _get_user_meetings_key(user_id: int) -> str:
        return f"{MeetingParticipantService.USER_MEETINGS_PREFIX}{user_id}"

    @staticmethod
    def invalidate_participants_cache(meeting_id: int):
        cache_key = f"meeting_participants_serialized_{meeting_id}"
        cache.delete(cache_key)

    @staticmethod
    def user_joined(meeting_id: int, user_id: int, ttl_seconds: int = 7200):
        """Add user to the meeting + track meeting for the user"""
        meeting_key = MeetingParticipantService._get_key(meeting_id)
        user_meetings_key = MeetingParticipantService._get_user_meetings_key(user_id)
        user_id_str = str(user_id)  # Ensure string consistency

        try:
            with redis_client.pipeline() as pipe:
                pipe.sadd(meeting_key, user_id_str)
                pipe.sadd(user_meetings_key, meeting_id)
                pipe.expire(meeting_key, ttl_seconds)
                pipe.expire(user_meetings_key, ttl_seconds)
                pipe.execute()
            MeetingParticipantService.invalidate_participants_cache(meeting_id)
            logger.debug(f"User {user_id} joined meeting {meeting_id}")
        except Exception as e:
            logger.error(f"Error in user_joined: {e}")

    @staticmethod
    def user_left(meeting_id: int, user_id: int):
        """Remove user from meeting and from their meeting list"""
        meeting_key = MeetingParticipantService._get_key(meeting_id)
        user_meetings_key = MeetingParticipantService._get_user_meetings_key(user_id)
        user_id_str = str(user_id)

        try:
            with redis_client.pipeline() as pipe:
                pipe.srem(meeting_key, user_id_str)
                pipe.srem(user_meetings_key, meeting_id)
                pipe.scard(meeting_key)
                results = pipe.execute()

                remaining = results[2]
                if remaining == 0:
                    redis_client.delete(meeting_key)
            MeetingParticipantService.invalidate_participants_cache(meeting_id)
            logger.debug(f"User {user_id} left meeting {meeting_id}")
        except Exception as e:
            logger.error(f"Error in user_left: {e}")

    @staticmethod
    def get_all_participant_ids(meeting_id: int) -> List[int]:
        """Get all participant IDs"""
        key = MeetingParticipantService._get_key(meeting_id)
        try:
            members = redis_client.smembers(key)
            return [int(uid) for uid in members]
        except Exception as e:
            logger.error(f"Error fetching participants for meeting {meeting_id}: {e}")
            return []

    @staticmethod
    def get_participant_count(meeting_id: int) -> int:
        """Fast participant count"""
        key = MeetingParticipantService._get_key(meeting_id)
        try:
            return redis_client.scard(key)
        except Exception as e:
            logger.error(f"Error getting count for meeting {meeting_id}: {e}")
            return 0

    @staticmethod
    def is_participant(meeting_id: int, user_id: int) -> bool:
        key = MeetingParticipantService._get_key(meeting_id)
        try:
            return bool(redis_client.sismember(key, str(user_id)))
        except Exception:
            return False


    # ====================== MUTED PARTICIPANTS ======================

    # Mute tracking using Redis Sets
    @staticmethod
    def _get_muted_key(meeting_id: int) -> str:
        return f"meeting:muted:{meeting_id}"

    @staticmethod
    def set_mute_status(meeting_id: int, user_id: int, is_muted: bool):
        """Set mute status for a user in a meeting"""
        muted_key = MeetingParticipantService._get_muted_key(meeting_id)
        user_id_str = str(user_id)

        try:
            with redis_client.pipeline() as pipe:
                if is_muted:
                    pipe.sadd(muted_key, user_id_str)
                else:
                    pipe.srem(muted_key, user_id_str)
                pipe.expire(muted_key, 7200)
                pipe.execute()

            MeetingParticipantService.signal_meeting(Meeting.objects.get(id=meeting_id))
            logger.debug(f"User {user_id} muted={is_muted} in meeting {meeting_id}")

            return True  # Return success indicator

        except Exception as e:
            logger.error(f"Error setting mute status for user {user_id} in meeting {meeting_id}: {e}")
            return False

    @staticmethod
    def get_muted_users(meeting_id: int) -> List[int]:
        """Get list of muted user IDs"""
        muted_key = MeetingParticipantService._get_muted_key(meeting_id)
        try:
            members = redis_client.smembers(muted_key)
            return [int(uid) for uid in members]
        except Exception as e:
            logger.error(f"Error fetching muted users: {e}")
            return []

    @staticmethod
    def is_muted(meeting_id: int, user_id: int) -> bool:
        muted_key = MeetingParticipantService._get_muted_key(meeting_id)
        try:
            return bool(redis_client.sismember(muted_key, str(user_id)))
        except Exception:
            return False


    # ====================== CLEANUP ======================

    @staticmethod
    def cleanup_meeting(meeting_id: int):
        key = MeetingParticipantService._get_key(meeting_id)
        try:
            redis_client.delete(key)
            MeetingParticipantService.invalidate_participants_cache(meeting_id)
            logger.info(f"Cleaned up meeting {meeting_id}")
        except Exception as e:
            logger.error(f"Cleanup failed for meeting {meeting_id}: {e}", exc_info=True)

    @staticmethod
    def cleanup_user_from_all_meetings(user_id: int):
        """Fast cleanup using per-user meeting tracking"""
        logger.info(f"✅ Cleaning up user {user_id} from meetings")
        user_meetings_key = MeetingParticipantService._get_user_meetings_key(user_id)
        user_id_str = str(user_id)

        try:
            meeting_ids = redis_client.smembers(user_meetings_key)
            if not meeting_ids:
                logger.debug(f"User {user_id} has no active meetings to clean")
                return

            pipeline = redis_client.pipeline()
            cleaned_meetings = 0
            affected_meeting_ids = []

            for mid in meeting_ids:
                mid = int(mid)
                meeting_key = MeetingParticipantService._get_key(mid)
                pipeline.srem(meeting_key, user_id_str)
                cleaned_meetings += 1
                affected_meeting_ids.append(mid)

            # Remove user from their tracking set
            pipeline.delete(user_meetings_key)
            pipeline.execute()

            logger.info(f"✅ Cleaned up user {user_id} from {cleaned_meetings} meetings")

            # Invalidate cache for affected meetings
            for mid in affected_meeting_ids:
                MeetingParticipantService.invalidate_participants_cache(mid)

            # Trigger real-time updates for affected meetings
            MeetingParticipantService._signal_meetings(affected_meeting_ids)

        except Exception as e:
            logger.error(f"Error cleaning up user {user_id} from all meetings: {e}", exc_info=True)

    @staticmethod
    def _signal_meetings(meeting_ids: list):
        """Trigger post_save signal for multiple meetings to update connected clients"""
        from apps.meeting.models import Meeting  # Avoid circular import

        if not meeting_ids:
            return

        try:
            # Fetch meetings with minimal data
            meetings = Meeting.objects.filter(id__in=meeting_ids).select_related('host')

            for meeting in meetings:
                try:
                    MeetingParticipantService.signal_meeting(meeting)
                except Exception as e:
                    logger.warning(f"Failed to signal meeting {meeting.id}: {e}")

            logger.debug(f"Triggered real-time update for {len(meetings)} meetings after disconnect")
        except Exception as e:
            logger.error(f"Error signaling meetings after cleanup: {e}", exc_info=True)

    @staticmethod
    def signal_meeting(meeting: Meeting):
        """Public method to trigger post_save signal"""
        from django.db.models.signals import post_save
        post_save.send(sender=Meeting, instance=meeting, created=False)

    @staticmethod
    def cleanup_all_inactive() -> int:
        """Background cleanup - removes empty participant sets"""
        cleaned = 0
        try:
            pattern = f"{MeetingParticipantService.PREFIX}*"
            for key in redis_client.scan_iter(match=pattern, count=100):
                try:
                    if redis_client.scard(key) == 0:
                        redis_client.delete(key)
                        cleaned += 1
                except Exception:
                    continue
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} empty meeting participant sets")
            return cleaned
        except Exception as e:
            logger.error(f"Background cleanup error: {e}", exc_info=True)
            return cleaned

    # ====================== LOCKING ======================
    LOCK_PREFIX = "lock:meeting_cleanup:"

    @staticmethod
    def acquire_cleanup_lock(timeout_seconds: int = 300) -> tuple[bool, str | None]:
        """
        Acquire a distributed lock for cleanup task.
        Returns (acquired: bool, lock_value: str | None)
        """
        lock_key = f"{MeetingParticipantService.LOCK_PREFIX}global"
        # Unique lock value to safely identify our lock
        lock_value = f"cleanup-task-{int(timezone.now().timestamp() * 1000)}"

        try:
            acquired = redis_client.set(
                lock_key,
                lock_value,
                nx=True,
                ex=timeout_seconds
            )
            return bool(acquired), lock_value if acquired else None
        except Exception as e:
            logger.error(f"Failed to acquire cleanup lock: {e}")
            return False, None

    @staticmethod
    def release_cleanup_lock(lock_value: str = None):
        """Safely release the cleanup lock"""
        lock_key = f"{MeetingParticipantService.LOCK_PREFIX}global"
        try:
            if lock_value:
                # Only delete if it's our lock (double-check)
                current_value = redis_client.get(lock_key)
                if current_value == lock_value:
                    redis_client.delete(lock_key)
            else:
                # Fallback: just delete
                redis_client.delete(lock_key)
        except Exception as e:
            logger.error(f"Failed to release cleanup lock: {e}")
