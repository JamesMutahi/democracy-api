import time
from functools import wraps
from typing import Optional

import redis.asyncio as redis
from django.conf import settings
from django.core.cache import cache  # fallback


class RateLimitDecorator:
    def __init__(self, limit: int = 60, period: int = 60, scope: str = "default"):
        self.limit = limit
        self.period = period
        self.scope = scope

    async def get_redis(self):
        if not hasattr(self, "_redis"):
            redis_url = getattr(settings, "REDIS_URL", None)
            if redis_url:
                self._redis = redis.from_url(redis_url, decode_responses=True)
            else:
                self._redis = None
        return self._redis

    def __call__(self, func):
        @wraps(func)
        async def wrapper(self_instance, *args, **kwargs):
            action_name = getattr(func, '__name__', self.scope)
            request_id = kwargs.get('request_id')

            user = self_instance.scope.get('user')
            is_auth = getattr(user, 'is_authenticated', False)

            # Build unique key
            if is_auth:
                key = f"ratelimit:ws:user:{user.id}:{action_name}"
            else:
                client_ip = self_instance.scope.get('client', [None])[0] or 'anon'
                key = f"ratelimit:ws:ip:{client_ip}:{action_name}"

            allowed = await self._check_limit(key)

            if not allowed:
                await self_instance.reply(
                    action=action_name,
                    request_id=request_id,
                    errors=["Rate limit exceeded. Please try again later."],
                    status=429
                )
                return  # Block execution

            # Execute the actual action
            return await func(self_instance, *args, **kwargs)

        return wrapper

    async def _check_limit(self, key: str) -> bool:
        """Atomic sliding window using Redis Lua"""
        redis_client = await self.get_redis()
        now = int(time.time())

        if redis_client:
            lua = """
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local window = tonumber(ARGV[2])
            local limit = tonumber(ARGV[3])

            redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
            local count = redis.call('ZCARD', key)

            if count >= limit then
                return 0
            end

            redis.call('ZADD', key, now, now .. ':' .. math.random(999999))
            redis.call('EXPIRE', key, window + 10)
            return 1
            """

            try:
                result = await redis_client.eval(lua, 1, key, now, self.period, self.limit)
                return bool(result)
            except Exception:
                pass  # fallback to Django cache

        # Fallback to Django cache (thread-safe enough for most cases)
        history = cache.get(key, [])
        history = [ts for ts in history if now - ts < self.period]

        if len(history) >= self.limit:
            return False

        history.append(now)
        cache.set(key, history, timeout=self.period + 10)
        return True


# Convenient decorators
def rate_limit(limit: int = 60, period: int = 60, scope: Optional[str] = None):
    """Usage: @rate_limit(limit=30, period=60)"""

    def decorator(func):
        return RateLimitDecorator(limit=limit, period=period, scope=scope or func.__name__)(func)

    return decorator


# Predefined common limits
def strict_rate_limit(func):
    return RateLimitDecorator(limit=10, period=60, scope="strict")(func)


def interaction_rate_limit(func):
    return RateLimitDecorator(limit=80, period=60, scope="interaction")(func)