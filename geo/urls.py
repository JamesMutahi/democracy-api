from django.urls import path

from geo import views

urlpatterns = [
    path('counties/', views.CountyListView.as_view()),
    path('county/<int:pk>/constituencies/', views.ConstituencyListView.as_view()),
    path('constituency/<int:pk>/wards/', views.WardListView.as_view()),
]
