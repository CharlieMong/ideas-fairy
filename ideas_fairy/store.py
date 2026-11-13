"""SQLite evidence store with provenance, dedupe, novelty, and a raw-evidence vault.

Design: raw payloads are written to data/raw/<evidence_id>.json (untouched source
bytes + fetch metadata) so processed analysis never contaminates raw evidence.
The DB holds processed items, a seen-ledger for novelty, runs, and ranked ideas.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import Item, Idea, utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    evidence_id   TEXT PRIMARY KEY,
    fingerprint   TEXT UNIQUE,
    source        TEXT,
    source_kind   TEXT,
    title         TEXT,
    url           TEXT,
    summary       TEXT,
    published     TEXT,
    retrieved_at  TEXT,
    method        TEXT,
    tags          TEXT,
    extra         TEXT,
    raw_sha256    TEXT,
    first_seen_run INTEGER
);
CREATE TABLE IF NOT EXISTS seen (
    fingerprint  TEXT PRIMARY KEY,
    first_seen   TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT,
    finished_at  TEXT,
    collected    INTEGER DEFAULT 0,
    novel        INTEGER DEFAULT 0,
    delivered    INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'running',
    notes        TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ideas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER,
    rank          INTEGER,
    title         TEXT,
    rationale     TEXT,
    attack_surface TEXT,
    novelty_note  TEXT,
    first_steps   TEXT,
    evidence_ids  TEXT,
    frameworks    TEXT
);
"""


class Store:
    def __init__(self, db_path: Path, raw_dir: Path):
        self.db_path = Path(db_path)
        self.raw_dir = Path(raw_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- runs -----------------------------------------------------------------
    def start_run(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(started_at, status) VALUES (?, 'running')", (utcnow_iso(),)
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, collected: int, novel: int,
                   delivered: int, status: str, notes: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, collected=?, novel=?, delivered=?, "
            "status=?, notes=? WHERE id=?",
            (utcnow_iso(), collected, novel, delivered, status, notes, run_id),
        )
        self.conn.commit()

    # --- evidence -------------------------------------------------------------
    def write_raw(self, evidence_id: str, payload: Any, meta: dict[str, Any]) -> str:
        """Persist the raw payload verbatim with a content hash (chain of custody)."""
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        sha = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()
        record = {
            "evidence_id": evidence_id,
            "raw_sha256": sha,
            "fetched_meta": meta,
            "payload": payload,
        }
        out = self.raw_dir / f"{evidence_id}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        return sha

    def is_seen(self, fingerprint: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        return row is not None

    def mark_seen(self, fingerprint: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen(fingerprint, first_seen) VALUES (?, ?)",
            (fingerprint, utcnow_iso()),
        )

    def upsert_item(self, item: Item, run_id: int) -> None:
        row = item.to_row()
        row["first_seen_run"] = run_id
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        self.conn.execute(
            f"INSERT OR IGNORE INTO items({cols}) VALUES ({placeholders})", row
        )

    def commit(self) -> None:
        self.conn.commit()

    # --- ideas ----------------------------------------------------------------
    def latest_run_with_ideas(self) -> int | None:
        row = self.conn.execute(
            "SELECT run_id FROM ideas ORDER BY id DESC LIMIT 1").fetchone()
        return int(row["run_id"]) if row else None

    def load_ideas(self, run_id: int) -> list[Idea]:
        rows = self.conn.execute(
            "SELECT rank, title, rationale, attack_surface, novelty_note, "
            "first_steps, evidence_ids, frameworks FROM ideas WHERE run_id=? "
            "ORDER BY rank", (run_id,)).fetchall()
        out: list[Idea] = []
        for r in rows:
            out.append(Idea(
                rank=r["rank"], title=r["title"], rationale=r["rationale"],
                attack_surface=r["attack_surface"], novelty_note=r["novelty_note"],
                first_steps=r["first_steps"],
                evidence_ids=json.loads(r["evidence_ids"] or "[]"),
                frameworks=json.loads(r["frameworks"] or "[]"),
            ))
        return out

    def save_ideas(self, run_id: int, ideas: Iterable[Idea]) -> None:
        for idea in ideas:
            r = idea.to_row()
            r["run_id"] = run_id
            self.conn.execute(
                "INSERT INTO ideas(run_id, rank, title, rationale, attack_surface, "
                "novelty_note, first_steps, evidence_ids, frameworks) VALUES "
                "(:run_id, :rank, :title, :rationale, :attack_surface, :novelty_note, "
                ":first_steps, :evidence_ids, :frameworks)", r
            )
        self.conn.commit()
