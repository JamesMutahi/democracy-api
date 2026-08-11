import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from fcm_django.models import FCMDevice
from firebase_admin.messaging import Message as fireMessage, Notification as fireNotification

from apps.ballot.models import Ballot
from apps.broadcast.models import Broadcast
from apps.chat.models import Message
from apps.notification.models import Notification, Preferences
from apps.notification.serializers import NotificationSerializer
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey
from apps.utils.firebase import get_firebase_app

logger = logging.getLogger(__name__)
User = get_user_model()


# ---------------------------------------------------------------------
# Base queryset helpers
# ---------------------------------------------------------------------

def _active_users():
    """
    Base queryset for active users.
    """
    return User.objects.filter(is_active=True).select_related("preferences").distinct()


def _notification_enabled_users():
    """
    Active users who have global notifications enabled.
    """
    return _active_users().filter(preferences__allow_notifications=True)


def _normalize_fcm_data(data: dict | None) -> dict:
    """
    Firebase Cloud Messaging data values must be strings.
    """
    if not data:
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _truncate(value, length: int = 100) -> str:
    """
    Truncate long push notification bodies.
    """
    value = str(value or "").strip()
    if len(value) <= length:
        return value
    return value[: max(length - 1, 0)] + "…"


def _apply_location_filters(users, obj):
    """
    Apply county / constituency / ward filters if present on the object.
    Uses *_id fields where available to avoid unnecessary joins.
    """
    for field in ("county", "constituency", "ward"):
        field_id = getattr(obj, f"{field}_id", None)
        if field_id:
            users = users.filter(**{f"{field}_id": field_id})
            continue

        value = getattr(obj, field, None)
        if value:
            users = users.filter(**{field: value})

    return users


def _allows_notification(user, preference_name: str | None = None) -> bool:
    """
    Checks:
    - user exists
    - global notifications are enabled
    - optional specific preference is enabled

    If Preferences row is missing, create it defensively.
    """
    if not user or not user.pk:
        return False

    preferences = getattr(user, "preferences", None)
    if preferences is None:
        preferences, _ = Preferences.objects.get_or_create(user_id=user.pk)

    if not preferences.allow_notifications:
        return False

    if preference_name:
        return bool(getattr(preferences, preference_name, True))

    return True


# ---------------------------------------------------------------------
# Push notification helpers
# ---------------------------------------------------------------------

def send_push_to_user(user, title, body, data=None):
    """
    Send push notification to all active devices for one user.
    """
    if not user or not user.pk:
        return

    devices = FCMDevice.objects.filter(user_id=user.pk, active=True)
    if not devices.exists():
        return

    try:
        get_firebase_app()
        devices.send_message(
            fireMessage(
                notification=fireNotification(title=title, body=body),
                data=_normalize_fcm_data(data),
            )
        )
    except Exception:
        logger.exception("Failed to send push notification to user_id=%s", user.pk)


def send_push_to_user_ids(user_ids, title: str, body: str, data: dict | None = None) -> None:
    """
    Send push notification to all active devices for a list of user IDs.
    """
    ids = list({user_id for user_id in user_ids if user_id})
    if not ids:
        return

    devices = FCMDevice.objects.filter(user_id__in=ids, active=True)
    if not devices.exists():
        return

    try:
        get_firebase_app()
        devices.send_message(
            fireMessage(
                notification=fireNotification(
                    title=title,
                    body=body,
                ),
                data=_normalize_fcm_data(data),
            )
        )
    except Exception:
        logger.exception("Failed to send bulk push notification to %s users", len(ids))


# ---------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------

def _group_send(group_name: str, message: dict) -> None:
    """
    Safe wrapper for Channels group_send.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning("Channel layer is not configured; skipping WS message action=%s", message.get("action"))
        return

    try:
        async_to_sync(channel_layer.group_send)(group_name, message)
    except Exception:
        logger.exception("Failed to send channel message to group=%s", group_name)


def _serialize_notification(notification: Notification) -> dict:
    return NotificationSerializer(
        instance=notification,
        context={"scope": {"user": notification.recipient}},
    ).data


def send_notification_create(notification: Notification):
    """
    Sends create event.
    """
    if not notification or not notification.pk or not notification.recipient_id:
        return

    group_name = f"notifications_{notification.recipient_id}"
    message = {
        "type": "notification_activity",
        "action": "create",
        "pk": notification.pk,
        "data": _serialize_notification(notification),
        "response_status": 201,
    }
    _group_send(group_name, message)


def send_notification_update(notification: Notification):
    """
    Sends update event.
    """
    if not notification or not notification.pk or not notification.recipient_id:
        return

    group_name = f"notifications_{notification.recipient_id}"
    message = {
        "type": "notification_activity",
        "action": "update",
        "pk": notification.pk,
        "data": _serialize_notification(notification),
        "response_status": 200,
    }
    _group_send(group_name, message)


def _send_notification_delete_event(notification_id: int, recipient_id: int):
    """
    Sends delete event.
    """
    if not notification_id or not recipient_id:
        return

    group_name = f"notifications_{recipient_id}"
    message = {
        "type": "notification_activity",
        "action": "delete",
        "pk": notification_id,
        "data": {},
        "response_status": 204,
    }
    _group_send(group_name, message)


@shared_task
def send_notification_delete(notification_id: int, recipient_id: int):
    """
    Celery wrapper for delete events.
    """
    _send_notification_delete_event(notification_id, recipient_id)


# ---------------------------------------------------------------------
# Bulk notification helpers
# ---------------------------------------------------------------------

def _notify_users(
        users,
        text: str,
        push_title: str,
        push_body: str,
        data: dict | None = None,
        batch_size: int = 500,
        **notification_fields,
) -> None:
    """
    Create notifications for many users in batches.

    This avoids loading all users into memory and avoids one-by-one DB inserts.
    """
    notifications_batch = []
    push_ids = []

    def flush():
        if not notifications_batch:
            return

        try:
            created_notifications = Notification.objects.bulk_create(
                notifications_batch,
                batch_size=batch_size,
            )
        except Exception:
            logger.exception("Failed to bulk create %s notifications", len(notifications_batch))
            raise

        for notification in created_notifications:
            if notification.pk:
                send_notification_create(notification)

        send_push_to_user_ids(
            user_ids=push_ids,
            title=push_title,
            body=push_body,
            data=data,
        )

        notifications_batch.clear()
        push_ids.clear()

    iterable = users.iterator(chunk_size=batch_size) if hasattr(users, "iterator") else iter(users)

    for user in iterable:
        notifications_batch.append(
            Notification(
                recipient=user,
                text=text,
                **notification_fields,
            )
        )
        push_ids.append(user.pk)

        if len(notifications_batch) >= batch_size:
            flush()

    flush()


# ---------------------------------------------------------------------
# Aggregated notification helpers (follow / like / support)
# ---------------------------------------------------------------------

def _add_aggregated_notification(
        *,
        recipient,
        user,
        text: str,
        is_like: bool = False,
        is_follow: bool = False,
        is_support: bool = False,
        post=None,
        petition=None,
):
    """
    Add a user to an unread aggregated notification, creating it if necessary.

    Returns:
        (notification, created)
        If user was already present, returns (None, False) to prevent duplicate pushes/events.
    """
    if not recipient or not user:
        return None, False

    filters = {
        "recipient": recipient,
        "is_read": False,
        "is_like": is_like,
        "is_follow": is_follow,
        "is_support": is_support,
    }

    if post is not None:
        filters["post"] = post
    if petition is not None:
        filters["petition"] = petition

    try:
        with transaction.atomic():
            notification = Notification.objects.select_for_update().filter(**filters).first()

            if notification:
                if notification.users.filter(pk=user.pk).exists():
                    return None, False

                notification.users.add(user)
                return notification, False

            notification = Notification.objects.create(
                recipient=recipient,
                text=text,
                is_like=is_like,
                is_follow=is_follow,
                is_support=is_support,
                post=post,
                petition=petition,
            )
            notification.users.add(user)
            return notification, True

    except IntegrityError:
        # Defensive fallback if unique constraints are added and a race happens.
        notification = Notification.objects.filter(**filters).first()
        if notification and not notification.users.filter(pk=user.pk).exists():
            notification.users.add(user)
            return notification, False
        return None, False


def _remove_aggregated_notification(
        *,
        recipient_id: int,
        user_id: int,
        is_like: bool = False,
        is_follow: bool = False,
        is_support: bool = False,
        post_id: int | None = None,
        petition_id: int | None = None,
):
    """
    Remove a user from an unread aggregated notification.
    If the user was the last one, delete the notification.

    Delete WebSocket event is emitted by the Notification post_delete signal.
    Update event is emitted here when the notification still remains.
    """
    if not recipient_id or not user_id:
        return None, False

    filters = {
        "recipient_id": recipient_id,
        "is_read": False,
        "is_like": is_like,
        "is_follow": is_follow,
        "is_support": is_support,
        "users__id": user_id,
    }

    if post_id:
        filters["post_id"] = post_id
    if petition_id:
        filters["petition_id"] = petition_id

    updated_notification = None
    deleted = False

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(**filters).first()
        if not notification:
            return None, False

        if notification.users.count() == 1:
            notification.delete()
            deleted = True
        else:
            notification.users.remove(user_id)
            updated_notification = notification

    if updated_notification:
        send_notification_update(updated_notification)

    return updated_notification, deleted


# ---------------------------------------------------------------------
# Ballot notifications
# ---------------------------------------------------------------------

@shared_task
def create_ballot_notifications_on_create(ballot_id):
    ballot = Ballot.objects.filter(id=ballot_id).first()
    if not ballot:
        return

    users = _notification_enabled_users()
    users = _apply_location_filters(users, ballot)

    _notify_users(
        users=users,
        text="New ballot",
        push_title="New ballot",
        push_body=_truncate(ballot.title),
        ballot=ballot,
    )


# ---------------------------------------------------------------------
# Survey notifications
# ---------------------------------------------------------------------

@shared_task
def create_survey_notifications_on_create(survey_id):
    survey = Survey.objects.filter(id=survey_id).first()
    if not survey:
        return

    users = _notification_enabled_users()
    users = _apply_location_filters(users, survey)

    _notify_users(
        users=users,
        text="New survey",
        push_title="New survey",
        push_body=_truncate(survey.title),
        survey=survey,
    )


# ---------------------------------------------------------------------
# Petition notifications
# ---------------------------------------------------------------------

@shared_task
def create_petition_notifications_on_create(petition_id):
    petition = Petition.objects.select_related("author").filter(id=petition_id).first()
    if not petition or not petition.author_id:
        return

    users = _active_users().filter(
        notifiers=petition.author,
        preferences__allow_notifications=True,
        preferences__allow_petition_notifications=True,
    ).exclude(
        muted=petition.author,
    ).exclude(
        pk=petition.author_id,
    ).distinct()

    users = _apply_location_filters(users, petition)

    text = f"New petition from {petition.author}"
    _notify_users(
        users=users,
        text=text,
        push_title=text,
        push_body=_truncate(petition.title),
        petition=petition,
    )


@shared_task
def notify_on_petition_status_change(petition_id: int, is_open: bool):
    petition = Petition.objects.select_related("author").filter(id=petition_id).first()
    if not petition or not petition.author_id:
        return

    users = _active_users().filter(
        notifiers=petition.author,
        preferences__allow_notifications=True,
        preferences__allow_petition_notifications=True,
    ).exclude(
        muted=petition.author,
    ).exclude(
        pk=petition.author_id,
    ).distinct()

    users = _apply_location_filters(users, petition)

    text = (
        f"{petition.author} opened a petition"
        if is_open
        else f"{petition.author} closed a petition"
    )

    _notify_users(
        users=users,
        text=text,
        push_title="Petition",
        push_body=_truncate(text),
        petition=petition,
    )


# ---------------------------------------------------------------------
# Broadcast notifications
# ---------------------------------------------------------------------

def _send_broadcast_notifications(broadcast):
    if not broadcast or not broadcast.host_id:
        return

    is_live = broadcast.type == Broadcast.Type.LIVESTREAM

    users = _active_users().filter(
        notifiers=broadcast.host,
        preferences__allow_notifications=True,
    ).exclude(
        muted=broadcast.host,
    ).exclude(
        pk=broadcast.host_id,
    ).distinct()

    users = _apply_location_filters(users, broadcast)

    if is_live:
        push_title = f"{broadcast.host} started a live stream"
        notification_text = broadcast.title or push_title
    else:
        push_title = f"New broadcast from {broadcast.host}"
        notification_text = push_title

    _notify_users(
        users=users,
        text=notification_text,
        push_title=push_title,
        push_body=_truncate(broadcast.title or push_title),
        broadcast=broadcast,
    )


@shared_task
def create_broadcast_notifications_on_create(broadcast_id):
    broadcast = Broadcast.objects.select_related("host").filter(id=broadcast_id).first()
    _send_broadcast_notifications(broadcast)


@shared_task
def create_live_stream_notifications(broadcast_id):
    broadcast = Broadcast.objects.select_related("host").filter(id=broadcast_id).first()
    if not broadcast:
        return

    if broadcast.type != Broadcast.Type.LIVESTREAM:
        return

    _send_broadcast_notifications(broadcast)


# ---------------------------------------------------------------------
# Message notifications
# ---------------------------------------------------------------------

@shared_task
def create_message_notifications_on_create(message_id):
    message = Message.objects.select_related("chat", "author").filter(id=message_id).first()
    if not message or not message.chat_id or not message.author_id:
        return

    users = message.chat.users.filter(
        is_active=True,
        preferences__allow_notifications=True,
        preferences__allow_message_notifications=True,
    ).exclude(
        muted=message.author,
    ).exclude(
        pk=message.author_id,
    ).select_related("preferences").distinct()

    text = f"{message.author} sent a message"
    push_body = _truncate(getattr(message, "text", "") or "New message")

    _notify_users(
        users=users,
        text=text,
        push_title=text,
        push_body=push_body,
        chat=message.chat,
        message=message,
    )


# ---------------------------------------------------------------------
# Post notifications
# ---------------------------------------------------------------------

@shared_task
def create_post_notifications_on_create(post_id):
    post = Post.objects.select_related(
        "author__preferences",
        "reply_to__author__preferences",
        "repost_of__author__preferences",
    ).prefetch_related(
        "tagged_users__preferences",
    ).filter(id=post_id).first()

    if not post or not post.author_id:
        return

    author_id = post.author_id
    post_body = _truncate(getattr(post, "body", "") or "New post")

    # ------------------------------------------------------------
    # Notifications to followers excluding replies / reposts
    # ------------------------------------------------------------
    if not post.reply_to_id and not post.repost_of_id and not getattr(post, "is_muted", False):
        users = _active_users().filter(
            notifiers=post.author,
            preferences__allow_notifications=True,
        ).exclude(
            muted=post.author,
        ).exclude(
            pk=author_id,
        ).distinct()

        text = f"New post from {post.author}"
        _notify_users(
            users=users,
            text=text,
            push_title=text,
            push_body=post_body,
            post=post,
        )

    # ------------------------------------------------------------
    # Repost notification
    # ------------------------------------------------------------
    if post.repost_of_id:
        if post.repost_of.author_id != author_id and not getattr(post.repost_of, "is_muted", False):
            recipient = post.repost_of.author

            if recipient and _allows_notification(recipient, "allow_repost_notifications"):
                if not recipient.muted.filter(id=author_id).exists():
                    notification = Notification.objects.create(
                        recipient=recipient,
                        text=f"{post.author} reposted your post",
                        post=post,
                    )
                    send_notification_create(notification)
                    send_push_to_user(
                        user=recipient,
                        title=notification.text,
                        body=post_body,
                    )

    # ------------------------------------------------------------
    # Reply notification
    # ------------------------------------------------------------
    if post.reply_to_id:
        if post.reply_to.author_id != author_id and not getattr(post.reply_to, "is_muted", False):
            recipient = post.reply_to.author

            if recipient and _allows_notification(recipient, "allow_reply_notifications"):
                if not recipient.muted.filter(id=author_id).exists():
                    notification = Notification.objects.create(
                        recipient=recipient,
                        text=f"{post.author} replied to your post",
                        post=post,
                    )
                    send_notification_create(notification)
                    send_push_to_user(
                        user=recipient,
                        title=notification.text,
                        body=post_body,
                    )

    # ------------------------------------------------------------
    # Tagged users
    # ------------------------------------------------------------
    if post.tagged_users.exists():
        tagged_users = post.tagged_users.filter(
            is_active=True,
            preferences__allow_notifications=True,
            preferences__allow_tag_notifications=True,
        ).exclude(
            pk=author_id,
        ).exclude(
            muted=post.author,
        ).select_related("preferences").distinct()

        text = f"{post.author} tagged you in a post"
        _notify_users(
            users=tagged_users,
            text=text,
            push_title=text,
            push_body=post_body,
            post=post,
        )


# ---------------------------------------------------------------------
# Follow notifications
# ---------------------------------------------------------------------

@shared_task
def notify_on_follow(user_id, recipient_id):
    if not user_id or not recipient_id or user_id == recipient_id:
        return

    recipient = User.objects.select_related("preferences").filter(id=recipient_id).first()
    user = User.objects.filter(id=user_id).first()

    if not recipient or not user:
        return

    if not _allows_notification(recipient, "allow_follow_notifications"):
        return

    notification, created = _add_aggregated_notification(
        recipient=recipient,
        user=user,
        text="followed you",
        is_follow=True,
    )

    if not notification:
        return

    if created:
        send_notification_create(notification)
    else:
        send_notification_update(notification)

    display_name = getattr(user, "name", "") or user.username
    body = f"{display_name} @{user.username}".strip()

    send_push_to_user(
        user=recipient,
        title="New follower",
        body=_truncate(body),
    )


@shared_task
def delete_notification_on_unfollow(user_id, recipient_id):
    if not user_id or not recipient_id:
        return

    _remove_aggregated_notification(
        recipient_id=recipient_id,
        user_id=user_id,
        is_follow=True,
    )


# ---------------------------------------------------------------------
# Like notifications
# ---------------------------------------------------------------------

@shared_task
def notify_on_like(user_id, post_id):
    if not user_id or not post_id:
        return

    post = Post.objects.select_related("author__preferences").filter(id=post_id).first()
    user = User.objects.filter(id=user_id).first()

    if not post or not user:
        return

    if user_id == post.author_id or getattr(post, "is_muted", False):
        return

    author = post.author
    if not author or not _allows_notification(author, "allow_like_notifications"):
        return

    notification, created = _add_aggregated_notification(
        recipient=author,
        user=user,
        text="liked your post",
        is_like=True,
        post=post,
    )

    if not notification:
        return

    if created:
        send_notification_create(notification)
    else:
        send_notification_update(notification)

    display_name = getattr(user, "name", "") or user.username
    send_push_to_user(
        user=author,
        title="Post",
        body=_truncate(f"{display_name} liked your post"),
    )


@shared_task
def delete_notification_on_unlike(user_id, post_id):
    if not user_id or not post_id:
        return

    post = Post.objects.only("author_id").filter(id=post_id).first()
    if not post or not post.author_id:
        return

    if user_id == post.author_id:
        return

    _remove_aggregated_notification(
        recipient_id=post.author_id,
        user_id=user_id,
        is_like=True,
        post_id=post_id,
    )


# ---------------------------------------------------------------------
# Support notifications
# ---------------------------------------------------------------------

@shared_task
def notify_on_support(user_id, petition_id):
    if not user_id or not petition_id:
        return

    petition = Petition.objects.select_related("author__preferences").filter(id=petition_id).first()
    user = User.objects.filter(id=user_id).first()

    if not petition or not user:
        return

    if user_id == petition.author_id:
        return

    author = petition.author
    if not author or not _allows_notification(author, "allow_petition_supporter_notifications"):
        return

    notification, created = _add_aggregated_notification(
        recipient=author,
        user=user,
        text="supported your petition",
        is_support=True,
        petition=petition,
    )

    if not notification:
        return

    if created:
        send_notification_create(notification)
    else:
        send_notification_update(notification)

    display_name = getattr(user, "name", "") or user.username
    send_push_to_user(
        user=author,
        title="Petition",
        body=_truncate(f"{display_name} @{user.username} supported your petition"),
    )


@shared_task
def delete_notification_on_support_removal(user_id, petition_id):
    if not user_id or not petition_id:
        return

    petition = Petition.objects.only("author_id").filter(id=petition_id).first()
    if not petition or not petition.author_id:
        return

    if user_id == petition.author_id:
        return

    _remove_aggregated_notification(
        recipient_id=petition.author_id,
        user_id=user_id,
        is_support=True,
        petition_id=petition_id,
    )


# ---------------------------------------------------------------------
# Chat / message cleanup notifications
# ---------------------------------------------------------------------

@shared_task
def delete_notification_on_marked_as_read(chat_id, user_id):
    """
    Bulk delete unread chat notifications for a user.
    Since queryset.delete() does not emit post_delete signals, emit WS events manually.
    """
    if not chat_id or not user_id:
        return

    notification_ids = list(
        Notification.objects.filter(
            chat_id=chat_id,
            recipient_id=user_id,
            is_read=False,
        ).exclude(
            message__author_id=user_id,
        ).values_list("id", flat=True)
    )

    if not notification_ids:
        return

    Notification.objects.filter(id__in=notification_ids).delete()

    for notification_id in notification_ids:
        _send_notification_delete_event(notification_id, user_id)


@shared_task
def delete_notification_on_message_deletion(message_id):
    """
    Bulk delete unread notifications tied to a message.
    Since queryset.delete() does not emit post_delete signals, emit WS events manually.
    """
    if not message_id:
        return

    notifications = list(
        Notification.objects.filter(
            is_read=False,
            message_id=message_id,
        ).values_list("id", "recipient_id")
    )

    if not notifications:
        return

    notification_ids = [item[0] for item in notifications]
    Notification.objects.filter(id__in=notification_ids).delete()

    for notification_id, recipient_id in notifications:
        _send_notification_delete_event(notification_id, recipient_id)
