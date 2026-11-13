"""Security research blogs / advisories via RSS/Atom (read-only)."""
from __future__ import annotations

from typing import Any

import feedparser
import httpx

from ..models import Item
from .base import Source


class RssSource(Source):
    source_kind = "blog"

    def collect(self, client: httpx.Client) -> tuple[list[Item], dict[str, Any]]:
        feeds = self.scfg.get("feeds", [])
        per_feed = max(1, self.limit // max(1, len(feeds))) if feeds else self.limit
        items: list[Item] = []
        raw: dict[str, Any] = {}
        for feed_url in feeds:
            try:
                resp = client.get(feed_url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue  # one bad feed must not sink the run
            parsed = feedparser.parse(resp.text)
            for e in parsed.entries[:per_feed]:
                published = self._iso(getattr(e, "published", "")
                                      or getattr(e, "updated", ""))
                if not self._within_lookback(published):
                    continue
                summary = getattr(e, "summary", "")
                it = Item(
                    source=f"rss:{parsed.feed.get('title', feed_url)[:40]}",
                    source_kind=self.source_kind,
                    title=getattr(e, "title", "").strip(),
                    url=getattr(e, "link", ""),
                    summary=summary.strip(),
                    published=published,
                    method="rss",
                )
                raw[it.evidence_id] = {k: getattr(e, k, None)
                                       for k in ("title", "link", "summary", "published")}
                items.append(it)
        return items, raw
