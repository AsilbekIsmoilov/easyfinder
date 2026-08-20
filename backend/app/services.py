from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque

from .config import settings

try:
    from redis import Redis
except ImportError:  # optional fallback
    Redis = None

_redis = None
_memory_cache: dict[str, tuple[float, object]] = {}
_rate_events: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def redis_client():
    global _redis
    if _redis is False:
        return None
    if _redis is None and Redis and settings.redis_url:
        try:
            client = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=0.3, socket_connect_timeout=0.3)
            client.ping()
            _redis = client
        except Exception:
            _redis = False
    return _redis or None


def cache_get(key: str):
    client = redis_client()
    if client:
        try:
            value = client.get(key)
            return json.loads(value) if value else None
        except Exception:
            pass
    value = _memory_cache.get(key)
    if value and value[0] > time.monotonic():
        return value[1]
    _memory_cache.pop(key, None)
    return None


def cache_set(key: str, value, ttl: int = 60) -> None:
    client = redis_client()
    if client:
        try:
            client.setex(key, ttl, json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            return
        except Exception:
            pass
    _memory_cache[key] = (time.monotonic() + ttl, value)


def cache_delete_pattern(pattern: str) -> None:
    client = redis_client()
    if client:
        try:
            keys = list(client.scan_iter(match=pattern, count=200))
            if keys:
                client.delete(*keys)
        except Exception:
            pass
    prefix = pattern.rstrip("*")
    for key in list(_memory_cache):
        if key.startswith(prefix):
            _memory_cache.pop(key, None)


def rate_allowed(key: str, limit: int, window_seconds: int) -> bool:
    client = redis_client()
    if client:
        try:
            redis_key = f"rate:{key}:{int(time.time() // window_seconds)}"
            count = client.incr(redis_key)
            if count == 1:
                client.expire(redis_key, window_seconds + 1)
            return count <= limit
        except Exception:
            pass
    now = time.monotonic()
    with _lock:
        events = _rate_events[key]
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True