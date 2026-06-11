import time

from agora_token_builder import RtcTokenBuilder
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.broadcast.models import Broadcast
from apps.notification.tasks import create_live_stream_notifications
from django.conf import settings


@csrf_exempt
@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def generate_agora_token(request):
    try:
        data = request.data.copy()
        broadcast_id = data.get('broadcast_id')
        user_id = request.user.id

        if not broadcast_id:
            return Response({'error': 'broadcast_id required'}, status=400)

        try:
            broadcast = Broadcast.objects.get(pk=broadcast_id)
            if not broadcast.type == Broadcast.Type.LIVESTREAM:

                join_time = timezone.now()

                # Start time check
                if join_time < broadcast.start_time:
                    return Response({'error': 'Broadcast has not started'}, status=403)

                # End time check
                if broadcast.end_time < join_time:
                    return Response({'error': 'Broadcast has ended'}, status=403)
            # Role: 1 = Broadcaster (can publish audio/video), 2 = Audience
            is_speaker = broadcast.speakers.filter(id=user_id).exists()
            role = 1 if (broadcast.host_id == user_id or is_speaker) else 2
        except Broadcast.DoesNotExist:
            return Response({'error': 'Broadcast not found'}, status=404)

        # Token valid for 1 hour (adjust as needed)
        expiration_time_in_seconds = int(settings.BROADCAST_PERIOD)
        current_timestamp = int(time.time())
        privilege_expired_ts = current_timestamp + expiration_time_in_seconds

        token = RtcTokenBuilder.buildTokenWithUid(
            appId=settings.AGORA_ID,
            appCertificate=settings.AGORA_SECRET,
            channelName=broadcast_id,
            uid=user_id,
            role=role,
            privilegeExpiredTs=privilege_expired_ts
        )

        if broadcast.type == Broadcast.Type.LIVESTREAM and broadcast.host_id == user_id:
            create_live_stream_notifications.delay(broadcast_id)

        return Response({
            'app_id': settings.AGORA_ID,
            'token': token,
            'broadcast_id': broadcast_id,
            'user_id': user_id,
            'expires_in': expiration_time_in_seconds
        })

    except Exception as e:
        return Response({'error': str(e)}, status=500)
