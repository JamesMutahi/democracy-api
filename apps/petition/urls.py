from django.urls import path

from apps.petition import views

urlpatterns = [
    path('create/', views.PetitionCreateView.as_view()),
]