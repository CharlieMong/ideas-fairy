"""Discord webhook delivery. The webhook URL is a secret: never logged or printed.

Discord enforces per-message limits (<=10 embeds, and <=6000 chars summed across
all embeds in a message). We chunk embeds across multiple messages by a character
budget so delivery never 400s regardless of how verbose the model is.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..models import Idea

log = logging.getLogger("ideas_fairy.deliver.discord")

COLOR = 0x8E5CF7            # fairy purple
MSG_CHAR_BUDGET = 5800     # safety margin under Discord's 6000/message embed limit
MAX_EMBEDS_PER_MSG = 10


def _truncate(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def build_embeds(ideas: list[Idea]) -> list[dict[str, Any]]:
    embeds: list[dict[str, Any]] = []
    for idea in ideas:
        fields = []
        if idea.attack_surface:
            fields.append({"name": "Attack surface",
                           "value": _truncate(idea.attack_surface, 600), "inline": False})
        if idea.novelty_note:
            fields.append({"name": "Why it's a gap",
                           "value": _truncate(idea.novelty_note, 600), "inline": False})
        if idea.first_steps:
            fields.append({"name": "Safe first steps",
                           "value": _truncate(idea.first_steps, 700), "inline": False})
        if idea.frameworks:
            fields.append({"name": "Frameworks",
                           "value": _truncate(", ".join(idea.frameworks), 300),
                           "inline": True})
        if idea.evidence_ids:
            fields.append({"name": "Evidence",
                           "value": _truncate(", ".join(idea.evidence_ids), 300),
                           "inline": True})
        embeds.append({
            "title": _truncate(f"#{idea.rank} — {idea.title}", 256),
            "description": _truncate(idea.rationale, 1200),
            "color": COLOR,
            "fields": fields,
        })
    return embeds


def _embed_chars(e: dict[str, Any]) -> int:
    n = len(e.get("title", "")) + len(e.get("description", ""))
    for f in e.get("fields", []):
        n += len(f.get("name", "")) + len(str(f.get("value", "")))
    return n


def _chunk(embeds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_len = 0
    for e in embeds:
        el = _embed_chars(e)
        if cur and (len(cur) >= MAX_EMBEDS_PER_MSG or cur_len + el > MSG_CHAR_BUDGET):
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(e)
        cur_len += el
    if cur:
        chunks.append(cur)
    return chunks


def _post(webhook: str, payload: dict[str, Any]) -> bool:
    for _ in range(3):
        try:
            r = httpx.post(webhook, json=payload, timeout=30.0)
            if r.status_code == 429:  # rate limited — honour retry_after
                try:
                    wait = float(r.json().get("retry_after", 1.0))
                except (ValueError, KeyError, TypeError):
                    wait = 1.0
                time.sleep(min(wait, 10.0))
                continue
            r.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.error("discord delivery failed: %s", type(exc).__name__)  # never the URL
            return False
    return False


def deliver(webhook: str, ideas: list[Idea], *, username: str = "ideas-fairy",
            mention: str = "", run_id: int | None = None) -> bool:
    """Post ideas to the webhook across as many messages as the limits require.
    Returns True only if every message succeeded. URL never logged."""
    if not webhook:
        log.error("no Discord webhook configured; skipping delivery")
        return False
    embeds = build_embeds(ideas)
    chunks = _chunk(embeds)
    header = f"{mention + ' ' if mention else ''}" \
             f"**ideas-fairy** — top {len(ideas)} offensive-security research ideas" \
             + (f" (run #{run_id})" if run_id else "")
    all_ok = True
    for i, chunk in enumerate(chunks):
        payload: dict[str, Any] = {
            "username": username,
            "embeds": chunk,
            "allowed_mentions": {"parse": ["users"]} if mention else {"parse": []},
        }
        if i == 0:
            payload["content"] = header
        all_ok = _post(webhook, payload) and all_ok
    return all_ok
