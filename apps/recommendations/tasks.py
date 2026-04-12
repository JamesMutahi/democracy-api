from celery import shared_task

from apps.users.models import CustomUser
from .follow_recommender import FollowRecommender
from .post_recommender import PostRecommender

'''
Trigger this task on key events:

User likes/clicks/views a post
New post is published in their area
User follows/unfollows someone
Every 15–30 minutes via Celery Beat for active users

# With moderate diversity (recommended)
posts = recommender.get_recommendations(limit=15, diversity_factor=0.08)

# Pure ranking (no randomness)
posts = recommender.get_recommendations(limit=15, diversity_factor=0.0)

# Force fresh computation
posts = recommender.get_recommendations(limit=15, force_refresh=True)
'''


@shared_task
def refresh_user_recommendations(user_id, force=False):
    try:
        user = CustomUser.objects.get(id=user_id)
        recommender = PostRecommender(user)
        recommender.get_recommendations(limit=15, force_refresh=force, diversity_factor=0.08)
    except Exception:
        # TODO: Log error
        pass


@shared_task
def refresh_follow_recommendations(user_id, force=False):
    try:
        user = CustomUser.objects.get(id=user_id)
        recommender = FollowRecommender(user)
        recommender.get_follow_recommendations(limit=12, force_refresh=force)
    except Exception:
        # TODO: Log error
        pass
