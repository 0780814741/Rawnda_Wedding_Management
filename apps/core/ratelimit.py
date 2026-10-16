"""
Small fixed-window rate limiter backed by Django's cache (DB cache in production so the
limit is shared across workers). Good enough for login/signup/request-form abuse control.
For heavy traffic, put a WAF/CDN rate limit in front of the service.
"""
import functools
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

from .audit import client_ip


def rate_limit(key_prefix: str, limit: int, window: int, methods=("POST",)):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if settings.RATELIMIT_ENABLED and request.method in methods:
                bucket = int(time.time() // window)
                key = f"rl:{key_prefix}:{client_ip(request)}:{bucket}"
                try:
                    cache.add(key, 0, timeout=window)
                    count = cache.incr(key)
                except Exception:  # cache unavailable: fail open rather than lock users out
                    count = 0
                if count > limit:
                    return HttpResponse(
                        "Too many attempts. Please wait a few minutes and try again.",
                        status=429,
                        content_type="text/plain",
                    )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
