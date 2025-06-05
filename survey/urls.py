from django.urls import path
from survey import views

urlpatterns = [
    path('surveys/', views.SurveyListView.as_view()),
    path('response/', views.ResponseListCreateView.as_view()),
]
