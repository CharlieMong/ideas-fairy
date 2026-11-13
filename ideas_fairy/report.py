"""Local HTML digest of a run. Kept separate from raw evidence."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Idea


def _esc(s: Any) -> str:
    return html.escape(str(s or ""))


def write_report(out_dir: Path, run_id: int, ideas: list[Idea],
                 gaps: dict[str, Any], counts: dict[str, int]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"ideas-fairy-run{run_id}-{ts}.html"

    cards = []
    for idea in ideas:
        ev = ", ".join(_esc(e) for e in idea.evidence_ids)
        fw = ", ".join(_esc(f) for f in idea.frameworks)
        cards.append(f"""
        <article class="idea">
          <h3><span class="rank">#{idea.rank}</span> {_esc(idea.title)}</h3>
          <p class="rationale">{_esc(idea.rationale)}</p>
          <dl>
            <dt>Attack surface</dt><dd>{_esc(idea.attack_surface)}</dd>
            <dt>Why it's a gap</dt><dd>{_esc(idea.novelty_note)}</dd>
            <dt>Safe first steps</dt><dd>{_esc(idea.first_steps)}</dd>
            <dt>Frameworks</dt><dd>{fw or '&mdash;'}</dd>
            <dt>Evidence</dt><dd class="ev">{ev or '&mdash;'}</dd>
          </dl>
        </article>""")

    def _gap_li(g):
        if isinstance(g, dict):
            return f"<li><b>{_esc(g.get('area'))}</b> — {_esc(g.get('why_neglected'))}</li>"
        return f"<li><b>{_esc(g)}</b></li>"
    gap_items = "".join(_gap_li(g) for g in (gaps.get("gaps", []) or []))
    themes = ", ".join(_esc(t) for t in gaps.get("themes", []))
    src_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in sorted(counts.items())
    )

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ideas-fairy — run #{run_id}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; background:#0e0b16; color:#e8e4f0; }}
  header {{ padding: 28px 32px; background: linear-gradient(135deg,#1c1230,#0e0b16); border-bottom:1px solid #2a2140; }}
  h1 {{ margin:0; font-size: 22px; }} h1 .f {{ color:#b794f6; }}
  .meta {{ color:#9a90b5; font-size: 13px; margin-top:6px; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 24px 32px 64px; }}
  section h2 {{ font-size: 16px; color:#b794f6; border-bottom:1px solid #2a2140; padding-bottom:6px; }}
  .idea {{ background:#160f24; border:1px solid #2a2140; border-radius:12px; padding:16px 20px; margin:14px 0; }}
  .idea h3 {{ margin:0 0 8px; font-size:17px; }}
  .rank {{ color:#8e5cf7; font-weight:700; margin-right:6px; }}
  dl {{ display:grid; grid-template-columns: 150px 1fr; gap:4px 14px; margin:10px 0 0; }}
  dt {{ color:#9a90b5; font-size:13px; }} dd {{ margin:0; }}
  dd.ev {{ font-family: ui-monospace, monospace; font-size:12px; color:#7fd1b9; }}
  table {{ border-collapse: collapse; font-size:13px; }} td {{ border:1px solid #2a2140; padding:4px 10px; }}
  ul {{ padding-left: 18px; }}
</style></head>
<body>
<header>
  <h1><span class="f">&#10024; ideas-fairy</span> &mdash; run #{run_id}</h1>
  <div class="meta">Generated {datetime.now(timezone.utc).isoformat()} &middot; {len(ideas)} ranked ideas</div>
</header>
<main>
  <section><h2>Top research ideas</h2>{''.join(cards) or '<p>No ideas produced.</p>'}</section>
  <section><h2>Identified gaps</h2><ul>{gap_items or '<li>&mdash;</li>'}</ul>
    <p class="meta"><b>Active themes:</b> {themes or '&mdash;'}</p></section>
  <section><h2>Collection provenance</h2>
    <table><tr><td><b>source</b></td><td><b>items</b></td></tr>{src_rows}</table></section>
</main></body></html>"""
    out.write_text(doc, encoding="utf-8")
    return out
