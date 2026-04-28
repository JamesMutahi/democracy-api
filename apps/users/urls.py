from django.urls import path
from apps.users import views

urlpatterns = [
    path('', views.UserView.as_view({'get': 'retrieve', 'patch': 'update'})),
]