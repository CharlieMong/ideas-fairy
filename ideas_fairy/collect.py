"""Collection orchestration: run sources, vault raw evidence, dedupe, score novelty."""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Any

from dateutil import parser as dateparser

from .config import Config
from .models import Item
from .sources import build_sources
from .sources.base import make_client
from .store import Store

log = logging.getLogger("ideas_fairy.collect")


@dataclass
class Scored:
    item: Item
    novelty: float
    is_new: bool


def _recency_factor(published_iso: str, lookback_days: int) -> float:
    if not published_iso:
        return 0.6  # unknown date: neutral-ish
    try:
        dt = dateparser.parse(published_iso)
    except (ValueError, TypeError, OverflowError):
        return 0.6
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    age_days = (_dt.datetime.now(_dt.timezone.utc) - dt).days
    if age_days <= 0:
        return 1.0
    if age_days >= lookback_days:
        return 0.0
    return 1.0 - (age_days / lookback_days)


def collect(cfg: Config, store: Store, run_id: int) -> tuple[list[Scored], dict[str, int]]:
    """Return scored, deduped items plus per-source counts. Side effect: vaults raw
    evidence and records items/seen in the store."""
    lookback = int(cfg.get("run", "lookback_days", default=21))
    min_novelty = float(cfg.get("run", "min_novelty", default=0.35))
    sources = build_sources(cfg)
    counts: dict[str, int] = {}
    scored: list[Scored] = []
    seen_this_run: set[str] = set()

    client = make_client(timeout_s=30.0)
    try:
        for src in sources:
            try:
                items, raw = src.collect(client)
            except Exception as exc:  # one source failing must not sink the run
                log.warning("source %s failed: %s", src.name, exc)
                counts[src.name] = 0
                continue
            counts[src.name] = len(items)
            for it in items:
                fp = it.fingerprint
                if fp in seen_this_run:
                    continue
                seen_this_run.add(fp)
                is_new = not store.is_seen(fp)
                # Vault raw evidence verbatim (chain of custody) for new items.
                if is_new and it.evidence_id in raw:
                    sha = store.write_raw(it.evidence_id, raw[it.evidence_id],
                                          {"source": it.source, "method": it.method,
                                           "retrieved_at": it.retrieved_at})
                    it.raw_sha256 = sha
                store.upsert_item(it, run_id)
                store.mark_seen(fp)
                rec = _recency_factor(it.published, lookback)
                novelty = (1.0 if is_new else 0.0) * (0.5 + 0.5 * rec)
                scored.append(Scored(item=it, novelty=round(novelty, 3), is_new=is_new))
        store.commit()
    finally:
        client.close()

    eligible = [s for s in scored if s.is_new and s.novelty >= min_novelty]
    eligible.sort(key=lambda s: s.novelty, reverse=True)
    return eligible, counts
