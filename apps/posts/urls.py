from django.urls import path

from apps.posts import views

urlpatterns = [
    path('create/', views.PostCreateView.as_view()),
    path('generate-upload-urls/', views.generate_upload_urls),
    path('asset-upload-complete/', views.AssetUploadCompleteView.as_view()),
]
