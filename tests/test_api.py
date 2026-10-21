import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pytest
from fastapi.testclient import TestClient

from app import db
from app.cache import cache
from app.ids import decode, encode
from app.main import app
from app.ratelimit import limiter


@pytest.fixture()
def client():
    limiter.reset()
    limiter.limit = 100
    with TestClient(app) as c:
        yield c


def test_base62_roundtrip():
    for n in (1, 61, 62, 12345, 10**9):
        assert decode(encode(n)) == n


def test_shorten_redirect_and_stats(client):
    r = client.post("/api/shorten", json={"url": "https://example.com/page"})
    assert r.status_code == 201
    code = r.json()["code"]
    red = client.get(f"/{code}", follow_redirects=False)
    assert red.status_code == 302
    assert red.headers["location"] == "https://example.com/page"
    assert client.get(f"/api/stats/{code}").json()["total_clicks"] == 1


def test_custom_alias_conflict(client):
    assert client.post("/api/shorten", json={"url": "https://a.com", "alias": "mylink"}).status_code == 201
    assert client.post("/api/shorten", json={"url": "https://b.com", "alias": "mylink"}).status_code == 409


def test_invalid_url_rejected(client):
    assert client.post("/api/shorten", json={"url": "not-a-url"}).status_code == 422


def test_unknown_code_404(client):
    assert client.get("/doesnotexist", follow_redirects=False).status_code == 404


def test_expired_link_410(client):
    code = client.post("/api/shorten", json={"url": "https://a.com", "alias": "soon"}).json()["code"]
    with db.get_conn() as c:
        c.execute("UPDATE urls SET expires_at='2000-01-01T00:00:00+00:00' WHERE code=?", (code,))
    cache.delete(f"url:{code}")
    assert client.get(f"/{code}", follow_redirects=False).status_code == 410


def test_cache_hit_on_second_redirect(client):
    code = client.post("/api/shorten", json={"url": "https://cached.com"}).json()["code"]
    client.get(f"/{code}", follow_redirects=False)
    before = client.get("/api/cache-stats").json()["hits"]
    client.get(f"/{code}", follow_redirects=False)
    assert client.get("/api/cache-stats").json()["hits"] == before + 1


def test_rate_limit(client):
    limiter.limit = 3
    codes = [client.post("/api/shorten", json={"url": "https://x.com"}).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]


def test_home_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Snip" in r.text


def test_reserved_alias_rejected(client):
    r = client.post("/api/shorten", json={"url": "https://a.com", "alias": "docs"})
    assert r.status_code == 400


@pytest.mark.parametrize("bad", ["http://localhost:8000/x", "http://127.0.0.1/x",
                                 "http://192.168.1.5/x", "http://testserver/loop"])
def test_internal_and_self_targets_blocked(client, bad):
    assert client.post("/api/shorten", json={"url": bad}).status_code == 400


def test_delete_requires_token_and_invalidates_cache(client):
    d = client.post("/api/shorten", json={"url": "https://del.com"}).json()
    code = d["code"]
    client.get(f"/{code}", follow_redirects=False)  # warms the cache
    assert client.delete(f"/api/links/{code}", headers={"X-Token": "wrong"}).status_code == 403
    assert client.delete(f"/api/links/{code}", headers={"X-Token": d["delete_token"]}).status_code == 204
    assert client.get(f"/{code}", follow_redirects=False).status_code == 404
    assert client.delete(f"/api/links/{code}", headers={"X-Token": d["delete_token"]}).status_code == 404


def test_analytics_breakdown(client):
    code = client.post("/api/shorten", json={"url": "https://stats.com"}).json()["code"]
    ua = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    client.get(f"/{code}", follow_redirects=False,
               headers={"user-agent": ua, "referer": "https://news.ycombinator.com/item?id=1"})
    s = client.get(f"/api/stats/{code}").json()
    assert len(s["by_day"]) == 7 and s["by_day"][-1]["clicks"] == 1
    assert s["browsers"][0] == {"name": "Chrome", "count": 1}
    assert s["devices"][0]["name"] == "Desktop"
    assert s["referrers"][0]["name"] == "news.ycombinator.com"
