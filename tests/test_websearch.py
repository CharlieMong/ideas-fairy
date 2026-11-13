"""Offline tests for the web-search source parsing (no network)."""
from __future__ import annotations

from ideas_fairy.config import Config
from ideas_fairy.sources.websearch import WebSearchSource


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class FakeClient:
    """Captures requests and returns canned provider payloads."""
    def __init__(self, payload):
        self.payload = payload
        self.last = {}

    def post(self, url, json=None, **kw):
        self.last = {"url": url, "json": json}
        return FakeResp(self.payload)

    def get(self, url, params=None, headers=None, **kw):
        self.last = {"url": url, "params": params, "headers": headers}
        return FakeResp(self.payload)


def _src(provider, monkeypatch):
    monkeypatch.setenv("IDEAS_FAIRY_SEARCH_API_KEY", "test-key")
    cfg = Config(raw={"run": {"per_source_limit": 10, "lookback_days": 3650}}, root=".")
    scfg = {"enabled": True, "provider": provider, "queries": ["novel exploit primitive"]}
    return WebSearchSource(name="websearch", cfg=cfg, scfg=scfg)


def test_tavily_parsing(monkeypatch):
    payload = {"results": [
        {"title": "New heap primitive", "url": "https://x.test/1",
         "content": "a technique", "published_date": "2026-11-01", "score": 0.9},
        {"title": "Evasion trick", "url": "https://x.test/2",
         "content": "b technique", "published_date": "2026-10-20", "score": 0.7},
    ]}
    client = FakeClient(payload)
    items, raw = _src("tavily", monkeypatch).collect(client)
    assert client.last["url"].endswith("/search")
    assert client.last["json"]["api_key"] == "test-key"   # key in body, not logged
    assert len(items) == 2
    assert items[0].source == "websearch:tavily"
    assert items[0].method == "tavily_search"
    assert items[0].evidence_id in raw


def test_brave_parsing(monkeypatch):
    payload = {"web": {"results": [
        {"title": "C2 over QUIC", "url": "https://x.test/3",
         "description": "desc", "page_age": "2026-11-05"},
    ]}}
    client = FakeClient(payload)
    items, raw = _src("brave", monkeypatch).collect(client)
    assert client.last["headers"]["X-Subscription-Token"] == "test-key"
    assert client.last["params"]["q"] == "novel exploit primitive"
    assert len(items) == 1
    assert items[0].source == "websearch:brave"
    assert items[0].method == "brave_search"


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("IDEAS_FAIRY_SEARCH_API_KEY", raising=False)
    cfg = Config(raw={"run": {"per_source_limit": 10, "lookback_days": 30}}, root=".")
    src = WebSearchSource("websearch", cfg,
                          {"enabled": True, "provider": "tavily", "queries": ["x"]})
    assert src.collect(FakeClient({})) == ([], {})


def test_unknown_provider(monkeypatch):
    monkeypatch.setenv("IDEAS_FAIRY_SEARCH_API_KEY", "k")
    cfg = Config(raw={"run": {"per_source_limit": 10, "lookback_days": 30}}, root=".")
    src = WebSearchSource("websearch", cfg,
                          {"enabled": True, "provider": "duckduckgo", "queries": ["x"]})
    assert src.collect(FakeClient({})) == ([], {})
