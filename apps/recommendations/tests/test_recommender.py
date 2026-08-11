from django.conf import settings
from django.test import TestCase


class TestRecommender(TestCase):
    def test_post_recommender_weights_sum_to_one(self):
        weights = settings.POST_RECOMMENDER_CONFIG["SCORING_WEIGHTS"].values()
        assert abs(sum(weights) - 1.0) < 1e-6

    def test_follow_recommender_weights_sum_to_one(self):
        weights = settings.FOLLOW_RECOMMENDER_CONFIG["WEIGHTS"].values()
        assert abs(sum(weights) - 1.0) < 1e-6
