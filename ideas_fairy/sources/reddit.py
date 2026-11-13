"""Reddit security subreddits (read-only).

Uses app-only OAuth (client_credentials) when a client id/secret are configured
in the environment — this is the reliable path, as Reddit now 403s most
unauthenticated datacenter requests. Falls back to the public JSON endpoint when
no credentials are present. Credentials are read from env and never logged.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

import httpx

from ..models import Item
from .base import Source

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_BASE = "https://oauth.reddit.com"
PUBLIC_BASE = "https://www.reddit.com"


class RedditSource(Source):
    source_kind = "community"

    def collect(self, client: httpx.Client) -> tuple[list[Item], dict[str, Any]]:
        subs = self.scfg.get("subreddits", ["netsec"])
        min_score = int(self.scfg.get("min_score", 25))
        per_sub = max(1, self.limit // max(1, len(subs)))
        ua = (self.cfg.env("IDEAS_FAIRY_REDDIT_USER_AGENT", "")
              or self.scfg.get("user_agent", "")
              or "ideas-fairy/0.1 (research scout)")

        client_id = self.cfg.env("IDEAS_FAIRY_REDDIT_CLIENT_ID", "").strip()
        secret = self.cfg.env("IDEAS_FAIRY_REDDIT_CLIENT_SECRET", "").strip()
        token = ""
        if client_id and secret:
            token = self._get_token(client, client_id, secret, ua)  # may be ""

        base = OAUTH_BASE if token else PUBLIC_BASE
        headers = {"User-Agent": ua}
        if token:
            headers["Authorization"] = f"bearer {token}"

        items: list[Item] = []
        raw: dict[str, Any] = {}
        for sub in subs:
            url = f"{base}/r/{sub}/new{'' if token else '.json'}"
            try:
                resp = client.get(url, params={"limit": str(per_sub)}, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue  # 403/blocked on one sub must not sink the run
            for child in resp.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                if d.get("score", 0) < min_score:
                    continue
                published = self._iso_from_epoch(d.get("created_utc"))
                if not self._within_lookback(published):
                    continue
                it = Item(
                    source=f"reddit:r/{sub}",
                    source_kind=self.source_kind,
                    title=d.get("title", "").strip(),
                    url="https://www.reddit.com" + d.get("permalink", ""),
                    summary=(d.get("selftext", "") or d.get("url", ""))[:600],
                    published=published,
                    method="reddit_oauth" if token else "reddit_json",
                    tags=[f"score:{d.get('score', 0)}", d.get("link_flair_text") or ""],
                    extra={"score": d.get("score", 0), "ext_url": d.get("url")},
                )
                raw[it.evidence_id] = {k: d.get(k) for k in
                                       ("title", "permalink", "selftext", "url",
                                        "score", "created_utc")}
                items.append(it)
        return items, raw

    def _get_token(self, client: httpx.Client, cid: str, secret: str,
                   ua: str) -> str:
        """Fetch an app-only bearer token. Returns '' on failure (caller falls back)."""
        try:
            resp = client.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(cid, secret),
                headers={"User-Agent": ua},
            )
            resp.raise_for_status()
            return resp.json().get("access_token", "")
        except httpx.HTTPError:
            return ""

    @staticmethod
    def _iso_from_epoch(epoch: Any) -> str:
        if not epoch:
            return ""
        try:
            return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)\
                .replace(microsecond=0).isoformat()
        except (ValueError, TypeError, OverflowError):
            return ""
