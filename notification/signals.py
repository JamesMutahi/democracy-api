from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from chat.models import Message
from notification.models import Notification
from poll.models import Poll
from survey.models import Survey

User = get_user_model()


@receiver(post_save, sender=Poll)
@receiver(post_save, sender=Survey)
@receiver(post_save, sender=Message)
def create_notification_on_creation(sender, instance, created, **kwargs):
    if created:
        users = User.objects.all()
        if sender == Poll:
            for user in users:
                Notification.objects.get_or_create(
                    user=user,
                    text='New poll',
                    poll=instance,
                )
        if sender == Survey:
            for user in users:
                Notification.objects.create(
                    user=user,
                    text='New survey',
                    survey=instance,
                )
        if sender == Message:
            users = instance.chat.users.exclude(id=instance.user.id)
            for user in users:
                Notification.objects.create(
                    user=user,
                    text=f'{instance.user} sent a message',
                    chat=instance.chat,
                    message=instance,
                )


@receiver(pre_delete, sender=Poll)
@receiver(pre_delete, sender=Survey)
@receiver(pre_delete, sender=Message)
def delete_notification_on_deletion(sender, instance, **kwargs):
    if sender == Poll:
        Notification.objects.filter(poll=instance).delete()
    if sender == Survey:
        Notification.objects.filter(survey=instance).delete()
    if sender == Message:
        Notification.objects.filter(message=instance).delete()
