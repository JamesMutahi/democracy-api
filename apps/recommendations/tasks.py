from celery import shared_task
from django.contrib.auth import get_user_model

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
    ).values_list('id', flat=True)[:500]   # limit batch size

    for user_id in active_users:
        refresh_follow_recommendations.delay(user_id)