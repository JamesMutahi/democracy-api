from django.urls import path
from apps.meeting.views import generate_agora_token

urlpatterns = [
    path('token/', generate_agora_token, name='agora_token'),
]