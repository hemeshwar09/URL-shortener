"""Sliding-window-log rate limiter (Redis sorted set, or in-memory fallback)."""
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .cache import cache


class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)

    def reset(self):
        self._hits.clear()

    def allow(self, key: str) -> bool:
        now = time.time()
        if cache.redis:
            try:
                rkey = f"rl:{key}"
                pipe = cache.redis.pipeline()
                pipe.zremrangebyscore(rkey, 0, now - self.window)
                pipe.zcard(rkey)
                _, count = pipe.execute()
                if count >= self.limit:
                    return False
                pipe = cache.redis.pipeline()
                pipe.zadd(rkey, {uuid.uuid4().hex: now})
                pipe.expire(rkey, self.window)
                pipe.execute()
                return True
            except Exception:
                return True  # fail open: availability over strictness
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


limiter = RateLimiter(int(os.getenv("RATE_LIMIT", "30")), int(os.getenv("RATE_WINDOW", "60")))


def rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    if not limiter.allow(ip):
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(limiter.window)})
