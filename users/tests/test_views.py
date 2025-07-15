import json

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

User = get_user_model()


class TestUserViews(APITestCase):
    def setUp(self):
        self.API_client = APIClient()
        self.login_uri = '/auth/login/'

        self.user = User.objects.create_user(
            username='username', email="username@gmail.com", password="Limuru20!", is_verified=True
        )

        Token.objects.create(user=self.user)
        self.token = self.user.auth_token.key

        self.valid_data = {
            "id": self.user.id,
            "token": self.token,
            "email": self.user.email,
            "name": self.user.name,
            "is_active": True,
            "is_staff": False,
        }

    def test_login_view(self):
        params = {
            "email": "username@gmail.com",
            "password": "Limuru20!",
        }
        response = self.API_client.post(self.login_uri, params)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))

    def test_login_response(self):
        params = {
            "email": "username@gmail.com",
            "password": "Limuru20!",
        }
        response = self.API_client.post(self.login_uri, params)
        self.assertDictEqual(json.loads(response.content), self.valid_data)