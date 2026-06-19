from django.urls import path

from apps.broadcast import views

urlpatterns = [
    path('token/', views.generate_agora_token),
    path('start-recording/', views.start_recording),
    path('check-recording-status/', views.check_recording_status),
    path('stop-recording/', views.stop_recording),
]
