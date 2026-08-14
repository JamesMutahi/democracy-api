"""
URL configuration for project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django_channels_jwt.views import AsgiValidateTokenView
from fcm_django.api.rest_framework import FCMDeviceAuthorizedViewSet
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView

from apps.recommendations.admin import recommendation_admin

router = DefaultRouter()
router.register("devices", FCMDeviceAuthorizedViewSet)

urlpatterns = [
    path('grappelli/', include('grappelli.urls')),
    path('user/', include('apps.users.urls')),
    path('post/', include('apps.posts.urls')),
    path('chat/', include('apps.chat.urls')),
    path('petition/', include('apps.petition.urls')),
    path('broadcast/', include('apps.broadcast.urls')),
    path('recommendation-admin/', recommendation_admin.urls),
    path('admin/', admin.site.urls),
    path("recommendation-admin/", recommendation_admin.urls),
    path('nested_admin/', include('nested_admin.urls')),
    path('auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', TokenBlacklistView.as_view(), name='token_blacklist'),
    path('ticket/', AsgiValidateTokenView.as_view()),
    path("api/", include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
