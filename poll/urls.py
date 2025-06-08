from django.urls import path

from poll import views

urlpatterns = [
    path('polls/', views.PollListView.as_view()),
]
