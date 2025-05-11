import json

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from users.models import Code
from users.utils.code import generate_code

User = get_user_model()


class TestUserViews(APITestCase):
    def setUp(self):
        self.API_client = APIClient()
        self.register_uri = '/auth/register/'
        self.registration_otp_resend = '/auth/register/resend-otp/'
        self.verification_uri = '/auth/register/verify/'
        self.login_uri = '/auth/login/'
        self.password_reset_verify_email_uri = '/auth/password-reset/verify-email/'
        self.password_reset_verify_code_uri = '/auth/password-reset/verify-code/'
        self.password_reset_uri = '/auth/password-reset/'
        self.password_change_uri = '/auth/password-change/'
        self.user_uri = '/auth/user/'

        self.user = User.objects.create_user(
            username='username', email="username@gmail.com", password="Limuru20!", is_verified=True
        )

        Token.objects.create(user=self.user)
        self.token = self.user.auth_token.key

        self.valid_data = {
            "id": self.user.id,
            "token": self.token,
            "email": self.user.email,
            "first_name": self.user.first_name,
            "last_name": self.user.last_name,
            "is_verified": True,
            "is_active": True,
            "is_staff": False,
        }

    def test_registration_process(self):
        params = {
            "email": "register@gmail.com",
            "first_name": "First",
            "last_name": "Last",
            "password": "Limuru20!",
            "password2": "Limuru20!",
        }
        response = self.API_client.post(self.register_uri, params)
        self.assertEqual(response.status_code, 201,
                         'Expected Response Code 201, received {0} instead.'
                         .format(response.status_code))

        # test unverified repeat registration
        response = self.API_client.post(self.register_uri, params)
        self.assertEqual(response.status_code, 201,
                         'Expected Response Code 201, received {0} instead.'
                         .format(response.status_code))

        user = User.objects.get(email="register@gmail.com")

        # test code verification
        obj = Code.objects.get(user=user)
        params = {
            "code": obj.code,
        }
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + user.auth_token.key)
        response = self.client.post(self.verification_uri, params)
        valid_data = {
            "id": user.id,
            "token": user.auth_token.key,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_verified": True,
            "is_active": True,
            "is_staff": False,
        }
        self.assertDictEqual(json.loads(response.content), valid_data)

        # test verified login
        params = {
            "email": "register@gmail.com",
            "password": "Limuru20!",
        }
        response = self.API_client.post(self.login_uri, params)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))

    def test_registration_otp_resend(self):
        params = {
            'email': 'username@gmail.com',
        }
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user.auth_token.key)
        response = self.client.post(self.registration_otp_resend, params)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))

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

    def test_password_reset_verify_email(self):
        params = {
            'email': 'username@gmail.com',
        }
        response = self.client.post(self.password_reset_verify_email_uri, params)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))

    def test_password_reset_verify_email_no_email_exists(self):
        params = {
            'email': 'emaildoesnotexist@gmail.com',
        }
        response = self.client.post(self.password_reset_verify_email_uri, params)
        self.assertEqual(response.status_code, 400,
                         'Expected Response Code 400, received {0} instead.'
                         .format(response.status_code))

    def test_password_reset_verify_code(self):
        code = generate_code()
        Code.objects.create(user=self.user, code=code)
        params = {
            'email': 'username@gmail.com',
            'code': code,
        }
        response = self.client.post(self.password_reset_verify_code_uri, params)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))
        self.assertDictEqual(json.loads(response.content), self.valid_data)

    def test_password_reset_verify_code_no_code_exists(self):
        params = {
            'code': 0000,
        }
        response = self.client.post(self.password_reset_verify_code_uri, params)
        self.assertEqual(response.status_code, 400,
                         'Expected Response Code 400, received {0} instead.'
                         .format(response.status_code))

    def test_password_reset(self):
        params = {
            'new_password1': "Limuru20!",
            'new_password2': "Limuru20!",
        }
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user.auth_token.key)
        response = self.client.post(self.password_reset_uri, params)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))

    def test_password_change_passwords_not_matching(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token[0])
        params = {
            "old_password": "Limuru20!",
            "new_password1": "154ehv49561",
            "new_password2": "154ehvlw49561",
        }
        response = self.client.post(self.password_change_uri, params)
        self.assertEqual(response.status_code, 401,
                         'Expected Response Code 400, received {0} instead.'
                         .format(response.status_code))

    def test_password_change_not_old_password(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token[0])
        params = {
            "old_password": "Limuru20!",
            "new_password1": "154ehv49561",
            "new_password2": "154ehvlw49561",
        }
        response = self.client.post(self.password_change_uri, params)
        self.assertEqual(response.status_code, 401,
                         'Expected Response Code 400, received {0} instead.'
                         .format(response.status_code))

    def test_password_change_accepted(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        params = {
            "old_password": "Limuru20!",
            "new_password1": "Limuru2020!",
            "new_password2": "Limuru2020!",
        }
        response = self.client.post(self.password_change_uri, params)
        self.assertEqual(response.status_code, 200,
                         f'Expected Response Code 200, received {response.status_code} instead.')
        self.assertIn(response.data, "New password has been saved.")

    def test_user_view(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        response = self.client.get(self.user_uri)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))
        self.assertDictEqual(json.loads(response.content), self.valid_data, 'Response data is not valid')

    def test_update_complete_view(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        response = self.client.get(self.user_uri)
        self.assertEqual(response.status_code, 200,
                         'Expected Response Code 200, received {0} instead.'
                         .format(response.status_code))

    def test_update_user(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        response = self.client.patch(self.user_uri)
        self.assertEqual(response.status_code, 200,
                         f'Expected Response Code 200, received {response.status_code} instead.')

    def test_retrieve_user_updates(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token)
        response = self.client.get(self.user_uri)
        self.assertEqual(response.status_code, 200,
                         f'Expected Response Code 200, received {response.status_code} instead.')
