"""arXiv collector via the public Atom export API (read-only)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx

from ..models import Item
from .base import Source

ARXIV_API = "http://export.arxiv.org/api/query"


class ArxivSource(Source):
    source_kind = "research"

    def collect(self, client: httpx.Client) -> tuple[list[Item], dict[str, Any]]:
        cats = self.scfg.get("categories", ["cs.CR"])
        terms = self.scfg.get("query_terms", [])
        cat_q = " OR ".join(f"cat:{c}" for c in cats)
        term_q = " OR ".join(f'all:"{t}"' for t in terms) if terms else ""
        search = f"({cat_q})" + (f" AND ({term_q})" if term_q else "")
        params = {
            "search_query": search,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": str(self.limit),
        }
        items: list[Item] = []
        raw: dict[str, Any] = {}
        resp = client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for e in feed.entries[: self.limit]:
            published = self._iso(getattr(e, "published", ""))
            if not self._within_lookback(published):
                continue
            it = Item(
                source=self.name,
                source_kind=self.source_kind,
                title=getattr(e, "title", "").strip(),
                url=getattr(e, "link", ""),
                summary=getattr(e, "summary", "").strip(),
                published=published,
                method="arxiv_api",
                tags=[t.get("term", "") for t in getattr(e, "tags", [])],
            )
            raw[it.evidence_id] = {k: getattr(e, k, None)
                                   for k in ("title", "summary", "link", "published", "id")}
            items.append(it)
        return items, raw
