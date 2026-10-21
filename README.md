# Snip: URL Shortener

A URL shortener with Redis caching, sliding-window rate limiting, link expiry, and click analytics.
Built with FastAPI + SQLite (swappable for Postgres), containerized with Docker, tested in CI.

## Architecture

```
Client -> FastAPI -> Redis (cache + rate-limit counters)
                  -> SQLite/Postgres (source of truth: urls, clicks)
```

**Redirect path (hot):** cache lookup -> on miss, DB lookup + cache fill -> 302.
Click recording runs as a background task so analytics never add latency to redirects.

## Design decisions & trade-offs

| Decision | Why | Trade-off |
|---|---|---|
| Base62 of auto-increment ID | Zero collisions, no retry loop | Codes are guessable/sequential; add an ID scrambler or random IDs if that matters |
| Read-through Redis cache | Reads dominate writes (~100:1) | Stale data possible; TTL is capped at link expiry |
| 302 (not 301) | Browsers don't cache it, so every click is counted | More server load than 301 |
| Sliding-window-log limiter | Exact, no burst-at-boundary problem of fixed windows | O(limit) memory per client; token bucket is cheaper |
| Fail-open on Redis errors | Availability over strictness | Brief window with no rate limiting |
| Redis optional (in-memory fallback) | Runs anywhere, easy tests | In-memory cache isn't shared across workers |

## Run

```bash
docker compose up --build      # API on http://localhost:8000 (docs at /docs)
# or locally
pip install -r requirements.txt && uvicorn app.main:app --reload
pytest -q
```

## API

```bash
curl -X POST localhost:8000/api/shorten -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","alias":"demo","ttl_seconds":3600}'
curl -i localhost:8000/demo          # 302 redirect
curl localhost:8000/api/stats/demo   # total + last-24h clicks
```

## Load test (fill in your own numbers)

```bash
k6 run loadtest/k6.js                                   # without Redis
REDIS_URL=redis://localhost:6379/0 uvicorn app.main:app # then re-run k6
```

| Setup | RPS | p99 latency |
|---|---|---|
| No cache | _measure_ | _measure_ |
| Redis cache | _measure_ | _measure_ |

## Scaling beyond this

- Replace SQLite with Postgres, add read replicas
- Batch click writes via a queue (SQS/Kafka) instead of per-request inserts
- Shard by code hash; run multiple API instances behind a load balancer
- Add a cleanup job for expired links

## Deploy (Render, free tier)

1. Push this repo to GitHub.
2. Render dashboard -> **New -> Web Service** -> connect the repo -> Runtime **Docker** -> Instance type **Free**.
3. (Optional) **New -> Key Value** (free) and set the web service env var `REDIS_URL` to its *Internal* URL.
4. Deploy. Your public URL looks like `https://snip-xxxx.onrender.com`.

Note: the free tier sleeps after ~15 min idle (first request is slow) and its disk is ephemeral,
so SQLite data resets on redeploy. For persistence, point the DB layer at a free Postgres (Neon/Supabase/Render).
