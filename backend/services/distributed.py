"""可選 Redis 共享狀態；未配置時回傳 None，交由呼叫端使用本機 fallback。"""
import time
import threading
from collections import defaultdict, deque

from .. import settings

_client = None
_attempted = False
_fallback_lock = threading.Lock()
_fallback_windows = defaultdict(deque)


def client():
    global _client, _attempted
    if _attempted:
        return _client
    _attempted = True
    if not settings.REDIS_URL:
        return None
    try:
        import redis
        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=2)
        _client.ping()
    except Exception:
        _client = None
    return _client


def available() -> bool:
    return client() is not None


def allow_window(key: str, limit: int, window_seconds: int) -> bool:
    """固定時間窗限流；Redis 啟用時跨程序，共用失敗時回傳本機結果。"""
    r = client()
    now = time.time()
    if r:
        bucket = f"novel:rate:{key}"
        try:
            count = r.incr(bucket)
            if count == 1:
                r.expire(bucket, window_seconds)
            return count <= limit
        except Exception:
            pass
    with _fallback_lock:
        hits = _fallback_windows[key]
        while hits and now - hits[0] >= window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def login_blocked(key: str, max_failures: int, lockout_seconds: int) -> bool | None:
    r = client()
    if not r:
        return None
    try:
        return bool(r.exists(f"novel:login:lock:{key}"))
    except Exception:
        return None


def login_failed(key: str, max_failures: int, window_seconds: int, lockout_seconds: int) -> bool | None:
    r = client()
    if not r:
        return None
    try:
        count_key = f"novel:login:fail:{key}"
        count = r.incr(count_key)
        r.expire(count_key, window_seconds)
        if count >= max_failures:
            r.setex(f"novel:login:lock:{key}", lockout_seconds, "1")
            r.delete(count_key)
            return True
        return False
    except Exception:
        return None


def login_ok(key: str):
    r = client()
    if r:
        try:
            r.delete(f"novel:login:fail:{key}", f"novel:login:lock:{key}")
        except Exception:
            pass


def acquire(key: str, ttl_seconds: int = 3600) -> bool | None:
    r = client()
    if not r:
        return None
    try:
        return bool(r.set(f"novel:lock:{key}", "1", nx=True, ex=ttl_seconds))
    except Exception:
        return None


def release(key: str):
    r = client()
    if r:
        try:
            r.delete(f"novel:lock:{key}")
        except Exception:
            pass


def locked(key: str) -> bool | None:
    r = client()
    if not r:
        return None
    try:
        return bool(r.exists(f"novel:lock:{key}"))
    except Exception:
        return None
