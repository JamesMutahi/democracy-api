from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_save, post_delete
from django.dispatch import receiver

from apps.posts.models import PostLike, PostClick
from apps.recommendations.tasks import (
    refresh_post_recommendations,
    refresh_follow_recommendations
)
from apps.users.models import ProfileVisit

User = get_user_model()


# === PROFILE INTERACTIONS ===
@receiver(m2m_changed, sender=User.following.through)
def on_follow_change(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove']:
        refresh_follow_recommendations.delay(instance.id, force=True)


@receiver(m2m_changed, sender=User.muted.through)
@receiver(m2m_changed, sender=User.blocked.through)
def on_mute_block_change(sender, instance, action, **kwargs):
    if action in ['post_add', 'post_remove']:
        refresh_follow_recommendations.delay(instance.id, force=True)
        refresh_post_recommendations.delay(instance.id, force=True)


@receiver(post_save, sender=ProfileVisit)
def on_profile_visit(sender, instance, created, **kwargs):
    refresh_follow_recommendations.delay(instance.visitor.id, force=True)
    refresh_post_recommendations.delay(instance.visitor.id, force=False)


# === POST INTERACTIONS ===
@receiver(post_save, sender=PostLike)
@receiver(post_save, sender=PostClick)
def on_save_post_interaction(sender, instance, created, **kwargs):
    refresh_post_recommendations.delay(instance.user.id, force=True)


@receiver(post_delete, sender=PostLike)
def on_post_like_deletion(sender, instance, **kwargs):
    refresh_post_recommendations.delay(instance.user.id, force=True)
