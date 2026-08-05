"""Small distributed lock helpers with atomic owner-checked release.

Production uses Redis as the single source of truth. When a Redis URL is
configured, connectivity failures fail closed instead of silently creating a
second process-local lock namespace that other workers cannot see. Django's
configured cache is used only in development/tests where no Redis URL exists.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)
_LOCAL_LOCK = threading.Lock()
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _raw_lock_key(key: str) -> str:
    return f"djopenkb:distributed-lock:{key}"


def _redis_is_configured() -> bool:
    return bool(str(getattr(settings, "REDIS_URL", "") or "").strip())


@lru_cache(maxsize=1)
def _redis_client():
    redis_url = str(getattr(settings, "REDIS_URL", "") or "").strip()
    if not redis_url:
        return None
    try:
        import redis

        return redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    except Exception:
        logger.exception("Unable to initialize the Redis distributed-lock client")
        return None


def acquire_distributed_lock(key: str, token: str, timeout_seconds: int) -> bool:
    """Acquire one lock atomically.

    Redis failures fail closed whenever Redis is configured. Falling back to a
    local cache in that situation would create a split-brain lock that another
    web/worker process cannot observe.
    """
    timeout_seconds = max(1, int(timeout_seconds))
    client = _redis_client()
    if client is not None:
        try:
            return bool(
                client.set(
                    _raw_lock_key(key),
                    token,
                    nx=True,
                    ex=timeout_seconds,
                )
            )
        except Exception:
            logger.exception("Redis lock acquisition failed")
            return False

    if _redis_is_configured():
        logger.error("Redis is configured but no distributed-lock client is available")
        return False

    return bool(cache.add(_raw_lock_key(key), token, timeout=timeout_seconds))


def release_distributed_lock(key: str, token: str | None) -> bool:
    """Delete a lock only when it is still owned by ``token``.

    Redis executes the comparison and deletion in one Lua operation. The local
    fallback is protected by a process lock and is used only when Redis is not
    configured (for example, an isolated unit-test environment).
    """
    if not token:
        return False

    client = _redis_client()
    if client is not None:
        try:
            return bool(client.eval(_RELEASE_SCRIPT, 1, _raw_lock_key(key), token))
        except Exception:
            logger.exception("Redis lock release failed")
            return False

    if _redis_is_configured():
        logger.error("Redis is configured but no distributed-lock client is available")
        return False

    with _LOCAL_LOCK:
        cache_key = _raw_lock_key(key)
        if cache.get(cache_key) != token:
            return False
        cache.delete(cache_key)
        return True
