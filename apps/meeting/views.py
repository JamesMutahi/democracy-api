import time

from agora_token_builder import RtcTokenBuilder
from decouple import config
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.meeting.models import Meeting
from apps.notification.tasks import create_live_stream_notifications

AGORA_ID = config('AGORA_ID')
AGORA_SECRET = config('AGORA_SECRET')


@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def generate_agora_token(request):
    try:
        data = request.data.copy()
        meeting_id = data.get('meeting_id')
        user_id = request.user.id

        if not meeting_id:
            return Response({'error': 'meeting_id required'}, status=400)

        try:
            meeting = Meeting.objects.get(pk=meeting_id)
            # Role: 1 = Broadcaster (can publish audio/video), 2 = Audience
            is_speaker = meeting.speakers.filter(id=user_id).exists()
            role = 1 if (meeting.host_id == user_id or is_speaker) else 2
        except Meeting.DoesNotExist:
            return Response({'error': 'Meeting not found'}, status=404)

        # Token valid for 1 hour (adjust as needed)
        expiration_time_in_seconds = 3600
        current_timestamp = int(time.time())
        privilege_expired_ts = current_timestamp + expiration_time_in_seconds

        token = RtcTokenBuilder.buildTokenWithUid(
            appId=AGORA_ID,
            appCertificate=AGORA_SECRET,
            channelName=meeting_id,
            uid=user_id,
            role=role,
            privilegeExpiredTs=privilege_expired_ts
        )

        if meeting.is_live_stream and meeting.host_id == user_id:
            create_live_stream_notifications.delay(meeting_id)

        return Response({
            'app_id': AGORA_ID,
            'token': token,
            'meeting_id': meeting_id,
            'user_id': user_id,
            'expires_in': expiration_time_in_seconds
        })

    except Exception as e:
        return Response({'error': str(e)}, status=500)
