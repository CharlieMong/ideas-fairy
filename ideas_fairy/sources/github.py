"""GitHub recently-active offensive tooling via the REST search API (read-only)."""
from __future__ import annotations

from typing import Any

import httpx

from ..models import Item
from .base import Source

GH_SEARCH = "https://api.github.com/search/repositories"


class GithubSource(Source):
    source_kind = "code"

    def collect(self, client: httpx.Client) -> tuple[list[Item], dict[str, Any]]:
        token = self.cfg.env("IDEAS_FAIRY_GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        min_stars = int(self.scfg.get("min_stars", 10))
        queries = self.scfg.get("queries", ["topic:offensive-security"])
        per_q = max(1, self.limit // max(1, len(queries)))
        items: list[Item] = []
        raw: dict[str, Any] = {}
        for q in queries:
            full_q = f"{q} stars:>={min_stars} pushed:>=2026-01-01"
            params = {"q": full_q, "sort": "updated", "order": "desc",
                      "per_page": str(per_q)}
            resp = client.get(GH_SEARCH, params=params, headers=headers)
            if resp.status_code == 403:
                # rate limited / unauth — skip this query, keep what we have
                continue
            resp.raise_for_status()
            for repo in resp.json().get("items", []):
                it = Item(
                    source=self.name,
                    source_kind=self.source_kind,
                    title=repo.get("full_name", ""),
                    url=repo.get("html_url", ""),
                    summary=repo.get("description") or "",
                    published=self._iso(repo.get("pushed_at")),
                    method="github_search",
                    tags=repo.get("topics", []) + [f"stars:{repo.get('stargazers_count', 0)}"],
                    extra={"stars": repo.get("stargazers_count", 0),
                           "language": repo.get("language")},
                )
                raw[it.evidence_id] = {k: repo.get(k) for k in
                                       ("full_name", "html_url", "description",
                                        "pushed_at", "stargazers_count", "topics")}
                items.append(it)
        return items, raw
