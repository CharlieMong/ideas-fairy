"""General web-search source (opt-in). Provider-pluggable: tavily | brave.

Enabled only when sources.websearch.enabled is true AND a key is present in
IDEAS_FAIRY_SEARCH_API_KEY. Results carry full provenance like any other source.
Bounded by per_source_limit / lookback_days; marked as a partial source in config.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models import Item
from .base import Source

log = logging.getLogger("ideas_fairy.sources.websearch")

TAVILY_URL = "https://api.tavily.com/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class WebSearchSource(Source):
    source_kind = "websearch"

    def collect(self, client: httpx.Client) -> tuple[list[Item], dict[str, Any]]:
        key = self.cfg.env("IDEAS_FAIRY_SEARCH_API_KEY", "").strip()
        provider = (self.scfg.get("provider", "none") or "none").lower()
        queries = self.scfg.get("queries", [])
        if not key or provider in ("", "none"):
            log.info("websearch disabled: no key or provider=none")
            return [], {}
        if not queries:
            return [], {}

        per_q = max(1, self.limit // max(1, len(queries)))
        dispatch = {"tavily": self._tavily, "brave": self._brave}
        fn = dispatch.get(provider)
        if fn is None:
            log.warning("unknown websearch provider %r; skipping", provider)
            return [], {}

        items: list[Item] = []
        raw: dict[str, Any] = {}
        for q in queries:
            try:
                q_items, q_raw = fn(client, key, q, per_q)
            except httpx.HTTPError as exc:
                log.warning("websearch(%s) query %r failed: %s",
                            provider, q, type(exc).__name__)
                continue
            items.extend(q_items)
            raw.update(q_raw)
        return items, raw

    # --- providers ------------------------------------------------------------
    def _tavily(self, client: httpx.Client, key: str, query: str,
                n: int) -> tuple[list[Item], dict[str, Any]]:
        payload = {
            "api_key": key,
            "query": query,
            "max_results": n,
            "search_depth": self.scfg.get("search_depth", "basic"),
            "topic": self.scfg.get("topic", "general"),
            "days": self.lookback_days,
            "include_answer": False,
            "include_raw_content": False,
        }
        resp = client.post(TAVILY_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        items, raw = [], {}
        for r in data.get("results", []):
            published = self._iso(r.get("published_date", ""))
            if not self._within_lookback(published):
                continue
            it = Item(
                source="websearch:tavily",
                source_kind=self.source_kind,
                title=(r.get("title") or "").strip(),
                url=r.get("url", ""),
                summary=(r.get("content") or "")[:800],
                published=published,
                method="tavily_search",
                tags=[f"query:{query}"],
                extra={"score": r.get("score")},
            )
            raw[it.evidence_id] = r
            items.append(it)
        return items, raw

    def _brave(self, client: httpx.Client, key: str, query: str,
               n: int) -> tuple[list[Item], dict[str, Any]]:
        headers = {"X-Subscription-Token": key, "Accept": "application/json"}
        params = {"q": query, "count": min(n, 20)}
        freshness = self.scfg.get("freshness", "")  # pd|pw|pm|py or blank
        if freshness:
            params["freshness"] = freshness
        resp = client.get(BRAVE_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items, raw = [], {}
        for r in (data.get("web", {}) or {}).get("results", []):
            published = self._iso(r.get("page_age") or r.get("age") or "")
            if not self._within_lookback(published):
                continue
            it = Item(
                source="websearch:brave",
                source_kind=self.source_kind,
                title=(r.get("title") or "").strip(),
                url=r.get("url", ""),
                summary=(r.get("description") or "")[:800],
                published=published,
                method="brave_search",
                tags=[f"query:{query}"],
            )
            raw[it.evidence_id] = r
            items.append(it)
        return items, raw
