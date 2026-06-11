import logging
from typing import List

import redis
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.broadcast.models import Broadcast

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


class BroadcastParticipantService:
    """
    High-performance participant tracking using Redis Sets + Connection Pooling.
    """

    PREFIX = "broadcast:participants:"
    USER_BROADCASTS_PREFIX = "user:broadcasts:"

    @staticmethod
    def _get_key(broadcast_id: int) -> str:
        return f"{BroadcastParticipantService.PREFIX}{broadcast_id}"

    @staticmethod
    def _get_user_broadcasts_key(user_id: int) -> str:
        return f"{BroadcastParticipantService.USER_BROADCASTS_PREFIX}{user_id}"

    @staticmethod
    def invalidate_participants_cache(broadcast_id: int):
        cache_key = f"broadcast_participants_serialized_{broadcast_id}"
        cache.delete(cache_key)

    @staticmethod
    def user_joined(broadcast_id: int, user_id: int, ttl_seconds: int = 7200):
        """Add user to the broadcast + track broadcast for the user"""
        broadcast_key = BroadcastParticipantService._get_key(broadcast_id)
        user_broadcasts_key = BroadcastParticipantService._get_user_broadcasts_key(user_id)
        user_id_str = str(user_id)  # Ensure string consistency

        try:
            with redis_client.pipeline() as pipe:
                pipe.sadd(broadcast_key, user_id_str)
                pipe.sadd(user_broadcasts_key, broadcast_id)
                pipe.expire(broadcast_key, ttl_seconds)
                pipe.expire(user_broadcasts_key, ttl_seconds)
                pipe.execute()
            BroadcastParticipantService.invalidate_participants_cache(broadcast_id)
            logger.debug(f"User {user_id} joined broadcast {broadcast_id}")
        except Exception as e:
            logger.error(f"Error in user_joined: {e}")

    @staticmethod
    def user_left(broadcast_id: int, user_id: int):
        """Remove user from broadcast and from their broadcast list"""
        broadcast_key = BroadcastParticipantService._get_key(broadcast_id)
        user_broadcasts_key = BroadcastParticipantService._get_user_broadcasts_key(user_id)
        user_id_str = str(user_id)

        try:
            with redis_client.pipeline() as pipe:
                pipe.srem(broadcast_key, user_id_str)
                pipe.srem(user_broadcasts_key, broadcast_id)
                pipe.scard(broadcast_key)
                results = pipe.execute()

                remaining = results[2]
                if remaining == 0:
                    redis_client.delete(broadcast_key)
            BroadcastParticipantService.invalidate_participants_cache(broadcast_id)
            logger.debug(f"User {user_id} left broadcast {broadcast_id}")
        except Exception as e:
            logger.error(f"Error in user_left: {e}")

    @staticmethod
    def get_all_participant_ids(broadcast_id: int) -> List[int]:
        """Get all participant IDs"""
        key = BroadcastParticipantService._get_key(broadcast_id)
        try:
            members = redis_client.smembers(key)
            return [int(uid) for uid in members]
        except Exception as e:
            logger.error(f"Error fetching participants for broadcast {broadcast_id}: {e}")
            return []

    @staticmethod
    def get_participant_count(broadcast_id: int) -> int:
        """Fast participant count"""
        key = BroadcastParticipantService._get_key(broadcast_id)
        try:
            return redis_client.scard(key)
        except Exception as e:
            logger.error(f"Error getting count for broadcast {broadcast_id}: {e}")
            return 0

    @staticmethod
    def is_participant(broadcast_id: int, user_id: int) -> bool:
        key = BroadcastParticipantService._get_key(broadcast_id)
        try:
            return bool(redis_client.sismember(key, str(user_id)))
        except Exception:
            return False

    # ====================== MUTED PARTICIPANTS ======================

    # Mute tracking using Redis Sets
    @staticmethod
    def _get_muted_key(broadcast_id: int) -> str:
        return f"broadcast:muted:{broadcast_id}"

    @staticmethod
    def set_mute_status(broadcast_id: int, user_id: int, is_muted: bool):
        """Set mute status for a user in a broadcast"""
        return BroadcastParticipantService.set_mute_status_in_pipeline(broadcast_id=broadcast_id, user_id=user_id,
                                                                       is_muted=is_muted)

    @staticmethod
    def mute_everyone(broadcast: Broadcast, user_id: int):
        """Mute all co_hosts && speakers in a broadcast. Host is not muted."""
        for user in broadcast.co_hosts.all().exclude(id=user_id):
            BroadcastParticipantService.set_mute_status_in_pipeline(broadcast_id=broadcast.id, user_id=user.id,
                                                                    is_muted=True)
        for user in broadcast.speakers.all():
            BroadcastParticipantService.set_mute_status_in_pipeline(broadcast_id=broadcast.id, user_id=user.id,
                                                                    is_muted=True)
        return True

    @staticmethod
    def set_mute_status_in_pipeline(broadcast_id: int, user_id: int, is_muted: bool):
        muted_key = BroadcastParticipantService._get_muted_key(broadcast_id)
        user_id_str = str(user_id)

        try:
            with redis_client.pipeline() as pipe:
                if is_muted:
                    pipe.sadd(muted_key, user_id_str)
                else:
                    pipe.srem(muted_key, user_id_str)
                pipe.expire(muted_key, 7200)
                pipe.execute()
            logger.debug(f"User {user_id} muted={is_muted} in broadcast {broadcast_id}")

            return True  # Return success indicator

        except Exception as e:
            logger.error(f"Error setting mute status for user {user_id} in broadcast {broadcast_id}: {e}")
            return False

    @staticmethod
    def get_muted_users(broadcast_id: int) -> List[int]:
        """Get list of muted user IDs"""
        muted_key = BroadcastParticipantService._get_muted_key(broadcast_id)
        try:
            members = redis_client.smembers(muted_key)
            return [int(uid) for uid in members]
        except Exception as e:
            logger.error(f"Error fetching muted users: {e}")
            return []

    @staticmethod
    def is_muted(broadcast_id: int, user_id: int) -> bool:
        """Check if a user is muted"""
        muted_key = BroadcastParticipantService._get_muted_key(broadcast_id)
        try:
            return bool(redis_client.sismember(muted_key, str(user_id)))
        except Exception:
            return False

    # ====================== CLEANUP ======================

    @staticmethod
    def cleanup_broadcast(broadcast_id: int):
        key = BroadcastParticipantService._get_key(broadcast_id)
        try:
            redis_client.delete(key)
            BroadcastParticipantService.invalidate_participants_cache(broadcast_id)
            logger.info(f"Cleaned up broadcast {broadcast_id}")
        except Exception as e:
            logger.error(f"Cleanup failed for broadcast {broadcast_id}: {e}", exc_info=True)

    @staticmethod
    def cleanup_user_from_all_broadcasts(user_id: int):
        """Fast cleanup using per-user broadcast tracking"""
        logger.info(f"✅ Cleaning up user {user_id} from broadcasts")
        user_broadcasts_key = BroadcastParticipantService._get_user_broadcasts_key(user_id)
        user_id_str = str(user_id)

        try:
            broadcast_ids = redis_client.smembers(user_broadcasts_key)
            if not broadcast_ids:
                logger.debug(f"User {user_id} has no active broadcasts to clean")
                return

            pipeline = redis_client.pipeline()
            cleaned_broadcasts = 0
            affected_broadcast_ids = []

            for bid in broadcast_ids:
                bid = int(bid)
                broadcast_key = BroadcastParticipantService._get_key(bid)
                pipeline.srem(broadcast_key, user_id_str)
                cleaned_broadcasts += 1
                affected_broadcast_ids.append(bid)

            # Remove user from their tracking set
            pipeline.delete(user_broadcasts_key)
            pipeline.execute()

            logger.info(f"✅ Cleaned up user {user_id} from {cleaned_broadcasts} broadcasts")

            # Invalidate cache for affected broadcasts
            for bid in affected_broadcast_ids:
                BroadcastParticipantService.invalidate_participants_cache(bid)

            # Trigger real-time updates for affected broadcasts
            BroadcastParticipantService._signal_broadcasts(affected_broadcast_ids)

        except Exception as e:
            logger.error(f"Error cleaning up user {user_id} from all broadcasts: {e}", exc_info=True)

    @staticmethod
    def _signal_broadcasts(broadcast_ids: list):
        """Trigger post_save signal for multiple broadcasts to update connected clients"""
        from apps.broadcast.models import Broadcast  # Avoid circular import

        if not broadcast_ids:
            return

        try:
            # Fetch broadcasts with minimal data
            broadcasts = Broadcast.objects.filter(id__in=broadcast_ids).select_related('host')

            for broadcast in broadcasts:
                try:
                    BroadcastParticipantService.signal_broadcast(broadcast)
                except Exception as e:
                    logger.warning(f"Failed to signal broadcast {broadcast.id}: {e}")

            logger.debug(f"Triggered real-time update for {len(broadcasts)} broadcasts after disconnect")
        except Exception as e:
            logger.error(f"Error signaling broadcasts after cleanup: {e}", exc_info=True)

    @staticmethod
    def signal_broadcast(broadcast: Broadcast):
        """Public method to trigger post_save signal"""
        from django.db.models.signals import post_save
        post_save.send(sender=Broadcast, instance=broadcast, created=False)

    @staticmethod
    def cleanup_all_inactive() -> int:
        """Background cleanup - removes empty participant sets"""
        cleaned = 0
        try:
            pattern = f"{BroadcastParticipantService.PREFIX}*"
            for key in redis_client.scan_iter(match=pattern, count=100):
                try:
                    if redis_client.scard(key) == 0:
                        redis_client.delete(key)
                        cleaned += 1
                except Exception:
                    continue
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} empty broadcast participant sets")
            return cleaned
        except Exception as e:
            logger.error(f"Background cleanup error: {e}", exc_info=True)
            return cleaned

    # ====================== LOCKING ======================
    LOCK_PREFIX = "lock:broadcast_cleanup:"

    @staticmethod
    def acquire_cleanup_lock(timeout_seconds: int = 300) -> tuple[bool, str | None]:
        """
        Acquire a distributed lock for cleanup task.
        Returns (acquired: bool, lock_value: str | None)
        """
        lock_key = f"{BroadcastParticipantService.LOCK_PREFIX}global"
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
        lock_key = f"{BroadcastParticipantService.LOCK_PREFIX}global"
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
