from django.contrib.auth import get_user_model
from django.test import TestCase

from users.models import Code
from users.utils.code import generate_code

User = get_user_model()


class TestUsersAppModels(TestCase):
    def setUp(self):
        self.user = User(
            username='test_user',
            email='testuser@gmail.com',
            name='Kenya',
        )
        self.user.save()

        self.email_verification_code = Code(
            user=self.user,
            code=generate_code(),
        )
        self.email_verification_code.save()

    def test_user_creation(self):
        self.assertEqual(User.objects.count(), 1)

    def test_user_representation(self):
        self.assertEqual(self.user, self.user)

    def test_get_full_name(self):
        self.assertEqual(self.user.first_name + ' ' + self.user.last_name, self.user.get_full_name())

    def test_short_name(self):
        self.assertEqual(self.user.first_name, self.user.get_short_name())

    def test_code_creation(self):
        self.assertEqual(Code.objects.count(), 1)

    def test_code_representation(self):
        self.assertEqual(self.email_verification_code, self.email_verification_code)
