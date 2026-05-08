from django.urls import path

from apps.chat import views

urlpatterns = [
    path('create-message/', views.MessageCreateView.as_view()),
    path('generate-upload-urls/', views.generate_upload_urls),
    path('direct-message/', views.direct_message),
    path('asset-upload-complete/', views.AssetUploadCompleteView.as_view()),
    path('message/<int:pk>/', views.MessageDetailView.as_view()),
]
