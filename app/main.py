import ipaddress
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, HttpUrl

from . import db
from .cache import cache
from .ratelimit import rate_limit


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Snip - URL Shortener", lifespan=lifespan)

INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"
# Aliases that would collide with real routes or look official
RESERVED = {"api", "docs", "redoc", "health", "static", "admin", "robots", "favicon", "login"}


class ShortenRequest(BaseModel):
    url: HttpUrl
    alias: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{3,32}$")
    ttl_seconds: int | None = Field(None, gt=0, le=60 * 60 * 24 * 365)


def validate_target(url: str, own_host: str) -> None:
    """Block redirect loops and links that point at internal/private addresses."""
    host = (urlparse(url).hostname or "").lower()
    if host == own_host.lower():
        raise HTTPException(400, "You can’t shorten a link to this site itself")
    if host == "localhost" or host.endswith((".local", ".internal")):
        raise HTTPException(400, "Local addresses aren’t allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a normal hostname
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise HTTPException(400, "Private or reserved IP addresses aren’t allowed")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(INDEX)


@app.get("/health")
def health():
    return {"status": "ok", "cache": "redis" if cache.redis else "memory"}


@app.post("/api/shorten", status_code=201, dependencies=[Depends(rate_limit)])
def shorten(body: ShortenRequest, request: Request):
    validate_target(str(body.url), request.url.hostname or "")
    if body.alias and body.alias.lower() in RESERVED:
        raise HTTPException(400, "That alias is reserved")
    try:
        row = db.create_url(str(body.url), body.alias, body.ttl_seconds)
    except db.AliasTaken:
        raise HTTPException(409, "Alias already in use")
    row["short_url"] = f"{request.base_url}{row['code']}"
    return row


@app.get("/api/stats/{code}")
def stats(code: str):
    s = db.get_stats(code)
    if not s:
        raise HTTPException(404, "Unknown code")
    return s


@app.delete("/api/links/{code}", status_code=204, dependencies=[Depends(rate_limit)])
def delete_link(code: str, x_token: str | None = Header(None)):
    result = db.delete_url(code, x_token or "")
    if result == "notfound":
        raise HTTPException(404, "Unknown code")
    if result == "forbidden":
        raise HTTPException(403, "Invalid delete token")
    cache.delete(f"url:{code}")  # invalidate so the link stops working immediately
    return Response(status_code=204)


@app.get("/api/cache-stats")
def cache_stats():
    total = cache.hits + cache.misses
    return {"hits": cache.hits, "misses": cache.misses,
            "hit_ratio": round(cache.hits / total, 3) if total else 0.0}


@app.get("/{code}")
def redirect(code: str, request: Request, background: BackgroundTasks):
    long_url = cache.get(f"url:{code}")  # 1) cache first
    if long_url is None:
        row = db.get_url(code)  # 2) fall back to DB
        if not row:
            raise HTTPException(404, "Unknown code")
        if db.is_expired(row):
            raise HTTPException(410, "Link expired")
        long_url = row["long_url"]
        ttl = 3600
        if row["expires_at"]:  # never cache past the link's own expiry
            remaining = (datetime.fromisoformat(row["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
            ttl = max(1, min(ttl, int(remaining)))
        cache.set(f"url:{code}", long_url, ttl)
    # analytics off the hot path
    background.add_task(db.record_click, code, request.headers.get("user-agent"),
                        request.headers.get("referer"))
    return RedirectResponse(long_url, status_code=302)
