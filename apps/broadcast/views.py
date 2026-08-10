import base64
import logging
import time
import uuid

import requests
from agora_token_builder import RtcTokenBuilder
from django.conf import settings
from django.utils import timezone
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.broadcast.models import Broadcast, RecordingSession
from apps.broadcast.services import redis_client
from apps.notification.tasks import create_live_stream_notifications

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = (5, 30)


# ====================== HELPERS ======================

def get_agora_headers():
    customer_id = settings.AGORA_CUSTOMER_ID
    customer_secret = settings.AGORA_CUSTOMER_SECRET
    credentials = f"{customer_id}:{customer_secret}"
    auth = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }


def _get_broadcast(broadcast_id):
    try:
        return (
            Broadcast.objects.select_related(
                "county",
                "constituency",
                "ward",
            )
            .prefetch_related(
                "co_hosts",
                "speakers",
            )
            .get(pk=broadcast_id)
        )
    except Exception:
        return None


def _user_in_users(users, user_id: int) -> bool:
    return any(user.id == user_id for user in users)


def can_manage_broadcast(broadcast: Broadcast, user) -> bool:
    if broadcast.host_id == user.id:
        return True

    return _user_in_users(broadcast.co_hosts.all(), user.id)


def user_can_view_region(broadcast: Broadcast, user) -> bool:
    if not broadcast.county_id:
        return True

    if user.county_id != broadcast.county_id:
        return False

    if broadcast.constituency_id and user.constituency_id != broadcast.constituency_id:
        return False

    if broadcast.ward_id and user.ward_id != broadcast.ward_id:
        return False

    return True


def _release_recording_resource(channel: str, resource_id: str):
    try:
        requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{resource_id}/mode/mix/release",
            headers=get_agora_headers(),
            json={
                "cname": channel,
                "uid": "0",
                "clientRequest": {},
            },
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        logger.warning(f"Failed to release recording resource {resource_id}: {e}")


def _stop_agora_recording(channel: str, resource_id: str, sid: str):
    try:
        requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{resource_id}/sid/{sid}/mode/mix/stop",
            headers=get_agora_headers(),
            json={
                "cname": channel,
                "uid": "0",
                "clientRequest": {},
            },
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        logger.warning(f"Failed to stop recording while cleaning up: {e}")


# ====================== AGORA TOKEN ======================

@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def generate_agora_token(request):
    broadcast_id = request.data.get("broadcast_id")

    if not broadcast_id:
        return Response({"broadcast_id": "This field is required"}, status=400)

    broadcast = _get_broadcast(broadcast_id)

    if not broadcast:
        return Response({"error": "Broadcast not found"}, status=404)

    user = request.user

    if not user.is_active:
        return Response({"error": "User is inactive"}, status=403)

    if not broadcast.is_active:
        return Response({"error": "Broadcast is inactive"}, status=403)

    is_host = broadcast.host_id == user.id
    is_co_host = _user_in_users(broadcast.co_hosts.all(), user.id)
    is_speaker = _user_in_users(broadcast.speakers.all(), user.id)

    if not (is_host or is_co_host or is_speaker):
        if not user_can_view_region(broadcast, user):
            return Response({"error": "Not authorized"}, status=403)

    if broadcast.type != Broadcast.Type.LIVESTREAM:
        join_time = timezone.now()

        if join_time < broadcast.start_time:
            return Response({"error": "Broadcast has not started"}, status=403)

        if broadcast.end_time and broadcast.end_time < join_time:
            return Response({"error": "Broadcast has ended"}, status=403)

    role = 1 if (is_host or is_co_host or is_speaker) else 2

    expiration_time_in_seconds = int(settings.BROADCAST_PERIOD)
    current_timestamp = int(time.time())
    privilege_expired_ts = current_timestamp + expiration_time_in_seconds

    token = RtcTokenBuilder.buildTokenWithUid(
        appId=settings.AGORA_APP_ID,
        appCertificate=settings.AGORA_APP_CERTIFICATE,
        channelName=str(broadcast_id),
        uid=user.id,
        role=role,
        privilegeExpiredTs=privilege_expired_ts,
    )

    if broadcast.type == Broadcast.Type.LIVESTREAM and is_host:
        notification_key = f"livestream:notified:{broadcast.id}"

        try:
            should_notify = redis_client.set(notification_key, "1", nx=True, ex=3600)

            if should_notify:
                create_live_stream_notifications.delay(broadcast.id)
        except Exception as e:
            logger.error(f"Failed livestream notification dedupe: {e}")

    return Response({
        "app_id": settings.AGORA_APP_ID,
        "token": token,
        "broadcast_id": broadcast.id,
        "user_id": user.id,
        "expires_in": expiration_time_in_seconds,
        "role": role,
    })


# ====================== START RECORDING ======================

@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def start_recording(request):
    broadcast_id = request.data.get("broadcast_id")

    if not broadcast_id:
        return Response({"broadcast_id": "This field is required"}, status=400)

    broadcast = _get_broadcast(broadcast_id)

    if not broadcast:
        return Response({"error": "Broadcast not found"}, status=404)

    if not can_manage_broadcast(broadcast, request.user):
        return Response({"error": "Not authorized"}, status=403)

    if not broadcast.is_active:
        return Response({"error": "Broadcast is inactive"}, status=403)

    active_session = (
        RecordingSession.objects.filter(
            broadcast=broadcast,
            stopped_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )

    if active_session and active_session.status == RecordingSession.Status.IN_PROGRESS:
        return Response({
            "success": True,
            "resourceId": active_session.resource_id,
            "sid": active_session.sid,
        }, status=200)

    if active_session and active_session.status == RecordingSession.Status.ERROR:
        active_session.delete()

    result = _record(broadcast, user_id=request.user.id)

    if not result.get("success"):
        status_code = 502 if result.get("agora_error") else 400
        return Response(result, status=status_code)

    return Response(result, status=200)


def _record(broadcast: Broadcast, user_id: int):
    channel = str(broadcast.pk)
    recorder_uid = "0"

    recorder_token = RtcTokenBuilder.buildTokenWithUid(
        appId=settings.AGORA_APP_ID,
        appCertificate=settings.AGORA_APP_CERTIFICATE,
        channelName=channel,
        uid=int(recorder_uid),
        role=1,
        privilegeExpiredTs=int(time.time()) + 3600 * 24,
    )

    resource_id = None
    sid = None

    try:
        is_livestream = broadcast.type == Broadcast.Type.LIVESTREAM

        if is_livestream:
            channel_type = 1
            stream_types = 2
            file_types = ["hls", "mp4"]
        else:
            channel_type = 0
            stream_types = 0
            file_types = ["hls"]

        unique_prefix = str(uuid.uuid4())[:12]

        acquire_payload = {
            "cname": channel,
            "uid": recorder_uid,
            "clientRequest": {},
        }

        logger.info(f"Acquiring recording resource for broadcast {broadcast.id}")

        acquire_resp = requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/acquire",
            headers=get_agora_headers(),
            json=acquire_payload,
            timeout=REQUEST_TIMEOUT,
        )
        acquire_resp.raise_for_status()

        resource_id = acquire_resp.json()["resourceId"]

        start_payload = {
            "cname": channel,
            "uid": recorder_uid,
            "clientRequest": {
                "token": recorder_token,
                "storageConfig": {
                    "vendor": 11,
                    "region": 0,
                    "bucket": settings.AWS_STORAGE_BUCKET_NAME,
                    "accessKey": settings.AWS_ACCESS_KEY_ID,
                    "secretKey": settings.AWS_SECRET_ACCESS_KEY,
                    "fileNamePrefix": [
                        "recordings",
                        str(user_id),
                        str(broadcast.pk),
                        unique_prefix,
                    ],
                    "extensionParams": {
                        "endpoint": settings.AWS_S3_ENDPOINT_URL.rstrip("/"),
                    },
                },
                "recordingConfig": {
                    "channelType": channel_type,
                    "streamTypes": stream_types,
                    "maxIdleTime": 900,
                    "subscribeUidGroup": 0,
                    "subscribeAudioUids": ["#allstream#"],
                },
                "recordingFileConfig": {
                    "avFileType": file_types,
                },
            },
        }

        # Never log the full payload because it contains storage secrets.
        logger.info(f"Starting recording for broadcast {broadcast.id}")

        start_resp = requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{resource_id}/mode/mix/start",
            headers=get_agora_headers(),
            json=start_payload,
            timeout=REQUEST_TIMEOUT,
        )
        start_resp.raise_for_status()

        result = start_resp.json()
        sid = result["sid"]

        RecordingSession.objects.create(
            broadcast=broadcast,
            resource_id=resource_id,
            sid=sid,
            status=RecordingSession.Status.IN_PROGRESS,
        )

        logger.info(f"Recording started successfully - SID: {sid}")

        return {
            "success": True,
            "resourceId": resource_id,
            "sid": sid,
        }

    except requests.RequestException as e:
        logger.error(f"Agora recording API error: {e}", exc_info=True)

        if resource_id and not sid:
            _release_recording_resource(channel, resource_id)

        return {
            "success": False,
            "agora_error": True,
            "error": "Agora API error",
        }

    except Exception as e:
        logger.error(f"Recording start exception: {e}", exc_info=True)

        if resource_id and sid:
            _stop_agora_recording(channel, resource_id, sid)
        elif resource_id:
            _release_recording_resource(channel, resource_id)

        return {
            "success": False,
            "error": "Recording start failed",
        }


# ====================== STOP RECORDING ======================

@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def stop_recording(request):
    broadcast_id = request.data.get("broadcast_id")

    if not broadcast_id:
        return Response({"broadcast_id": "This field is required"}, status=400)

    try:
        session = (
            RecordingSession.objects.select_related("broadcast")
            .get(
                broadcast_id=broadcast_id,
                stopped_at__isnull=True,
            )
        )
    except RecordingSession.DoesNotExist:
        return Response({"error": "No active recording"}, status=404)

    broadcast = session.broadcast

    if not can_manage_broadcast(broadcast, request.user):
        return Response({"error": "Not authorized"}, status=403)

    stop_payload = {
        "cname": str(broadcast_id),
        "uid": "0",
        "clientRequest": {},
    }

    try:
        stop_resp = requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{session.resource_id}/sid/{session.sid}/mode/mix/stop",
            headers=get_agora_headers(),
            json=stop_payload,
            timeout=REQUEST_TIMEOUT,
        )

        logger.info(f"stop_resp status: {stop_resp.status_code}")

        stop_resp.raise_for_status()
        result = stop_resp.json()

        session.stopped_at = timezone.now()
        session.status = RecordingSession.Status.STOPPED
        session.file_list = result.get("serverResponse", {}).get("fileList")
        session.save()

        broadcast.end_time = timezone.now()
        broadcast.save()

        return Response(result)

    except requests.RequestException as e:
        logger.error(f"Agora stop recording API error: {e}", exc_info=True)

        session.status = RecordingSession.Status.ERROR
        session.save(update_fields=["status"])

        return Response({"error": "Agora API error"}, status=502)

    except Exception as e:
        logger.critical(f"Stop recording exception: {e}", exc_info=True)
        return Response({"error": "Stop recording failed"}, status=500)
