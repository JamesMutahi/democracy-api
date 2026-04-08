from django.urls import path

from apps.posts import views

urlpatterns = [
    path('create/', views.PostCreateView.as_view()),
    path('<int:pk>/update/', views.PostUpdateView.as_view()),
]
