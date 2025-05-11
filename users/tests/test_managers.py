from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class UsersManagersTests(TestCase):

    def test_create_user(self):
        user = User.objects.create_user(username='user', email='user@gmail.com', password='foo')
        self.assertEqual(user.username, 'user')
        self.assertEqual(user.email, 'user@gmail.com')
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        with self.assertRaises(TypeError):
            User.objects.create_user()
        with self.assertRaises(ValueError):
            User.objects.create_user(username='')
        with self.assertRaises(ValueError):
            User.objects.create_user(username='', email='user@gmail.com', password="foo")

    def test_create_superuser(self):
        admin_user = User.objects.create_superuser('superuser', 'foo')
        self.assertEqual(admin_user.username, 'superuser')
        self.assertTrue(admin_user.is_active)
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_superuser)
        try:
            # email is None for the AbstractUser option
            # email does not exist for the AbstractBaseUser option
            self.assertIsNone(admin_user.email)
        except AttributeError:
            pass
        with self.assertRaises(ValueError):
            User.objects.create_superuser(username='superuser', password='foo', is_superuser=False)
