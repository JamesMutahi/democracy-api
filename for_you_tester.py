import os
import django

# 1. Set the settings module (replace 'myproject' with your project folder name)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')

# 2. Initialize Django
django.setup()

# 3. Now import and use your models
from django.contrib.auth import get_user_model
User = get_user_model()
from apps.recommendations.post_recommender import PostRecommender
recommender = PostRecommender(User.objects.last())

# With moderate diversity (recommended)
posts = recommender.get_recommendations(limit=20, force_refresh=True, diversity_factor=0.08, exclude_post_ids=None)

# Pure ranking (no randomness)
# posts = recommender.get_recommendations(limit=15, diversity_factor=0.0)

# Force fresh computation
# posts = recommender.get_recommendations(limit=15, force_refresh=True)

print(posts)