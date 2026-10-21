"""Read-through cache. Uses Redis when REDIS_URL is set and reachable,
otherwise falls back to a small in-process TTL cache (handy for tests/dev)."""
import os
import time

TTL = int(os.getenv("CACHE_TTL", "3600"))


class Cache:
    def __init__(self):
        self._mem: dict[str, tuple[float, str]] = {}
        self._redis = None
        url = os.getenv("REDIS_URL")
        if url:
            try:
                import redis

                client = redis.Redis.from_url(url, decode_responses=True)
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None  # degrade gracefully, never take the service down
        self.hits = 0
        self.misses = 0

    @property
    def redis(self):
        return self._redis

    def get(self, key: str) -> str | None:
        val = None
        if self._redis:
            try:
                val = self._redis.get(key)
            except Exception:
                val = None
        else:
            item = self._mem.get(key)
            if item and item[0] > time.time():
                val = item[1]
            elif item:
                del self._mem[key]
        self.hits += val is not None
        self.misses += val is None
        return val

    def set(self, key: str, value: str, ttl: int = TTL) -> None:
        if self._redis:
            try:
                self._redis.setex(key, ttl, value)
            except Exception:
                pass
        else:
            self._mem[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        if self._redis:
            try:
                self._redis.delete(key)
            except Exception:
                pass
        self._mem.pop(key, None)


cache = Cache()
