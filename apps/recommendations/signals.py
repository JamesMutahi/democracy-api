from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_save, post_delete
from django.dispatch import receiver

from apps.posts.models import PostLike, PostClick, Post
from apps.recommendations import tasks
from apps.users.models import ProfileVisit

User = get_user_model()


# === POST INTERACTIONS ===
@receiver(post_save, sender=Post)
def record_post_interaction(sender, instance: Post, created, **kwargs):
    """Record reply and repost interactions"""
    if not created or not instance.author:
        return

    if instance.reply_to is not None:
        tasks.record_interaction.delay(
            user_id=instance.author.id,
            post_id=instance.reply_to.id,
            interaction_type='reply',
        )

    elif instance.repost_of is not None and instance.body and instance.body.strip():
        # Only record Quotes (reposts with text)
        tasks.record_interaction.delay(
            user_id=instance.author.id,
            post_id=instance.repost_of.id,
            interaction_type='repost',
        )


@receiver(post_save, sender=PostLike)
@receiver(post_save, sender=PostClick)
def on_save_post_interaction(sender, instance, created, **kwargs):
    """Record user interation when the through model (PostLike/PostClick) is used"""
    tasks.record_interaction.delay(
        user_id=instance.user.id,
        post_id=instance.id,
        interaction_type='like' if sender == PostLike else 'click'
    )
    tasks.refresh_post_recommendations.delay(instance.user.id, force=True)


@receiver(m2m_changed, sender=Post.clicks.through)
@receiver(m2m_changed, sender=Post.likes.through)
@receiver(m2m_changed, sender=Post.bookmarks.through)
def record_like_interaction(sender, instance: Post, action, reverse, pk_set, **kwargs):
    """Record interactions"""
    if sender == Post.clicks.through:
        interaction_type = 'click'
    elif sender == Post.bookmarks.through:
        interaction_type = 'bookmark'
    else:
        interaction_type = 'like'
    if action == 'post_add' and not reverse:
        for user_id in pk_set:
            tasks.record_interaction.delay(
                user_id=user_id,
                post_id=instance.id,
                interaction_type=interaction_type
            )


@receiver(post_delete, sender=PostLike)
def on_post_like_deletion(sender, instance, **kwargs):
    tasks.refresh_post_recommendations.delay(instance.user.id, force=True)


# === PROFILE INTERACTIONS ===
@receiver(m2m_changed, sender=User.following.through)
def on_follow_change(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove']:
        tasks.refresh_follow_recommendations.delay(instance.id, force=True)


@receiver(m2m_changed, sender=User.muted.through)
@receiver(m2m_changed, sender=User.blocked.through)
def on_mute_block_change(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove']:
        tasks.refresh_follow_recommendations.delay(instance.id, force=True)
        tasks.refresh_post_recommendations.delay(instance.id, force=True)


@receiver(post_save, sender=ProfileVisit)
def on_profile_visit(sender, instance, created, **kwargs):
    tasks.refresh_follow_recommendations.delay(instance.visitor.id, force=True)
    tasks.refresh_post_recommendations.delay(instance.visitor.id, force=True)
