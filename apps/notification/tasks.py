from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model

from apps.ballot.models import Ballot
from apps.chat.models import Message
from apps.meeting.models import Meeting
from apps.notification.models import Notification
from apps.notification.serializers import NotificationSerializer
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey

User = get_user_model()
channel_layer = get_channel_layer()


def send_notification_create(notification: Notification):
    """ Sends create event """
    if not notification or not notification.recipient_id:
        return

    group_name = f"notifications_{notification.recipient_id}"

    serializer = NotificationSerializer(instance=notification, context={'scope': {'user': notification.recipient}})

    message = {
        "type": "notification_activity",
        "action": "create",
        "pk": notification.pk,
        "data": serializer.data,
        "response_status": 201,
    }

    async_to_sync(channel_layer.group_send)(group_name, message)


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


@shared_task
def create_ballot_notifications_on_create(ballot_id):
    users = User.objects.all()
    ballot = Ballot.objects.get(id=ballot_id)
    if ballot.county:
        users = users.filter(county=ballot.county)
        if ballot.constituency:
            users = users.filter(constituency=ballot.constituency)
            if ballot.ward:
                users = users.filter(ward=ballot.ward)
    for user in users:
        if user.preferences.allow_notifications:
            notification = Notification.objects.create(
                recipient=user,
                text='New ballot',
                ballot=ballot,
            )
            send_notification_create(notification)


@shared_task
def create_survey_notifications_on_create(survey_id):
    users = User.objects.all()
    survey = Survey.objects.get(id=survey_id)
    if survey.county:
        users = users.filter(county=survey.county)
        if survey.constituency:
            users = users.filter(constituency=survey.constituency)
            if survey.ward:
                users = users.filter(ward=survey.ward)
    for user in users:
        if user.preferences.allow_notifications:
            notification = Notification.objects.create(
                recipient=user,
                text='New survey',
                survey=survey,
            )
            send_notification_create(notification)


@shared_task
def create_petition_notifications_on_create(petition_id):
    petition = Petition.objects.get(id=petition_id)
    users = User.objects.filter(
        preferences__in=petition.author.followers_notified.all(),
        preferences__allow_notifications=True
    ).exclude(muted=petition.author)
    if petition.county:
        users = users.filter(county=petition.county)
        if petition.constituency:
            users = users.filter(constituency=petition.constituency)
            if petition.ward:
                users = users.filter(ward=petition.ward)
    for user in users:
        notification = Notification.objects.create(
            recipient=user,
            text=f'New petition from {petition.author}',
            petition=petition,
        )
        send_notification_create(notification)


@shared_task
def create_meeting_notifications_on_create(meeting_id):
    meeting = Meeting.objects.get(id=meeting_id)
    users = User.objects.filter(
        preferences__in=meeting.host.followers_notified.all(),
        preferences__allow_notifications=True
    ).exclude(muted=meeting.host)
    if meeting.county:
        users = users.filter(county=meeting.county)
        if meeting.constituency:
            users = users.filter(constituency=meeting.constituency)
            if meeting.ward:
                users = users.filter(ward=meeting.ward)
    for user in users:
        notification = Notification.objects.create(
            recipient=user,
            text=f'New meeting from {meeting.host}',
            meeting=meeting,
        )
        send_notification_create(notification)


@shared_task
def create_message_notifications_on_create(message_id):
    message = Message.objects.get(id=message_id)
    users = message.chat.users.exclude(id=message.author.id)
    for user in users:
        allowed = user.preferences.allow_notifications and \
                  not user.muted.contains(message.author)
        if allowed:
            notification = Notification.objects.create(
                recipient=user,
                text=f'{message.author} sent a message',
                chat=message.chat,
                message=message,
            )
            send_notification_create(notification)


@shared_task
def create_post_notifications_on_create(post_id):
    post = Post.objects.get(id=post_id)

    # Notifications to followers excluding replies
    if not post.reply_to:
        users_to_notify = User.objects.filter(
            preferences__in=post.author.followers_notified.all(),
            preferences__allow_notifications=True
        ).exclude(muted=post.author)
        for user in users_to_notify:
            notification = Notification.objects.create(
                recipient=user,
                text=f'New post from {post.author}',
                post=post,
            )
            send_notification_create(notification)

    # Repost notification
    if post.repost_of:
        if post.repost_of.author != post.author:
            allowed = post.repost_of.author.preferences.allow_repost_notifications and \
                      not post.repost_of.author.muted.contains(post.author) and \
                      not post.repost_of.author.preferences.muted_posts.contains(post.repost_of)
            if allowed:
                notification = Notification.objects.create(
                    recipient=post.repost_of.author,
                    text=f'{post.author} reposted your post',
                    post=post,
                )
                send_notification_create(notification)

    # Reply notification
    if post.reply_to:
        if post.reply_to.author != post.author:
            allowed = post.reply_to.author.preferences.allow_reply_notifications and \
                      not post.reply_to.author.muted.contains(post.author) and \
                      not post.reply_to.author.preferences.muted_posts.contains(post.reply_to)
            if allowed:
                notification = Notification.objects.create(
                    recipient=post.reply_to.author,
                    text=f'{post.author} replied to your post',
                    post=post,
                )
                send_notification_create(notification)

    # Tagged users
    if post.tagged_users.exists():
        for user in post.tagged_users.all():
            if user != post.author:
                allowed = post.author.preferences.allow_tag_notifications and \
                          not user.muted.contains(post.author)
                if allowed:
                    notification = Notification.objects.create(
                        recipient=user,
                        text=f'{post.author} tagged you in a post',
                        post=post,
                    )
                    send_notification_create(notification)


@shared_task
def notify_on_follow(user_id, recipient_id):
    user = User.objects.get(id=user_id)
    recipient = User.objects.get(id=recipient_id)
    notification = Notification.objects.create(
        recipient=recipient,
        text=f'{user} followed you',
        user=user,
    )
    send_notification_create(notification)


@shared_task
def delete_notification_on_unfollow(user_id, recipient_id):
    user = User.objects.get(id=user_id)
    recipient = User.objects.get(id=recipient_id)

    # Get IDs before deleting
    pks_to_delete = list(
        Notification.objects.filter(
            recipient=recipient,
            user_id=user_id,
            post__isnull=True,
            petition__isnull=True,
            meeting__isnull=True,
            message__isnull=True,
            chat__isnull=True,
        ).values_list('pk', flat=True)
    )

    Notification.objects.filter(
        recipient=recipient,
        user=user,
        post__isnull=True,
        petition__isnull=True,
        meeting__isnull=True,
        message__isnull=True,
        chat__isnull=True,
    ).delete()

    # Notify frontend
    for pk in pks_to_delete:
        send_notification_delete(notification_id=pk, recipient_id=recipient_id)


@shared_task
def notify_on_like(user_id, post_id):
    user = User.objects.get(id=user_id)
    post = Post.objects.get(id=post_id)
    if user != post.author:
        notification = Notification.objects.create(
            recipient=post.author,
            text=f'{user} liked your post',
            post=post,
            user=user,
        )
        send_notification_create(notification)


@shared_task
def delete_notification_on_unlike(user_id, post_id):
    user = User.objects.get(id=user_id)
    post = Post.objects.get(id=post_id)

    pks_to_delete = list(
        Notification.objects.filter(
            recipient_id=post.author_id,
            post=post,
            user_id=user_id,
        ).values_list('pk', flat=True)
    )

    if user != post.author:
        Notification.objects.filter(
            recipient=post.author,
            post=post,
            user=user,
        ).delete()

    for pk in pks_to_delete:
        send_notification_delete(notification_id=pk, recipient_id=post.author_id)


@shared_task
def notify_on_petition_status_change(petition_id: int, is_open: bool):
    petition = Petition.objects.get(id=petition_id)
    users = petition.author.followers_notified.all()
    if petition.county:
        users = users.filter(county=petition.county)
        if petition.constituency:
            users = users.filter(constituency=petition.constituency)
            if petition.ward:
                users = users.filter(ward=petition.ward)

    for user in users:
        if user.preferences.allow_notifications:
            text = f'{petition.author} opened a petition' if is_open else f'{petition.author} closed a petition'
            notification = Notification.objects.create(
                recipient=user,
                text=text,
                petition=petition,
            )
            send_notification_create(notification)
