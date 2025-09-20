from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from chat.models import Message
from live.models import Meeting
from notification.models import Notification, Preferences
from ballot.models import Ballot
from petition.models import Petition
from posts.models import Post
from survey.models import Survey

User = get_user_model()


@receiver(post_save, sender=Ballot)
@receiver(post_save, sender=Survey)
@receiver(post_save, sender=Message)
@receiver(post_save, sender=Post)
@receiver(post_save, sender=User)
def create_notification(sender, instance, created, **kwargs):
    if created:
        if sender == User:
            Preferences.objects.create(user=instance)
        if sender == Ballot:
            users = User.objects.all()
            for user in users:
                Notification.objects.get_or_create(
                    user=user,
                    text='New ballot',
                    ballot=instance,
                )
        if sender == Survey:
            users = User.objects.all()
            for user in users:
                Notification.objects.create(
                    user=user,
                    text='New survey',
                    survey=instance,
                )
        if sender == Petition:
            for user in instance.author.followers_notified.all():
                Notification.objects.create(
                    user=user,
                    text=f'New petition from {instance.author}',
                    petition=instance,
                )
        if sender == Meeting:
            for user in instance.author.followers_notified.all():
                Notification.objects.create(
                    user=user,
                    text=f'New meeting from {instance.author}',
                    meeting=instance,
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
        if sender == Post:
            for user in instance.author.followers_notified.all():
                Notification.objects.create(
                    user=user,
                    text=f'New post from {instance.author}',
                    post=instance,
                )
            if instance.repost_of:
                if instance.repost_of.author != instance.author:
                    Notification.objects.create(
                        user=instance.repost_of.author,
                        text=f'{instance.author} reposted your post',
                        post=instance,
                    )
            if instance.reply_to:
                if instance.reply_to.author != instance.author:
                    Notification.objects.create(
                        user=instance.reply_to.author,
                        text=f'{instance.author} replied to your post',
                        post=instance,
                    )
            if instance.tagged_users.exists():
                for user in instance.tagged_users.all():
                    if user != instance.author:
                        Notification.objects.create(
                            user=user,
                            text=f'{instance.author} tagged you in a post',
                            post=instance,
                        )
