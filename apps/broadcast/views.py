import base64
import logging
import time
import uuid

import requests
from agora_token_builder import RtcTokenBuilder
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from apps.broadcast.models import Broadcast, RecordingSession
from apps.notification.tasks import create_live_stream_notifications

logger = logging.getLogger(__name__)


@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def generate_agora_token(request):
    try:
        data = request.data.copy()
        broadcast_id = data.get('broadcast_id', None)
        user_id = request.user.id

        if not broadcast_id:
            return Response({'broadcast_id': 'This field is required'}, status=400)

        broadcast = get_object_or_404(Broadcast.objects.all(), pk=broadcast_id)
        is_host = broadcast.host_id == user_id
        is_co_host = broadcast.co_hosts.filter(id=user_id).exists()
        is_speaker = broadcast.speakers.filter(id=user_id).exists()

        broadcast = Broadcast.objects.get(pk=broadcast_id)
        if not broadcast.type == Broadcast.Type.LIVESTREAM:

            join_time = timezone.now()

            # Start time check
            if join_time < broadcast.start_time:
                return Response('Broadcast has not started', status=403)

            # End time check
            if broadcast.end_time < join_time:
                return Response('Broadcast has ended', status=403)

        # Role: 1 = Broadcaster (can publish audio/video), 2 = Audience
        role = 1 if (is_host or is_co_host or is_speaker) else 2

        # Token valid for 1 hour (adjust as needed)
        expiration_time_in_seconds = int(settings.BROADCAST_PERIOD)
        current_timestamp = int(time.time())
        privilege_expired_ts = current_timestamp + expiration_time_in_seconds

        token = RtcTokenBuilder.buildTokenWithUid(
            appId=settings.AGORA_APP_ID,
            appCertificate=settings.AGORA_APP_CERTIFICATE,
            channelName=broadcast_id,
            uid=user_id,
            role=role,
            privilegeExpiredTs=privilege_expired_ts
        )

        response_data = {
            'app_id': settings.AGORA_APP_ID,
            'token': token,
            'broadcast_id': broadcast_id,
            'user_id': request.user.id,
            'expires_in': expiration_time_in_seconds,
            'role': role
        }

        # Auto-start recording for host if live stream
        if is_host or is_co_host or is_speaker:

            # Check if session exists
            if RecordingSession.objects.filter(broadcast_id=broadcast_id).exists():
                # TODO: Check status, maybe delete and start a new session if not recording
                pass
            else:
                recording_result = _record(broadcast, user_id)
                if recording_result.get('success'):
                    response_data['recording'] = {
                        'resourceId': recording_result['resourceId'],
                        'sid': recording_result['sid'],
                        'status': 'started'
                    }
                else:
                    response_data['recording_error'] = recording_result.get('error')

        if broadcast.type == Broadcast.Type.LIVESTREAM and broadcast.host_id == user_id:
            create_live_stream_notifications.delay(broadcast_id)

        return Response(response_data)

    except Exception as e:
        return Response({'error': str(e)}, status=500)


def get_agora_headers():
    customer_id = settings.AGORA_CUSTOMER_ID
    customer_secret = settings.AGORA_CUSTOMER_SECRET

    if not customer_id or not customer_secret:
        raise ValueError("AGORA_CUSTOMER_ID or AGORA_CUSTOMER_SECRET is missing in settings")

    credentials = f"{customer_id}:{customer_secret}"
    auth = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

    return {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/json'
    }


@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def start_recording(request):
    try:
        data = request.data
        broadcast_id = data.get('broadcast_id')
        user_id = request.user.id  # Usually the host

        if not broadcast_id:
            return Response({'broadcast_id': 'This field is required'}, status=400)

        broadcast = Broadcast.objects.get(pk=broadcast_id)

        is_host = broadcast.host_id == user_id
        is_co_host = broadcast.co_hosts.filter(id=user_id).exists()

        if not is_host and not is_co_host:
            return Response('Only host can start recording', status=403)

        return _record(broadcast, user_id=user_id)

    except Broadcast.DoesNotExist:
        return Response('Broadcast not found', status=404)
    except requests.exceptions.RequestException as e:
        return Response({'error': f'Agora API error: {str(e)}'}, status=502)
    except Exception as e:
        return Response({'error': str(e)}, status=500)


def _record(broadcast, user_id):
    """Internal function to start recording - reusable"""
    channel = str(broadcast.pk)
    recorder_uid = "0"  # Dedicated recorder UID (not used by real users)

    # Generate a separate token for the recorder (Broadcaster role)
    recorder_token = RtcTokenBuilder.buildTokenWithUid(
        appId=settings.AGORA_APP_ID,
        appCertificate=settings.AGORA_APP_CERTIFICATE,
        channelName=channel,
        uid=int(recorder_uid),
        role=1,  # Broadcaster
        privilegeExpiredTs=int(time.time()) + 3600 * 24  # Longer lived
    )

    try:
        is_livestream = broadcast.type == Broadcast.Type.LIVESTREAM

        if is_livestream:
            stream_types = 2  # Audio + Video
            file_types = ["hls", "mp4"]
        else:
            # Meeting / other → Audio only
            stream_types = 0  # Audio only
            file_types = ["hls", "mp4"]  # MP4 will contain audio only

        # 1. Acquire
        acquire_payload = {
            "cname": channel,
            "uid": recorder_uid,
            "clientRequest": {}
        }
        acquire_resp = requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/acquire",
            headers=get_agora_headers(),
            json=acquire_payload
        )
        acquire_resp.raise_for_status()
        resource_id = acquire_resp.json()['resourceId']

        folder = "recordings"
        unique_prefix = str(uuid.uuid4())[:12]
        file_name_prefix = [folder, str(user_id), unique_prefix]

        # 2. Start Composite with Cloudflare R2
        start_payload = {
            "cname": channel,
            "uid": recorder_uid,
            "clientRequest": {
                "token": recorder_token,
                "storageConfig": {
                    "vendor": 11,  # Cloudflare R2 (S3 compatible)
                    "region": 0,
                    "bucket": settings.AWS_STORAGE_BUCKET_NAME,
                    "accessKey": settings.AWS_ACCESS_KEY_ID,
                    "secretKey": settings.AWS_SECRET_ACCESS_KEY,
                    "fileNamePrefix": file_name_prefix,
                    "extensionParams": {
                        "endpoint": settings.AWS_S3_ENDPOINT_URL
                    }
                },
                "recordingConfig": {
                    "channelType": 1,  # Live Broadcast
                    "streamTypes": stream_types,  # Audio + Video
                    "maxIdleTime": 300,
                },
                "recordingFileConfig": {
                    "avFileType": file_types
                },
                # Optional: Add transcodingConfig for custom layout
            }
        }

        start_resp = requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{resource_id}/mode/mix/start",
            headers=get_agora_headers(),
            json=start_payload
        )
        start_resp.raise_for_status()
        result = start_resp.json()

        # Save session
        session = RecordingSession.objects.create(
            broadcast=broadcast,
            resource_id=resource_id,
            sid=result['sid'],
        )

        query_url = f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{session.resource_id}/sid/{session.sid}/mode/mix/query"

        query_resp = requests.get(query_url, headers=get_agora_headers())
        query_resp.raise_for_status()
        agora_data = query_resp.json()
        logger.info(f"Query result: {agora_data}")

        return {'success': True, 'resourceId': resource_id, 'sid': result['sid']}

    except Exception as e:
        logger.error(f"Exception: {e}")
        return {'success': False, 'error': str(e)}


@csrf_exempt
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def check_recording_status(request):
    broadcast_id = request.query_params.get('broadcast_id') or request.data.get('broadcast_id')

    if not broadcast_id:
        return Response({'error': 'broadcast_id is required'}, status=400)

    try:
        session = RecordingSession.objects.get(
            broadcast_id=broadcast_id,
            stopped_at__isnull=True  # Only active sessions
        )
    except RecordingSession.DoesNotExist:
        return Response({
            'is_recording': False,
            'message': 'No active recording found for this broadcast'
        })

    # Query Agora
    try:
        query_url = f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{session.resource_id}/sid/{session.sid}/mode/mix/query"

        query_resp = requests.get(query_url, headers=get_agora_headers())
        query_resp.raise_for_status()
        agora_data = query_resp.json()

        # Update local record with latest info
        session.file_list = agora_data.get('fileList', [])
        if agora_data.get('status') == 'stopped':  # or check serverResponse
            session.stopped_at = timezone.now()
            session.status = 'stopped'
        session.save()

        return Response({
            'is_recording': True,
            'status': agora_data.get('status', 'unknown'),
            'resourceId': session.resource_id,
            'sid': session.sid,
            'mode': session.mode,
            'is_audio_only': session.is_audio_only,
            'fileList': agora_data.get('fileList', []),
            'serverResponse': agora_data.get('serverResponse', {})
        })

    except requests.exceptions.RequestException as e:
        # Still return local info if Agora query fails temporarily
        return Response({
            'is_recording': True,
            'status': 'query_failed',
            'error': str(e),
            'local_status': session.status,
            'resourceId': session.resource_id,
            'sid': session.sid
        }, status=502)

    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def stop_recording(request):
    broadcast_id = request.data.get('broadcast_id')

    try:
        session = RecordingSession.objects.get(
            broadcast_id=broadcast_id,
            stopped_at__isnull=True
        )

        stop_payload = {
            "cname": str(broadcast_id),
            "uid": session.recorder_uid,
            "clientRequest": {}
        }

        stop_resp = requests.post(
            f"https://api.sd-rtn.com/v1/apps/{settings.AGORA_APP_ID}/cloud_recording/resourceid/{session.resource_id}/sid/{session.sid}/mode/mix/stop",
            headers=get_agora_headers(),
            json=stop_payload
        )
        stop_resp.raise_for_status()
        result = stop_resp.json()

        session.stopped_at = timezone.now()
        session.status = 'stopped'
        session.file_list = result.get('fileList', [])
        session.save()

        return Response(result)

    except RecordingSession.DoesNotExist:
        return Response({'error': 'No active recording'}, status=404)
    except Exception as e:
        return Response({'error': str(e)}, status=500)
