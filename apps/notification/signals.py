from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, post_delete, m2m_changed, post_init
from django.dispatch import receiver

from apps.ballot.models import Ballot
from apps.chat.models import Message
from apps.meeting.models import Meeting
from apps.notification import tasks
from apps.notification.models import Preferences, Notification
from apps.petition.models import Petition
from apps.posts.models import Post, PostLike
from apps.survey.models import Survey

User = get_user_model()


@receiver(post_init, sender=Petition)
def remember_status(sender, instance, **kwargs):
    # Store the initial status (open/closed) on the petition
    instance._previous_status = instance.is_open


@receiver(post_save, sender=Ballot)
@receiver(post_save, sender=Survey)
@receiver(post_save, sender=Petition)
@receiver(post_save, sender=Meeting)
@receiver(post_save, sender=Message)
@receiver(post_save, sender=Post)
@receiver(post_save, sender=User)
@receiver(post_save, sender=PostLike)
def create_notification(sender, instance, created, **kwargs):
    if created:
        if sender == User:
            Preferences.objects.create(user=instance)
        if sender == Ballot:
            tasks.create_ballot_notifications_on_create.delay(instance.id)
        if sender == Survey:
            tasks.create_survey_notifications_on_create.delay(instance.id)
        if sender == Petition:
            tasks.create_petition_notifications_on_create.delay(instance.id)
        if sender == Meeting:
            tasks.create_meeting_notifications_on_create.delay(instance.id)
        if sender == Message:
            tasks.create_message_notifications_on_create.delay(instance.id)
        if sender == Post:
            tasks.create_post_notifications_on_create.delay(instance.id)
        if sender == PostLike:
            tasks.notify_on_like.delay(instance.user.id, instance.post.id)
    # updates
    else:
        if sender == Petition:
            if instance.is_open != getattr(instance, '_previous_status', None):
                tasks.notify_on_petition_status_change.delay(instance.id, instance.is_open)


@receiver(m2m_changed, sender=User.following.through)
def on_follow_change(sender, instance, action, pk_set, **kwargs):
    if action == 'post_add':
        for pk in pk_set:
            tasks.notify_on_follow.delay(instance.id, pk)
    if action == 'post_remove':
        for pk in pk_set:
            tasks.delete_notification_on_unfollow.delay(instance.id, pk)


@receiver(post_delete, sender=Notification)
@receiver(post_delete, sender=PostLike)
def notify_on_notification_deletion(sender, instance, **kwargs):
    if sender == PostLike:
        tasks.delete_notification_on_unlike.delay(instance.user.id, instance.post.id)
    if sender == Notification:
        tasks.send_notification_delete.delay(notification_id=instance.id, recipient_id=instance.recipient.id)
