from django.urls import path
from users import views

urlpatterns = [
    path('login/', views.LoginView.as_view()),
    path('logout/', views.logout),
    path('user/', views.UserView.as_view({'get': 'retrieve', 'patch': 'update', 'put': 'update'})),
]