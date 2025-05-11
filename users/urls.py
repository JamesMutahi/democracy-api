from django.urls import path
from users import views

urlpatterns = [
    path('register/', views.CreateUserView.as_view()),
    path('login/', views.LoginView.as_view()),
    path('logout/', views.logout),
    path('register/verify/', views.RegistrationVerificationView.as_view()),
    path('register/resend-otp/', views.resend_code),
    path('password-reset/verify-email/', views.PasswordResetEmailVerification.as_view()),
    path('password-reset/verify-code/', views.PasswordResetCodeVerification.as_view()),
    path('password-reset/', views.PasswordResetView.as_view()),
    path('password-change/', views.PasswordChangeView.as_view()),
    path('user/', views.UserView.as_view({'get': 'retrieve', 'patch': 'update', 'put': 'update'})),
]