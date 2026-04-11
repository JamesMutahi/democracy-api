from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from apps.ballot.models import Ballot
from apps.chat.models import Message
from apps.meeting.models import Meeting
from apps.notification import tasks
from apps.notification.models import Preferences, Notification
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey

User = get_user_model()


@receiver(post_save, sender=Ballot)
@receiver(post_save, sender=Survey)
@receiver(post_save, sender=Petition)
@receiver(post_save, sender=Meeting)
@receiver(post_save, sender=Message)
@receiver(post_save, sender=Post)
@receiver(post_save, sender=User)
def create_notification(sender, instance, created, **kwargs):
    if created:
        if sender == User:
            Preferences.objects.create(user=instance)
        if sender == Ballot:
            tasks.create_ballot_notifications_on_create.delay_on_commit(instance.id)
        if sender == Survey:
            tasks.create_survey_notifications_on_create.delay_on_commit(instance.id)
        if sender == Petition:
            tasks.create_petition_notifications_on_create.delay_on_commit(instance.id)
        if sender == Meeting:
            tasks.create_meeting_notifications_on_create.delay_on_commit(instance.id)
        if sender == Message:
            tasks.create_message_notifications_on_create.delay_on_commit(instance.id)
        if sender == Post:
            tasks.create_post_notifications_on_create.delay_on_commit(instance.id)


@receiver(post_delete, sender=Notification)
def notify_on_notification_deletion(sender, instance, **kwargs):
    tasks.send_notification_delete.delay(notification_id=instance.id, recipient_id=instance.recipient.id)
