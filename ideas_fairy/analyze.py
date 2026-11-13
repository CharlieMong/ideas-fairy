"""Gap analysis + top-N ranking. Two passes over the local llama-server, matching
the two input streams: (1) emerging work collected, (2) gaps in what's being studied."""
from __future__ import annotations

import json
import logging
import textwrap
from typing import Any

from .collect import Scored
from .llm import LlamaClient, LLMError, extract_json
from .models import Idea

log = logging.getLogger("ideas_fairy.analyze")

ROLE = (
    "You are a senior offensive-security research analyst with 15 years of red-team "
    "and vulnerability-research experience. You reason precisely, distinguish fact "
    "from assumption, and favour novel, technically-grounded research directions. "
    "All suggested first steps must be non-destructive and lab-scoped."
)


def build_context(items: list[Scored], cap: int) -> tuple[str, set[str]]:
    lines: list[str] = []
    ids: set[str] = set()
    for s in items[:cap]:
        lines.append("- " + s.item.context_line())
        ids.add(s.item.evidence_id)
    return "\n".join(lines), ids


def gap_pass(client: LlamaClient, context: str, max_themes: int) -> dict[str, Any]:
    user = textwrap.dedent(f"""\
        Below is a corpus of recently-surfaced offensive-security material
        (research, tooling, advisories, community posts), each tagged with an
        evidence id in angle brackets.

        CORPUS:
        {context}

        Task:
        1. Extract up to {max_themes} themes that are ACTIVELY being worked on.
        2. Identify GAPS: adjacent, high-value areas that are under-explored or
           absent given this corpus — neglected intersections, assumptions nobody
           is testing, techniques that lag their defensive counterparts.

        Return STRICT JSON:
        {{
          "themes": ["..."],
          "gaps": [{{"area": "...", "why_neglected": "...", "evidence_ids": ["..."]}}]
        }}
        Only use evidence ids that appear in the corpus above.""")
    raw = client.chat(ROLE, user, json_mode=True, max_tokens=2048)
    data = extract_json(raw)
    if not isinstance(data, dict):
        raise LLMError("gap pass did not return an object")
    # Models return gaps in varied shapes (list of dicts, list of strings, or a
    # dict); normalize to a stable list[dict] so downstream never guesses.
    themes = data.get("themes", [])
    data["themes"] = [str(t) for t in themes] if isinstance(themes, list) else []
    norm = []
    raw_gaps = data.get("gaps", [])
    if isinstance(raw_gaps, dict):
        raw_gaps = list(raw_gaps.values())
    if not isinstance(raw_gaps, list):
        raw_gaps = []
    for g in raw_gaps:
        if isinstance(g, dict):
            norm.append({
                "area": str(g.get("area") or g.get("title") or g.get("name") or ""),
                "why_neglected": str(g.get("why_neglected")
                                     or g.get("reason") or g.get("why") or ""),
                "evidence_ids": [e for e in g.get("evidence_ids", [])
                                 if isinstance(e, str)],
            })
        elif isinstance(g, str):
            norm.append({"area": g, "why_neglected": "", "evidence_ids": []})
    data["gaps"] = norm
    return data


def _salvage_objects(text: str) -> list[dict]:
    """Pull every complete top-level {...} object out of (possibly truncated) text.
    Recovers all idea objects emitted before a token-limit cutoff."""
    out: list[dict] = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    o = json.loads(text[start:i + 1])
                    if isinstance(o, dict):
                        out.append(o)
                except json.JSONDecodeError:
                    pass
                start = None
    return [o for o in out if o.get("title")]


def _coerce_idea_list(raw: str) -> list[dict]:
    """Turn a model response into a list of idea dicts, tolerating wrapper keys,
    single-object replies, and truncated JSON."""
    try:
        data = extract_json(raw)
    except LLMError:
        data = None
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and any(isinstance(x, dict) for x in v):
                return [x for x in v if isinstance(x, dict)]
        if data.get("title") or data.get("rationale"):
            return [data]
    return _salvage_objects(raw)


def rank_pass(client: LlamaClient, context: str, gaps: dict[str, Any],
              valid_ids: set[str], top_n: int) -> list[Idea]:
    import json as _json
    gaps_json = _json.dumps(gaps, ensure_ascii=False, indent=2)
    user = textwrap.dedent(f"""\
        You have two inputs.

        INPUT A — emerging work (corpus with evidence ids):
        {context}

        INPUT B — identified gaps / under-explored areas:
        {gaps_json}

        Task: synthesise the TOP {top_n} offensive-security RESEARCH IDEAS worth
        pursuing now. Prefer ideas that exploit a gap in Input B or combine threads
        from Input A in a non-obvious way. Each idea must be concrete and testable.

        Return STRICT JSON as a list of exactly {top_n} objects, ranked best-first:
        [
          {{
            "title": "short, specific",
            "rationale": "why it matters and why now (2-4 sentences)",
            "attack_surface": "where it applies (target class / primitive)",
            "novelty_note": "why this is a gap / under-explored",
            "first_steps": "safe, non-destructive, lab-scoped first validation steps",
            "evidence_ids": ["ids from Input A/B that motivate this"],
            "frameworks": ["relevant MITRE ATT&CK technique ids or other frameworks"]
          }}
        ]
        Only cite evidence ids that were provided. Do not invent sources.""")
    raw = client.chat(ROLE, user, json_mode=True, max_tokens=6000)
    data = _coerce_idea_list(raw)
    if not data:
        raise LLMError("rank pass returned no parseable ideas")

    ideas: list[Idea] = []
    for i, obj in enumerate(data[:top_n], start=1):
        if not isinstance(obj, dict):
            continue
        ev = [e for e in obj.get("evidence_ids", []) if e in valid_ids]
        ideas.append(Idea(
            rank=i,
            title=str(obj.get("title", "")).strip(),
            rationale=str(obj.get("rationale", "")).strip(),
            attack_surface=str(obj.get("attack_surface", "")).strip(),
            novelty_note=str(obj.get("novelty_note", "")).strip(),
            first_steps=str(obj.get("first_steps", "")).strip(),
            evidence_ids=ev,
            frameworks=[str(f) for f in obj.get("frameworks", [])],
        ))
    return ideas


def analyze(client: LlamaClient, items: list[Scored], *, top_n: int,
            max_context_items: int, max_themes: int) -> tuple[list[Idea], dict[str, Any]]:
    context, valid_ids = build_context(items, max_context_items)
    if not context.strip():
        return [], {"themes": [], "gaps": []}
    gaps = gap_pass(client, context, max_themes)
    ideas = rank_pass(client, context, gaps, valid_ids, top_n)
    return ideas, gaps
