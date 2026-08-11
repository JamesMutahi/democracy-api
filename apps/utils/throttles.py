import asyncio
import logging
import time
import uuid
from functools import wraps
from typing import Callable, Optional, Tuple

import redis.asyncio as redis
from channels.db import database_sync_to_async
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("ratelimit")


class RateLimitExceeded(Exception):
    """Raised when rate limit is hit in sync context"""
    pass


class RateLimitDecorator:
    def __init__(self, limit: int = 60, period: int = 60, scope: str = "default"):
        self.limit = limit
        self.period = period
        self.scope = scope
        self._redis = None

    async def get_redis(self):
        if not self._redis:
            redis_url = getattr(settings, "REDIS_URL", None)
            if redis_url:
                self._redis = redis.from_url(redis_url, decode_responses=True)
        return self._redis

    def __call__(self, func: Callable):
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @wraps(func)
            async def async_wrapper(self_instance, *args, **kwargs):
                return await self._handle_async(func, self_instance, *args, **kwargs)

            return async_wrapper
        else:
            @wraps(func)
            async def sync_wrapper(self_instance, *args, **kwargs):
                try:
                    return await database_sync_to_async(
                        self._handle_sync(func)
                    )(self_instance, *args, **kwargs)
                except RateLimitExceeded:
                    # Unify behavior: send the 429 reply asynchronously
                    # even if the underlying handler is synchronous.
                    action_name = getattr(func, '__name__', self.scope)
                    request_id = kwargs.get('request_id')
                    if hasattr(self_instance, 'reply'):
                        await self_instance.reply(
                            action=action_name,
                            request_id=request_id,
                            errors=["Rate limit exceeded. Please try again later."],
                            status=429
                        )
                    return None

            return sync_wrapper

    # ==================== Rate Limit Checking ====================

    async def _handle_async(self, func, self_instance, *args, **kwargs):
        action_name = getattr(func, '__name__', self.scope)
        user = self_instance.scope.get('user')
        allowed, current_count = await self._check_rate_limit(self_instance, func, **kwargs)
        self._log_rate_limit(user, action_name, allowed, current_count)

        if not allowed:
            return None
        return await func(self_instance, *args, **kwargs)

    def _handle_sync(self, func):
        def wrapped(self_instance, *args, **kwargs):
            action_name = getattr(func, '__name__', self.scope)
            user = self_instance.scope.get('user')
            allowed, current_count = self._check_rate_limit_sync(self_instance, func, **kwargs)
            self._log_rate_limit(user, action_name, allowed, current_count)

            if not allowed:
                raise RateLimitExceeded("Rate limit exceeded")
            return func(self_instance, *args, **kwargs)

        return wrapped

    async def _check_rate_limit(self, self_instance, func, **kwargs) -> Tuple[bool, int]:
        """Returns (allowed, current_count)"""
        action_name = getattr(func, '__name__', self.scope)
        request_id = kwargs.get('request_id')
        key = self._build_key(self_instance, action_name)
        allowed, count = await self._check_limit_with_count(key)

        if not allowed and hasattr(self_instance, 'reply'):
            await self_instance.reply(
                action=action_name,
                request_id=request_id,
                errors=["Rate limit exceeded. Please try again later."],
                status=429
            )
        return allowed, count

    def _check_rate_limit_sync(self, self_instance, func, **kwargs) -> Tuple[bool, int]:
        action_name = getattr(func, '__name__', self.scope)
        key = self._build_key(self_instance, action_name)
        # Note: We don't reply here; the exception is caught in the async sync_wrapper
        return self._check_limit_with_count_sync(key)

    def _build_key(self, self_instance, action_name: str) -> str:
        user = self_instance.scope.get('user')

        # FIX: Prevent all anonymous users from sharing the same rate limit bucket
        if user and getattr(user, 'is_authenticated', False):
            identifier = user.id
        else:
            client = self_instance.scope.get('client')
            identifier = client[0] if client else 'unknown_ip'

        return f"ratelimit:ws:user:{identifier}:{action_name}"

    async def _check_limit_with_count(self, key: str) -> Tuple[bool, int]:
        """Returns (allowed, current_count)"""
        redis_client = await self.get_redis()
        now = int(time.time())

        if redis_client:
            # FIX: Pass unique_id to avoid Lua math.random() replication warnings
            unique_id = f"{now}:{uuid.uuid4().hex[:8]}"
            lua = """
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local window = tonumber(ARGV[2])
            local limit = tonumber(ARGV[3])
            local unique_id = ARGV[4]

            redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
            local count = redis.call('ZCARD', key)

            if count >= limit then
                return {0, count}
            end

            redis.call('ZADD', key, now, unique_id)
            redis.call('EXPIRE', key, window + 10)
            return {1, count + 1}
            """
            try:
                result = await redis_client.eval(lua, 1, key, now, self.period, self.limit, unique_id)
                return bool(result[0]), int(result[1])
            except Exception as e:
                # FIX: Log the error instead of failing silently
                logger.warning(f"Redis rate limit check failed, falling back to cache: {e}")

        # Django cache fallback (Warning: Prone to race conditions under high concurrency)
        history = cache.get(key, [])
        history = [ts for ts in history if now - ts < self.period]
        current_count = len(history)
        allowed = current_count < self.limit

        if allowed:
            history.append(now)
            cache.set(key, history, timeout=self.period + 10)
            current_count += 1

        return allowed, current_count

    def _check_limit_with_count_sync(self, key: str) -> Tuple[bool, int]:
        """Sync version"""
        now = int(time.time())
        history = cache.get(key, [])
        history = [ts for ts in history if now - ts < self.period]
        current_count = len(history)
        allowed = current_count < self.limit

        if allowed:
            history.append(now)
            cache.set(key, history, timeout=self.period + 10)
            current_count += 1

        return allowed, current_count

    def _log_rate_limit(self, user, action_name: str, allowed: bool, current_count: int):
        """Enhanced logging with current usage"""
        level = logging.WARNING if not allowed else logging.INFO
        status = "BLOCKED" if not allowed else "ALLOWED"
        user_id = getattr(user, 'id', 'anonymous')

        logger.log(
            level,
            f"Rate limit {status} | User: {user_id} | "
            f"Action: {action_name} | Usage: {current_count}/{self.limit} "
            f"in {self.period}s | Scope: {self.scope}"
        )


# ====================== Public Decorators ======================

def rate_limit(limit: int = 60, period: int = 60, scope: Optional[str] = None):
    def decorator(func):
        return RateLimitDecorator(
            limit=limit,
            period=period,
            # FIX: Use __name__ instead of name
            scope=scope or getattr(func, '__name__', 'default')
        )(func)

    return decorator


def interaction_rate_limit(func):
    return RateLimitDecorator(limit=80, period=60, scope="interaction")(func)


def strict_rate_limit(func):
    return RateLimitDecorator(limit=10, period=60, scope="strict")(func)
