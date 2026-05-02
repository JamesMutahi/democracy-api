import time

from agora_token_builder import RtcTokenBuilder
from decouple import config
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.meeting.models import Meeting

APP_ID = config('AGORA_ID')
APP_CERTIFICATE = config('AGORA_CERTIFICATE')


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
            role = 1 if meeting.host_id == user_id else 2
        except Meeting.DoesNotExist:
            return Response({'error': 'Meeting not found'}, status=404)

        # Token valid for 24 hours (adjust as needed)
        expiration_time_in_seconds = 86400
        current_timestamp = int(time.time())
        privilege_expired_ts = current_timestamp + expiration_time_in_seconds

        token = RtcTokenBuilder.buildTokenWithUid(
            appId=APP_ID,
            appCertificate=APP_CERTIFICATE,
            channelName=meeting_id,
            uid=user_id,
            role=role,
            privilegeExpiredTs=privilege_expired_ts
        )

        return Response({
            'app_id': APP_ID,
            'token': token,
            'meeting_id': meeting_id,
            'user_id': user_id,
            'expires_in': expiration_time_in_seconds
        })

    except Exception as e:
        return Response({'error': str(e)}, status=500)
