"""Core data models. Raw evidence and processed analysis are kept distinct."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


@dataclass
class Item:
    """A single collected artefact with full provenance.

    Provenance fields (source, retrieved_at, method, evidence_id, raw_sha256)
    support chain-of-custody per the operator's evidence standard.
    """
    source: str                 # logical source name, e.g. "arxiv"
    source_kind: str            # "research" | "code" | "blog" | "community" | "advisory"
    title: str
    url: str
    summary: str = ""
    published: str = ""         # ISO8601 if known
    retrieved_at: str = field(default_factory=utcnow_iso)
    method: str = ""            # how it was fetched, e.g. "arxiv_api"
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    raw_sha256: str = ""        # hash of the raw payload this item derives from

    @property
    def fingerprint(self) -> str:
        """Identity for dedupe: source + canonical url (or title fallback)."""
        key = f"{self.source}|{(self.url or self.title).strip().lower()}"
        return _sha256(key)

    @property
    def evidence_id(self) -> str:
        """Stable, human-greppable evidence ID."""
        return "ev_" + self.fingerprint[:16]

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["tags"] = json.dumps(self.tags, ensure_ascii=False)
        d["extra"] = json.dumps(self.extra, ensure_ascii=False)
        d["fingerprint"] = self.fingerprint
        d["evidence_id"] = self.evidence_id
        return d

    def context_line(self) -> str:
        """Compact representation handed to the LLM (kept small on purpose)."""
        pub = f" ({self.published[:10]})" if self.published else ""
        summ = (self.summary or "").strip().replace("\n", " ")
        if len(summ) > 320:
            summ = summ[:317] + "..."
        return f"[{self.source_kind}:{self.source}] {self.title}{pub} — {summ} <{self.evidence_id}>"


@dataclass
class Idea:
    """A processed, ranked research idea produced by the model."""
    rank: int
    title: str
    rationale: str
    attack_surface: str = ""       # where it applies
    novelty_note: str = ""         # why it is a gap / under-explored
    first_steps: str = ""          # safe, non-destructive validation steps
    evidence_ids: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)  # e.g. MITRE ATT&CK refs

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_ids"] = json.dumps(self.evidence_ids, ensure_ascii=False)
        d["frameworks"] = json.dumps(self.frameworks, ensure_ascii=False)
        return d
