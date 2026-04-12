from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from apps.posts.models import Post

User = get_user_model()


class UserInteraction(models.Model):
    """Track user interactions for better recommendations"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='interactions')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='interactions')

    INTERACTION_TYPES = (
        ('click', 'Clicked'),
        ('like', 'Liked'),
        ('bookmark', 'Bookmarked'),
        ('reply', 'Replied'),
        ('repost', 'Reposted'),
    )
    interaction_type = models.CharField(max_length=20, choices=INTERACTION_TYPES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'UserInteraction'
        unique_together = ('user', 'post', 'interaction_type')
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['post', 'interaction_type']),
        ]


class PostRecommendationCache(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='post_recommendation_cache')
    recommended_post_ids = models.JSONField(default=list)  # Store only IDs for efficiency
    scores = models.JSONField(default=dict)  # {post_id: score}
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "PostRecommendationCache"
        verbose_name = "Post Recommendation Cache"

    def is_stale(self, max_age_minutes=30):
        """Check if cache needs refresh"""
        if not self.generated_at:
            return True
        age = timezone.now() - self.generated_at
        return age.total_seconds() > (max_age_minutes * 60)

    def get_recommended_posts(self, limit=20):
        """Return actual Post objects from cached IDs"""
        if not self.recommended_post_ids:
            return Post.objects.none()

        # Preserve order and add scores
        posts = Post.objects.filter(
            id__in=self.recommended_post_ids[:limit]
        ).select_related('author').prefetch_related('likes', 'clicks')

        # Annotate score for each post (from cache)
        for post in posts:
            post.cached_score = self.scores.get(str(post.id), 0.0)

        # Re-order by cached score (since DB order may differ)
        posts = sorted(posts, key=lambda p: getattr(p, 'cached_score', 0), reverse=True)
        return posts


class FollowRecommendationCache(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='follow_recommendation_cache')
    recommended_users = models.ManyToManyField(User, blank=True, related_name='recommended_to')
    scores = models.JSONField(default=dict)  # {user_id: score}
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "FollowRecommendationCache"
        verbose_name = "Follow Recommendation Cache"

    def is_stale(self, max_age_minutes=60):
        age = timezone.now() - self.generated_at
        return age.total_seconds() > (max_age_minutes * 60)