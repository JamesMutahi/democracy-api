from django.contrib.auth import user_logged_in, get_user_model
from django.dispatch import receiver
from django.utils import timezone

User = get_user_model()

@receiver(user_logged_in)
def update_last_login(sender, request, user, **kwargs):
    """
    Automatically update last_login field on every successful login.
    """
    User.objects.filter(pk=user.pk).update(last_login=timezone.now())