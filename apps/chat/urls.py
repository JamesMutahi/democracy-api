from django.urls import path

from apps.chat import views

urlpatterns = [
    path('create-message/', views.MessageCreateView.as_view()),
    path('direct-message/', views.direct_message),
    path('asset-upload-complete/', views.AssetUploadCompleteView.as_view()),
]
