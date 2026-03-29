from django.urls import path

from apps.chat import views

urlpatterns = [
    path('create-message/', views.MessageCreateView.as_view()),
]
