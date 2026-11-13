"""Offline tests for the Reddit source: OAuth path and public fallback."""
from __future__ import annotations

import time

from ideas_fairy.config import Config
from ideas_fairy.sources.reddit import RedditSource


def _listing(title="PoC for new RCE", score=120):
    return {"data": {"children": [
        {"data": {"title": title, "permalink": "/r/netsec/x", "selftext": "body",
                  "url": "https://e.test", "score": score,
                  "created_utc": time.time()}}
    ]}}


class FakeClient:
    def __init__(self, listing, token=None):
        self.listing = listing
        self.token = token
        self.calls = []

    def post(self, url, data=None, auth=None, headers=None, **kw):
        self.calls.append(("POST", url, {"auth": auth, "headers": headers}))
        return _Resp({"access_token": self.token} if self.token else {})

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, {"headers": headers}))
        return _Resp(self.listing)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def _src(monkeypatch, creds):
    for k in ("IDEAS_FAIRY_REDDIT_CLIENT_ID", "IDEAS_FAIRY_REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    if creds:
        monkeypatch.setenv("IDEAS_FAIRY_REDDIT_CLIENT_ID", "cid")
        monkeypatch.setenv("IDEAS_FAIRY_REDDIT_CLIENT_SECRET", "sec")
    cfg = Config(raw={"run": {"per_source_limit": 10, "lookback_days": 3650}}, root=".")
    return RedditSource("reddit", cfg,
                        {"enabled": True, "subreddits": ["netsec"], "min_score": 25})


def test_oauth_path(monkeypatch):
    client = FakeClient(_listing(), token="tok-123")
    items, raw = _src(monkeypatch, creds=True).collect(client)
    # token fetched with basic auth, then oauth.reddit.com hit with bearer
    assert any(c[0] == "POST" and "access_token" in c[1] for c in client.calls)
    get = next(c for c in client.calls if c[0] == "GET")
    assert get[1].startswith("https://oauth.reddit.com/r/netsec/new")
    assert get[2]["headers"]["Authorization"] == "bearer tok-123"
    assert len(items) == 1 and items[0].method == "reddit_oauth"


def test_public_fallback(monkeypatch):
    client = FakeClient(_listing())
    items, _ = _src(monkeypatch, creds=False).collect(client)
    get = next(c for c in client.calls if c[0] == "GET")
    assert get[1].startswith("https://www.reddit.com/r/netsec/new.json")
    assert "Authorization" not in get[2]["headers"]
    assert items[0].method == "reddit_json"


def test_token_failure_falls_back(monkeypatch):
    # creds present but token endpoint returns no token -> public endpoint
    client = FakeClient(_listing(), token="")  # empty access_token
    items, _ = _src(monkeypatch, creds=True).collect(client)
    get = next(c for c in client.calls if c[0] == "GET")
    assert get[1].startswith("https://www.reddit.com/r/netsec/new.json")
    assert items[0].method == "reddit_json"


def test_min_score_filter(monkeypatch):
    client = FakeClient(_listing(score=5))  # below default 25
    items, _ = _src(monkeypatch, creds=False).collect(client)
    assert items == []
