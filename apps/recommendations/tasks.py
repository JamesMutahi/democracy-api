from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.recommendations.models import UserInteraction

User = get_user_model()

'''
# With moderate diversity (recommended)
posts = recommender.get_recommendations(limit=15, diversity_factor=0.08)

# Pure ranking (no randomness)
posts = recommender.get_recommendations(limit=15, diversity_factor=0.0)

# Force fresh computation
posts = recommender.get_recommendations(limit=15, force_refresh=True)
'''


@shared_task
@transaction.atomic
def record_interaction(user_id: int, post_id: int, interaction_type: str):
    """
    Record user interaction with rate limiting to prevent duplicates/spam.
    """
    if not user_id or not post_id:
        return

    interaction_type = interaction_type.lower()

    # Rate limiting key
    cache_key = f"interaction_rate:{user_id}:{post_id}:{interaction_type}"
    limit_seconds = {
        'view': 30,
        'click': 10,
        'like': 5,
        'bookmark': 30,
        'reply': 60,
        'repost': 60,
    }.get(interaction_type, 30)

    # Skip if recently recorded
    if cache.get(cache_key):
        return

    # Record the interaction
    UserInteraction.objects.get_or_create(
        user_id=user_id,
        post_id=post_id,
        interaction_type=interaction_type,
        defaults={'created_at': timezone.now()}
    )

    # Set rate limit
    cache.set(cache_key, True, timeout=limit_seconds)


@shared_task
def refresh_post_recommendations(user_id: int, force=False):
    from apps.recommendations.post_recommender import PostRecommender
    try:
        user = User.objects.get(id=user_id)
        recommender = PostRecommender(user)
        recommender.get_recommendations(limit=30, force_refresh=force)
    except Exception:
        pass


@shared_task
def refresh_follow_recommendations(user_id: int, force=False):
    from apps.recommendations.follow_recommender import FollowRecommender
    try:
        user = User.objects.get(id=user_id)
        recommender = FollowRecommender(user)
        recommender.get_follow_recommendations(limit=30, force_refresh=force)
    except Exception:
        pass


@shared_task
def refresh_all_active_users():
    """Refresh for users who have been active recently"""
    from django.utils import timezone
    from datetime import timedelta

    active_users = User.objects.filter(
        last_login__gte=timezone.now() - timedelta(hours=24)
    ).values_list('id', flat=True)[:500]  # limit batch size

    for user_id in active_users:
        refresh_follow_recommendations.delay(user_id)
