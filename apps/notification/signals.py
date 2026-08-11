from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_save, post_delete, m2m_changed, post_init
from django.dispatch import receiver

from apps.ballot.models import Ballot
from apps.broadcast.models import Broadcast
from apps.chat.models import Message
from apps.notification import tasks
from apps.notification.models import Preferences, Notification
from apps.petition.models import Petition
from apps.posts.models import Post, PostLike
from apps.survey.models import Survey

User = get_user_model()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def delay_on_commit(task, *args, **kwargs):
    """
    Delay Celery task only after DB commit.
    """
    transaction.on_commit(lambda: task.delay(*args, **kwargs))


def _enqueue_once(task, key: str, timeout: int, *args, **kwargs):
    """
    Prevent duplicate enqueues from multiple signals fired for the same action.
    """
    try:
        should_enqueue = cache.add(key, True, timeout)
    except Exception:
        should_enqueue = True

    if should_enqueue:
        delay_on_commit(task, *args, **kwargs)


# ---------------------------------------------------------------------
# Petition status tracking
# ---------------------------------------------------------------------

@receiver(post_init, sender=Petition)
def remember_status(sender, instance, **kwargs):
    """
    Store the initial status (open/closed) on the petition.
    """
    instance._previous_status = instance.is_open


# ---------------------------------------------------------------------
# Preferences creation
# ---------------------------------------------------------------------

@receiver(post_save, sender=User)
def create_user_preferences(sender, instance, created, **kwargs):
    if created:
        Preferences.objects.get_or_create(user=instance)


# ---------------------------------------------------------------------
# Created object notifications
# ---------------------------------------------------------------------

@receiver(post_save, sender=Ballot)
def ballot_saved(sender, instance, created, **kwargs):
    if created:
        delay_on_commit(tasks.create_ballot_notifications_on_create, instance.id)


@receiver(post_save, sender=Survey)
def survey_saved(sender, instance, created, **kwargs):
    if created:
        delay_on_commit(tasks.create_survey_notifications_on_create, instance.id)


@receiver(post_save, sender=Broadcast)
def broadcast_saved(sender, instance, created, **kwargs):
    if created:
        # This task now handles both normal broadcasts and livestreams.
        delay_on_commit(tasks.create_broadcast_notifications_on_create, instance.id)


@receiver(post_save, sender=Message)
def message_saved(sender, instance, created, **kwargs):
    if created:
        delay_on_commit(tasks.create_message_notifications_on_create, instance.id)


@receiver(post_save, sender=Post)
def post_saved(sender, instance, created, **kwargs):
    if created:
        delay_on_commit(tasks.create_post_notifications_on_create, instance.id)


@receiver(post_save, sender=Petition)
def petition_saved(sender, instance, created, update_fields=None, **kwargs):
    if created:
        instance._previous_status = instance.is_open
        delay_on_commit(tasks.create_petition_notifications_on_create, instance.id)
        return

    if update_fields is not None and "is_open" not in update_fields:
        return

    previous = getattr(instance, "_previous_status", None)
    if instance.is_open != previous:
        instance._previous_status = instance.is_open
        delay_on_commit(tasks.notify_on_petition_status_change, instance.id, instance.is_open)


# ---------------------------------------------------------------------
# Like signals
# ---------------------------------------------------------------------

@receiver(post_save, sender=PostLike)
def like_saved(sender, instance, created, **kwargs):
    if not created:
        return

    user_id = getattr(instance, "user_id", None)
    post_id = getattr(instance, "post_id", None)

    if not user_id or not post_id:
        return

    key = f"notification.like.{post_id}.{user_id}"
    _enqueue_once(tasks.notify_on_like, key, 5, user_id, post_id)


@receiver(post_delete, sender=PostLike)
def like_deleted(sender, instance, **kwargs):
    user_id = getattr(instance, "user_id", None)
    post_id = getattr(instance, "post_id", None)

    if not user_id or not post_id:
        return

    key = f"notification.unlike.{post_id}.{user_id}"
    _enqueue_once(tasks.delete_notification_on_unlike, key, 5, user_id, post_id)


# ---------------------------------------------------------------------
# M2M interactions
# ---------------------------------------------------------------------

@receiver(m2m_changed, sender=Post.likes.through)
@receiver(m2m_changed, sender=User.following.through)
@receiver(m2m_changed, sender=Petition.supporters.through)
def on_interaction(sender, instance, action, pk_set, **kwargs):
    if not pk_set:
        return

    if action == "post_add":
        for pk in pk_set:
            if sender == Post.likes.through:
                key = f"notification.like.{instance.id}.{pk}"
                _enqueue_once(tasks.notify_on_like, key, 5, pk, instance.id)

            elif sender == User.following.through:
                delay_on_commit(tasks.notify_on_follow, instance.id, pk)

            elif sender == Petition.supporters.through:
                delay_on_commit(tasks.notify_on_support, pk, instance.id)

    elif action == "post_remove":
        for pk in pk_set:
            if sender == Post.likes.through:
                key = f"notification.unlike.{instance.id}.{pk}"
                _enqueue_once(tasks.delete_notification_on_unlike, key, 5, pk, instance.id)

            elif sender == User.following.through:
                delay_on_commit(tasks.delete_notification_on_unfollow, instance.id, pk)

            elif sender == Petition.supporters.through:
                delay_on_commit(tasks.delete_notification_on_support_removal, pk, instance.id)


# ---------------------------------------------------------------------
# Deletion cleanup / events
# ---------------------------------------------------------------------

@receiver(post_delete, sender=Notification)
def notification_deleted(sender, instance, **kwargs):
    """
    Emit WS delete event for single Notification deletes.
    Bulk deletes do not trigger this signal and must emit events manually.
    """
    notification_id = instance.id
    recipient_id = instance.recipient_id

    if notification_id and recipient_id:
        delay_on_commit(
            tasks.send_notification_delete,
            notification_id=notification_id,
            recipient_id=recipient_id,
        )


@receiver(post_delete, sender=Message)
def message_deleted(sender, instance, **kwargs):
    delay_on_commit(tasks.delete_notification_on_message_deletion, instance.id)