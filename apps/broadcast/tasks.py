import base64
import json
import logging
import time
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import RecordingSession
from .services import BroadcastParticipantService

logger = logging.getLogger(__name__)

# ====================== SETTINGS ======================

AGORA_API_TIMEOUT = getattr(settings, "AGORA_API_TIMEOUT", (5, 30))
RECORDING_STATUS_BATCH_SIZE = getattr(settings, "RECORDING_STATUS_BATCH_SIZE", 50)
RECORDING_STATUS_SLEEP_SECONDS = getattr(settings, "RECORDING_STATUS_SLEEP_SECONDS", 0.1)
STALE_RECORDING_SESSION_HOURS = getattr(settings, "STALE_RECORDING_SESSION_HOURS", 24)


# ====================== HELPERS ======================

def _get_agora_headers() -> dict:
    """
    Build Agora Cloud Recording API headers.
    """
    customer_id = settings.AGORA_CUSTOMER_ID
    customer_secret = settings.AGORA_CUSTOMER_SECRET

    credentials = f"{customer_id}:{customer_secret}"
    auth = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }


def _is_json_field(field) -> bool:
    """
    Detect whether a model field is a JSONField.

    This allows the task to work with both:
      - new JSONField-based RecordingSession.file_list
      - old CharField/TextField-based RecordingSession.file_list
    """
    try:
        internal_type = field.get_internal_type()
        if internal_type == "JSONField":
            return True
    except Exception:
        pass

    try:
        from django.db.models import JSONField
        return isinstance(field, JSONField)
    except Exception:
        return False


def _assign_file_list(session: RecordingSession, file_list):
    """
    Assign Agora fileList to session.file_list safely.

    If file_list is JSONField, store native JSON.
    If old CharField/TextField, store serialized JSON and truncate if needed.
    """
    field = session._meta.get_field("file_list")

    if _is_json_field(field):
        session.file_list = file_list
        return

    if file_list is None:
        session.file_list = None
        return

    if isinstance(file_list, str):
        value = file_list
    else:
        try:
            value = json.dumps(file_list)
        except Exception:
            value = str(file_list)

    max_length = getattr(field, "max_length", None)

    if max_length and len(value) > max_length:
        logger.warning(
            "RecordingSession %s file_list is too long for CharField. "
            "Truncating to %s characters. Consider migrating to JSONField.",
            session.pk,
            max_length,
        )
        value = value[:max_length]

    session.file_list = value


def _get_backoff_countdown(base_delay: int, retries: int) -> int:
    """
    Simple exponential backoff:
      30, 60, 120, 240...
    """
    return base_delay * (2 ** max(retries, 0))


def _mark_stale_recording_sessions() -> int:
    """
    Mark recording sessions as errored if they have been active too long.

    This prevents sessions from remaining active forever if Agora stop/query
    events were missed.
    """
    try:
        stale_hours = float(STALE_RECORDING_SESSION_HOURS)
    except Exception:
        stale_hours = 24

    if stale_hours <= 0:
        return 0

    cutoff = timezone.now() - timedelta(hours=stale_hours)

    stale_sessions = RecordingSession.objects.filter(
        stopped_at__isnull=True,
        created_at__lt=cutoff,
    )

    count = stale_sessions.update(
        stopped_at=timezone.now(),
        status=RecordingSession.Status.ERROR,
    )

    if count:
        logger.warning("Marked %s stale recording sessions as error.", count)

    return count


# ====================== CLEANUP TASKS ======================

@shared_task(
    name="broadcast.cleanup_broadcast_participants",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def cleanup_broadcast_participants(self):
    """
    Periodic task to clean up empty broadcast participant sets.

    Uses distributed locking so only one worker runs cleanup at a time.
    """
    lock_acquired = False
    lock_value = None

    try:
        lock_acquired, lock_value = BroadcastParticipantService.acquire_cleanup_lock(
            timeout_seconds=300
        )

        if not lock_acquired:
            logger.info("Cleanup task skipped - another instance is already running.")
            return {"status": "skipped"}

        logger.info("Cleanup lock acquired - starting participant cleanup.")

        cleaned = BroadcastParticipantService.cleanup_all_inactive()

        if cleaned:
            logger.info("Cleaned %s empty broadcast participant sets.", cleaned)
        else:
            logger.debug("No empty participant sets found.")

        return {
            "status": "success",
            "cleaned": cleaned,
        }

    except Exception as exc:
        logger.exception("Participant cleanup task failed.")

        retry_count = getattr(self.request, "retries", 0)
        countdown = _get_backoff_countdown(60, retry_count)

        raise self.retry(exc=exc, countdown=countdown)

    finally:
        if lock_acquired:
            BroadcastParticipantService.release_cleanup_lock(lock_value)
            logger.debug("Cleanup lock released.")


@shared_task(
    name="broadcast.cleanup_user_from_all_broadcasts",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def cleanup_user_from_all_broadcasts(self, user_id):
    """
    Manual/background task to clean a user from all broadcasts.

    Important:
      This should only be used when the user truly has no active connections.
      If you adopted connection-aware presence in BroadcastParticipantService,
      the service should already protect active connections.
    """
    try:
        user_id = int(user_id)
    except Exception:
        logger.warning("cleanup_user_from_all_broadcasts called with invalid user_id: %r", user_id)
        return {"status": "invalid_user_id"}

    try:
        BroadcastParticipantService.cleanup_user_from_all_broadcasts(user_id)

        logger.info("Cleaned up user %s from all broadcasts via task.", user_id)

        return {
            "status": "success",
            "user_id": user_id,
        }

    except Exception as exc:
        logger.exception("Failed to cleanup user %s from all broadcasts.", user_id)

        retry_count = getattr(self.request, "retries", 0)
        countdown = _get_backoff_countdown(30, retry_count)

        raise self.retry(exc=exc, countdown=countdown)


# ====================== RECORDING STATUS TASK ======================

@shared_task(
    name="broadcast.check_recording_status",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def check_recording_status(self):
    """
    Periodically query Agora for active recording sessions.
      - processes sessions in batches,
      - uses request timeouts,
      - does not stop the whole task if one session fails,
      - safely parses Agora responses,
      - marks stale sessions as error,
      - supports JSONField or legacy CharField file_list.
    """
    app_id = getattr(settings, "AGORA_APP_ID", None)

    if not app_id:
        logger.error("AGORA_APP_ID is not configured.")
        return {"status": "configuration_error", "error": "AGORA_APP_ID missing"}

    try:
        headers = _get_agora_headers()
    except Exception:
        logger.exception("Failed to build Agora headers.")
        return {"status": "configuration_error", "error": "Agora credentials missing"}

    _mark_stale_recording_sessions()

    try:
        batch_size = int(RECORDING_STATUS_BATCH_SIZE)
    except Exception:
        batch_size = 50

    batch_size = max(1, batch_size)

    sessions = (
        RecordingSession.objects.filter(stopped_at__isnull=True)
        .select_related("broadcast")
        .order_by("created_at")[:batch_size]
    )

    summary = {
        "checked": 0,
        "stopped": 0,
        "errors": 0,
        "failed_session_ids": [],
    }

    try:
        sleep_seconds = float(RECORDING_STATUS_SLEEP_SECONDS)
    except Exception:
        sleep_seconds = 0.1

    with requests.Session() as http:
        for session in sessions:
            try:
                if not session.resource_id or not session.sid:
                    logger.warning(
                        "RecordingSession %s is missing resource_id/sid. Marking as error.",
                        session.pk,
                    )

                    session.status = RecordingSession.Status.ERROR
                    session.stopped_at = timezone.now()
                    session.save(update_fields=["status", "stopped_at", "updated_at"])

                    summary["errors"] += 1
                    continue

                query_url = (
                    f"https://api.sd-rtn.com/v1/apps/{app_id}/cloud_recording/"
                    f"resourceid/{session.resource_id}/sid/{session.sid}/mode/mix/query"
                )

                response = http.get(
                    query_url,
                    headers=headers,
                    timeout=AGORA_API_TIMEOUT,
                )

                # If Agora no longer knows about this recording, mark it as error.
                if response.status_code == 404:
                    logger.warning(
                        "Agora returned 404 for RecordingSession %s. Marking as error.",
                        session.pk,
                    )

                    session.status = RecordingSession.Status.ERROR
                    session.stopped_at = timezone.now()
                    session.save(update_fields=["status", "stopped_at", "updated_at"])

                    summary["errors"] += 1
                    continue

                response.raise_for_status()

                try:
                    payload = response.json()
                except ValueError:
                    logger.error(
                        "Invalid JSON response from Agora for RecordingSession %s.",
                        session.pk,
                    )
                    summary["errors"] += 1
                    summary["failed_session_ids"].append(session.id)
                    continue

                if not isinstance(payload, dict):
                    payload = {}

                logger.debug("Agora query response for session %s: %s", session.pk, payload)

                server_response = payload.get("serverResponse")

                if not isinstance(server_response, dict):
                    server_response = {}

                raw_status = server_response.get("status") or payload.get("status") or ""
                status = str(raw_status).lower()

                changed = False
                status_changed = False

                file_list = server_response.get("fileList")

                if file_list is not None:
                    try:
                        current_file_list = session.file_list
                    except Exception:
                        current_file_list = None

                    if current_file_list != file_list:
                        _assign_file_list(session, file_list)
                        changed = True

                if status in {"stopped", "error", "failed", "failure"}:
                    session.stopped_at = timezone.now()

                    if status == "stopped":
                        session.status = RecordingSession.Status.STOPPED
                        summary["stopped"] += 1
                    else:
                        session.status = RecordingSession.Status.ERROR

                    changed = True
                    status_changed = True

                elif status in {"in progress", "inprogress", "active", "recording"}:
                    if session.status != RecordingSession.Status.IN_PROGRESS:
                        session.status = RecordingSession.Status.IN_PROGRESS
                        changed = True
                        status_changed = True

                if changed:
                    session.save()

                if status_changed and session.broadcast_id:
                    try:
                        BroadcastParticipantService.signal_broadcast(session.broadcast)
                    except Exception:
                        logger.warning(
                            "Failed to signal broadcast %s after recording status change.",
                            session.broadcast_id,
                        )

                summary["checked"] += 1

            except requests.RequestException as exc:
                logger.error(
                    "Agora request failed for RecordingSession %s: %s",
                    session.pk,
                    exc,
                )

                summary["errors"] += 1
                summary["failed_session_ids"].append(session.id)

            except Exception:
                logger.exception(
                    "Unexpected error while checking RecordingSession %s.",
                    session.pk,
                )

                summary["errors"] += 1
                summary["failed_session_ids"].append(session.id)

            finally:
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

    # If every checked session failed, retry the whole task.
    # If some succeeded, do not retry; the next periodic run will handle failures.
    if (
            summary["checked"] == 0 and
            summary["errors"] > 0 and
            getattr(self.request, "retries", 0) < self.max_retries
    ):
        raise self.retry(
            exc=RuntimeError("Agora recording status checks failed."),
            countdown=60,
        )

    return summary
