from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class TestUsersAppModels(TestCase):
    def setUp(self):
        self.user = User(
            username='test_user',
            email='testuser@gmail.com',
            name='Kenya',
        )
        self.user.save()

    def test_user_creation(self):
        self.assertEqual(User.objects.count(), 1)

    def test_user_representation(self):
        self.assertEqual(self.user, self.user)

