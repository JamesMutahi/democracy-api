from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, update_last_login
from rest_framework.authtoken.models import Token

User = get_user_model()

class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        headers = dict(scope['headers'])
        if b'authorization' in headers:
            try:
                token_name, token_key = headers[b'authorization'].decode().split()
            except ValueError:
                token_key = None
            scope['user'] = AnonymousUser() if token_key is None else await get_user(token_key)
            return await super().__call__(scope, receive, send)
        else:
            scope['user'] = AnonymousUser()
            return await super().__call__(scope, receive, send)


@database_sync_to_async
def get_user(token):
    try:
        user = Token.objects.get(key=token).user
        update_last_login(User, user)
    except:
        user = AnonymousUser()
    return user
