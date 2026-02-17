import os
import sys
from datetime import datetime, timedelta, timezone

# Ensure repo root on path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from providers.miniflux import MinifluxProvider


class _DummyResp:
    def __init__(self, status_code=204, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _provider(feed_timeout_seconds=15):
    cfg = {
        "feed_timeout_seconds": feed_timeout_seconds,
        "providers": {
            "miniflux": {
                "url": "https://example.test",
                "api_key": "token",
            }
        },
    }
    return MinifluxProvider(cfg)


def test_miniflux_req_uses_configured_timeout_for_normal_endpoints(monkeypatch):
    p = _provider(feed_timeout_seconds=42)
    seen = {}

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        seen["timeout"] = timeout
        return _DummyResp(status_code=204)

    monkeypatch.setattr("providers.miniflux.requests.request", _fake_request)
    p._req("GET", "/v1/me")
    assert seen.get("timeout") == 42


def test_miniflux_refresh_uses_longer_timeout_floor(monkeypatch):
    p = _provider(feed_timeout_seconds=10)
    seen = {}

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        seen["timeout"] = timeout
        return _DummyResp(status_code=204)

    monkeypatch.setattr("providers.miniflux.requests.request", _fake_request)
    p._req("PUT", "/v1/feeds/123/refresh")
    assert seen.get("timeout") == 25


def test_miniflux_req_adds_revalidation_headers(monkeypatch):
    p = _provider(feed_timeout_seconds=10)
    seen = {}

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        seen["headers"] = dict(headers or {})
        return _DummyResp(status_code=204)

    monkeypatch.setattr("providers.miniflux.requests.request", _fake_request)
    p._req("GET", "/v1/me")

    headers = seen.get("headers") or {}
    assert "no-cache" in (headers.get("Cache-Control") or "").lower()
    assert (headers.get("Pragma") or "").lower() == "no-cache"
    assert headers.get("Expires") == "0"


def test_miniflux_refresh_force_refreshes_each_feed(monkeypatch):
    p = _provider(feed_timeout_seconds=10)
    calls = []
    now = datetime.now(timezone.utc)
    recent = now.isoformat()

    feeds_payload = [
        {"id": 1, "title": "Feed 1", "category": {"title": "Podcasts"}, "checked_at": recent, "parsing_error_count": 0},
        {"id": 2, "title": "Feed 2", "category": {"title": "News"}, "checked_at": recent, "parsing_error_count": 0},
    ]

    def _fake_req(method, endpoint, json=None, params=None):
        calls.append((method, endpoint))
        if endpoint == "/v1/feeds":
            return feeds_payload
        if endpoint == "/v1/feeds/counters":
            return {"unreads": {"1": 3, "2": 0}}
        return None

    monkeypatch.setattr(p, "_req", _fake_req)
    p.refresh(force=True)

    assert ("PUT", "/v1/feeds/1/refresh") in calls
    assert ("PUT", "/v1/feeds/2/refresh") in calls


def test_miniflux_refresh_non_force_only_retries_stale_or_error(monkeypatch):
    p = _provider(feed_timeout_seconds=10)
    calls = []
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(hours=4)).isoformat()
    recent = now.isoformat()

    feeds_payload = [
        {
            "id": 10,
            "title": "Stale feed",
            "category": {"title": "Podcasts"},
            "checked_at": stale,
            "parsing_error_count": 0,
        },
        {
            "id": 11,
            "title": "Error feed",
            "category": {"title": "Podcasts"},
            "checked_at": recent,
            "parsing_error_count": 1,
            "parsing_error_message": "parse failed",
        },
        {
            "id": 12,
            "title": "Healthy feed",
            "category": {"title": "Podcasts"},
            "checked_at": recent,
            "parsing_error_count": 0,
        },
    ]

    def _fake_req(method, endpoint, json=None, params=None):
        calls.append((method, endpoint))
        if endpoint == "/v1/feeds":
            return feeds_payload
        if endpoint == "/v1/feeds/counters":
            return {"unreads": {"10": 0, "11": 0, "12": 0}}
        return None

    monkeypatch.setattr(p, "_req", _fake_req)
    p.refresh(force=False)

    assert ("PUT", "/v1/feeds/10/refresh") in calls
    assert ("PUT", "/v1/feeds/11/refresh") in calls
    assert ("PUT", "/v1/feeds/12/refresh") not in calls
