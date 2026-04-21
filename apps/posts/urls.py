from django.urls import path

from apps.posts import views

urlpatterns = [
    path('create/', views.PostCreateView.as_view()),
    path('update/<int:pk>/', views.PostUpdateView.as_view()),
]
