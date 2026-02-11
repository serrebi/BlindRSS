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

