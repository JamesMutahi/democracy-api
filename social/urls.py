from django.urls import path

from social import views

urlpatterns = [
    path('posts/', views.PostListView.as_view()),
]
