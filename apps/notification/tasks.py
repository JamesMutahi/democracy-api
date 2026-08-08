from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import transaction
from fcm_django.models import FCMDevice
from firebase_admin.messaging import Message as fireMessage, Notification as fireNotification

from apps.ballot.models import Ballot
from apps.broadcast.models import Broadcast
from apps.chat.models import Message
from apps.notification.models import Notification
from apps.notification.serializers import NotificationSerializer
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey

User = get_user_model()
channel_layer = get_channel_layer()


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
        else:
            value = getattr(obj, field, None)
            if value:
                users = users.filter(**{field: value})

    return users


# ---------------------------------------------------------------------
# Push notification helpers
# ---------------------------------------------------------------------

def send_push_to_user(user, title, body, data=None):
    devices = FCMDevice.objects.filter(user=user, active=True)
    devices.send_message(
        fireMessage(
            notification=fireNotification(title=title, body=body),
            data=_normalize_fcm_data(data),
        )
    )


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

    devices.send_message(
        fireMessage(
            notification=fireNotification(
                title=title,
                body=body,
            ),
            data=_normalize_fcm_data(data),
        )
    )


def send_notification_create(notification: Notification):
    """ Sends create event """
    if not notification or not notification.recipient.id:
        return

    group_name = f"notifications_{notification.recipient.id}"

    serializer = NotificationSerializer(instance=notification, context={'scope': {'user': notification.recipient}})

    message = {
        "type": "notification_activity",
        "action": "create",
        "pk": notification.pk,
        "data": serializer.data,
        "response_status": 201,
    }
    async_to_sync(channel_layer.group_send)(group_name, message)


def send_notification_update(notification: Notification):
    """ Sends update event """
    if not notification or not notification.recipient.id:
        return

    group_name = f"notifications_{notification.recipient.id}"

    serializer = NotificationSerializer(instance=notification, context={'scope': {'user': notification.recipient}})

    message = {
        "type": "notification_activity",
        "action": "update",
        "pk": notification.pk,
        "data": serializer.data,
        "response_status": 200,
    }

    async_to_sync(channel_layer.group_send)(group_name, message)


@shared_task
def send_notification_delete(notification_id: int, recipient_id: int):
    """ Sends delete event """
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

    async_to_sync(channel_layer.group_send)(group_name, message)


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

        created_notifications = Notification.objects.bulk_create(
            notifications_batch,
            batch_size=batch_size,
        )

        for notification in created_notifications:
            send_notification_create(notification)

        send_push_to_user_ids(
            user_ids=push_ids,
            title=push_title,
            body=push_body,
            data=data,
        )

        notifications_batch.clear()
        push_ids.clear()

    for user in users.iterator(chunk_size=batch_size):
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
# Ballot notifications
# ---------------------------------------------------------------------

@shared_task
def create_ballot_notifications_on_create(ballot_id):
    ballot = Ballot.objects.filter(id=ballot_id).first()
    if not ballot: return

    users = _notification_enabled_users()
    users = _apply_location_filters(users, ballot)

    _notify_users(
        users=users,
        text="New ballot",
        push_title="New ballot",
        push_body=ballot.title,
        ballot=ballot,
    )


# ---------------------------------------------------------------------
# Survey notifications
# ---------------------------------------------------------------------

@shared_task
def create_survey_notifications_on_create(survey_id):
    survey = Survey.objects.filter(id=survey_id).first()
    if not survey: return

    users = _notification_enabled_users()
    users = _apply_location_filters(users, survey)

    _notify_users(
        users=users,
        text="New survey",
        push_title="New survey",
        push_body=survey.title,
        survey=survey,
    )


# ---------------------------------------------------------------------
# Petition notifications
# ---------------------------------------------------------------------

@shared_task
def create_petition_notifications_on_create(petition_id):
    petition = Petition.objects.filter(id=petition_id).first()
    if not petition: return

    author_id = petition.author_id

    users = _active_users().filter(
        notifiers=petition.author,
        preferences__allow_petition_notifications=True,
    ).exclude(
        muted=petition.author,
    ).exclude(
        pk=author_id,
    ).distinct()

    users = _apply_location_filters(users, petition)

    text = f"New petition from {petition.author}"

    _notify_users(
        users=users,
        text=text,
        push_title=text,
        push_body=petition.title,
        petition=petition,
    )


# ---------------------------------------------------------------------
# Broadcast notifications
# ---------------------------------------------------------------------

@shared_task
def create_broadcast_notifications_on_create(broadcast_id):
    broadcast = Broadcast.objects.select_related("host").filter(id=broadcast_id).first()
    if not broadcast: return

    if broadcast.type == Broadcast.Type.LIVESTREAM:
        return

    host_id = broadcast.host_id

    users = _active_users().filter(
        notifiers=broadcast.host,
        preferences__allow_notifications=True,
    ).exclude(
        muted=broadcast.host,
    ).exclude(
        pk=host_id,
    ).distinct()

    users = _apply_location_filters(users, broadcast)

    text = f"New broadcast from {broadcast.host}"

    _notify_users(
        users=users,
        text=text,
        push_title=text,
        push_body=broadcast.title,
        broadcast=broadcast,
    )


@shared_task
def create_live_stream_notifications(broadcast_id):
    broadcast = Broadcast.objects.select_related("host").filter(id=broadcast_id).first()
    if not broadcast: return

    if broadcast.type != Broadcast.Type.LIVESTREAM:
        return

    host_id = broadcast.host_id

    users = _active_users().filter(
        notifiers=broadcast.host,
        preferences__allow_notifications=True,
    ).exclude(
        muted=broadcast.host,
    ).exclude(
        pk=host_id,
    ).distinct()

    users = _apply_location_filters(users, broadcast)

    text = f"{broadcast.host} started a live stream"

    _notify_users(
        users=users,
        text=broadcast.title,
        push_title=text,
        push_body=broadcast.title,
        broadcast=broadcast,
    )


# ---------------------------------------------------------------------
# Message notifications
# ---------------------------------------------------------------------

@shared_task
def create_message_notifications_on_create(message_id):
    message = Message.objects.filter(id=message_id).first()
    if not message: return

    users = message.chat.users.filter(
        is_active=True,
        preferences__allow_notifications=True,
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
    if not post: return

    author_id = post.author_id
    post_body = _truncate(getattr(post, "body", ""))

    # ------------------------------------------------------------
    # Notifications to followers excluding replies
    # ------------------------------------------------------------
    if not post.reply_to and not post.repost_of and not post.is_muted:
        users = _active_users().filter(
            notifiers=post.author,
            preferences__allow_notifications=True
        ).exclude(
            muted=post.author
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
    if post.repost_of:
        if post.repost_of.author != post.author and not post.repost_of.is_muted:
            allowed = post.repost_of.author.preferences.allow_repost_notifications and \
                      not post.repost_of.author.muted.contains(post.author)
            if allowed:
                notification = Notification.objects.create(
                    recipient=post.repost_of.author,
                    text=f'{post.author} reposted your post',
                    post=post,
                )
                send_notification_create(notification)
                send_push_to_user(
                    user=post.repost_of.author,
                    title=notification.text,
                    body=post_body
                )

    # ------------------------------------------------------------
    # Reply notification
    # ------------------------------------------------------------
    if post.reply_to:
        if post.reply_to.author != post.author and not post.reply_to.is_muted:
            allowed = post.reply_to.author.preferences.allow_reply_notifications and \
                      not post.reply_to.author.muted.contains(post.author)
            if allowed:
                notification = Notification.objects.create(
                    recipient=post.reply_to.author,
                    text=f'{post.author} replied to your post',
                    post=post,
                )
                send_notification_create(notification)
                send_push_to_user(
                    user=post.reply_to.author,
                    title=notification.text,
                    body=post_body
                )

    # ------------------------------------------------------------
    # Tagged users
    # ------------------------------------------------------------
    if post.tagged_users.exists():
        for user in post.tagged_users.filter(
                is_active=True
        ).exclude(
            pk=author_id,
        ).exclude(
            muted=post.author,
        ).select_related("preferences").distinct():
            allowed = post.author.preferences.allow_tag_notifications and \
                      not user.muted.filter(id=post.author.id).exists()
            if allowed:
                notification = Notification.objects.create(
                    recipient=user,
                    text=f'{post.author} tagged you in a post',
                    post=post,
                )
                send_notification_create(notification)
                send_push_to_user(user=user, title=notification.text, body=post_body)


# ---------------------------------------------------------------------
# Follow notifications
# ---------------------------------------------------------------------
@shared_task
def notify_on_follow(user_id, recipient_id):
    recipient = User.objects.select_related('preferences').get(id=recipient_id)

    if not recipient.preferences.allow_follow_notifications:
        return

    user = User.objects.get(id=user_id)

    created = False
    notification = None

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            is_read=False,
            recipient_id=recipient_id,
            is_follow=True,
        ).first()

        if notification:
            notification.users.add(user_id)
        else:
            notification = Notification.objects.create(
                recipient=recipient,
                text="followed you",
                is_follow=True,
            )
            notification.users.add(user_id)
            created = True

    if not notification:
        return

    if created:
        send_notification_create(notification)
    else:
        send_notification_update(notification)

    send_push_to_user(
        user=recipient,
        title='New follower',
        body=f'{user.name} @{user.username}'
    )


@shared_task
def delete_notification_on_unfollow(user_id, recipient_id):
    user = User.objects.get(id=user_id)

    deleted = False
    notification = None
    notification_id = None
    notification_recipient_id = None

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            is_read=False,
            recipient_id=recipient_id,
            is_follow=True,
            users=user,
        ).first()

        if not notification:
            return

        notification_id = notification.id
        notification_recipient_id = notification.recipient_id

        if notification.users.count() == 1:
            notification.delete()
            deleted = True
        else:
            notification.users.remove(user)

    if deleted:
        send_notification_delete(notification_id, notification_recipient_id)
    elif notification:
        send_notification_update(notification)


@shared_task
def notify_on_like(user_id, post_id):
    post = Post.objects.select_related('author__preferences').filter(id=post_id).first()
    user = User.objects.get(id=user_id)

    if not post: return

    if user_id == post.author_id or post.is_muted or not post.author.preferences.allow_like_notifications:
        return

    created = False
    notification = None

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            is_read=False,
            post_id=post_id,
            recipient_id=post.author_id,
            is_like=True,
        ).first()

        if notification:
            notification.users.add(user_id)
        else:
            notification = Notification.objects.create(
                recipient=post.author,
                text="liked your post",
                post=post,
                is_like=True,
            )
            notification.users.add(user_id)
            created = True

    if not notification:
        return

    if created:
        send_notification_create(notification)
    else:
        send_notification_update(notification)

    send_push_to_user(
        user=post.author,
        title='Post',
        body=f'{user.name} @{user.username} liked your post'
    )


@shared_task
def delete_notification_on_unlike(user_id, post_id):
    user = User.objects.get(id=user_id)

    post = Post.objects.select_related("author").filter(id=post_id).first()

    if not post: return

    deleted = False
    notification = None
    notification_id = None
    notification_recipient_id = None

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            is_read=False,
            post_id=post_id,
            is_like=True,
            users=user,
        ).first()

        if not notification:
            return

        notification_id = notification.id
        notification_recipient_id = notification.recipient_id

        if notification.users.count() == 1:
            notification.delete()
            deleted = True
        else:
            notification.users.remove(user)

    if deleted:
        send_notification_delete(notification_id, notification_recipient_id)
    elif notification:
        send_notification_update(notification)


@shared_task
def notify_on_support(user_id, petition_id):
    petition = Petition.objects.select_related('author__preferences').filter(id=petition_id).first()

    if not petition: return

    if user_id == petition.author_id or not petition.author.preferences.allow_petition_supporter_notifications:
        return

    user = User.objects.get(id=user_id)

    created = False
    notification = None

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            is_read=False,
            petition_id=petition_id,
            recipient_id=petition.author_id,
            is_support=True,
        ).first()

        if notification:
            notification.users.add(user_id)
        else:
            notification = Notification.objects.create(
                recipient=petition.author,
                text="supported your petition",
                petition=petition,
                is_support=True,
            )
            notification.users.add(user_id)
            created = True

    if not notification:
        return

    if created:
        send_notification_create(notification)
    else:
        send_notification_update(notification)

    send_push_to_user(
        user=petition.author,
        title='Petition',
        body=f'{user.name} @{user.username} supported your petition',
    )


@shared_task
def delete_notification_on_support_removal(user_id, petition_id):
    user = User.objects.get(id=user_id)

    petition = Petition.objects.select_related("author").filter(id=petition_id).first()
    if not petition: return

    if user != petition.author:
        return

    deleted = False
    notification_id = None
    notification_recipient_id = None

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            is_read=False,
            petition_id=petition_id,
            is_support=True,
            users=user,
        ).first()

        if not notification:
            return

        notification_id = notification.id
        notification_recipient_id = notification.recipient_id

        if notification.users.count() == 1:
            notification.delete()
            deleted = True
        else:
            notification.users.remove(user)

    if deleted:
        send_notification_delete(notification_id, notification_recipient_id)
    elif notification:
        send_notification_update(notification)


@shared_task
def delete_notification_on_marked_as_read(chat_id, user_id):
    Notification.objects.filter(chat=chat_id).exclude(message__author=user_id).delete()


@shared_task
def delete_notification_on_message_deletion(message_id):
    Notification.objects.filter(is_read=False, message_id=message_id).delete()


@shared_task
def notify_on_petition_status_change(petition_id: int, is_open: bool):
    petition = Petition.objects.select_related("author").filter(id=petition_id).first()
    if not petition: return

    author_id = petition.author_id

    users = _active_users().filter(
        notifiers=petition.author,
        preferences__allow_notifications=True,
    ).exclude(
        muted=petition.author,
    ).exclude(
        pk=author_id,
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
        push_body=text,
        petition=petition,
    )
