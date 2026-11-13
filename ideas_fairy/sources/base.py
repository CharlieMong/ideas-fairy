"""Source base class + shared HTTP helper. All sources are strictly read-only."""
from __future__ import annotations

import datetime as _dt
from typing import Any

import httpx
from dateutil import parser as dateparser

from ..config import Config
from ..models import Item, utcnow_iso

USER_AGENT = "ideas-fairy/0.1 (+offensive-security research scout; contact: OverFlowBuff3r)"


class Source:
    """A read-only collector. Subclasses implement collect()."""

    source_kind = "generic"

    def __init__(self, name: str, cfg: Config, scfg: dict[str, Any]):
        self.name = name
        self.cfg = cfg
        self.scfg = scfg
        self.limit = int(cfg.get("run", "per_source_limit", default=40))
        self.lookback_days = int(cfg.get("run", "lookback_days", default=21))

    # Subclasses return (items, raw_payloads_by_evidence_id).
    def collect(self, client: httpx.Client) -> tuple[list[Item], dict[str, Any]]:
        raise NotImplementedError

    # --- helpers --------------------------------------------------------------
    def _within_lookback(self, published_iso: str) -> bool:
        if not published_iso:
            return True  # unknown date: keep, let novelty/LLM decide
        try:
            dt = dateparser.parse(published_iso)
        except (ValueError, TypeError, OverflowError):
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=self.lookback_days)
        return dt >= cutoff

    @staticmethod
    def _iso(value: Any) -> str:
        if not value:
            return ""
        try:
            dt = dateparser.parse(str(value))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return dt.replace(microsecond=0).isoformat()
        except (ValueError, TypeError, OverflowError):
            return ""


def make_client(timeout_s: float = 30.0, token: str = "") -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, */*"}
    return httpx.Client(headers=headers, timeout=timeout_s, follow_redirects=True)
