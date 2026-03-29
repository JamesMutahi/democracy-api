from django.urls import path

from apps.posts import views

urlpatterns = [
    path('create/', views.PostCreateView.as_view()),
]
