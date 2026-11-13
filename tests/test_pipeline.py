"""Offline end-to-end test: fake source + fake llama-server, no network."""
from __future__ import annotations

import json
from pathlib import Path

from ideas_fairy import collect as collect_mod
from ideas_fairy.config import Config
from ideas_fairy.analyze import analyze
from ideas_fairy.collect import collect
from ideas_fairy.deliver.discord import build_embeds
from ideas_fairy.models import Item
from ideas_fairy.report import write_report
from ideas_fairy.store import Store


class FakeSource:
    name = "fake"
    source_kind = "research"

    def collect(self, client):
        items = [
            Item(source="fake", source_kind="research",
                 title="Speculative decode-side cache attacks",
                 url="https://example.test/a", summary="A on B.", method="fake"),
            Item(source="fake", source_kind="code",
                 title="novel-c2-framework", url="https://example.test/b",
                 summary="async c2 over QUIC", method="fake"),
        ]
        raw = {it.evidence_id: {"title": it.title} for it in items}
        return items, raw


class FakeLlama:
    def health(self):
        return True

    def chat(self, system, user, *, json_mode=False, max_tokens=2048):
        if "INPUT A" in user:  # rank pass
            return json.dumps([
                {"title": f"Idea {i}", "rationale": "because", "attack_surface": "x",
                 "novelty_note": "gap", "first_steps": "lab only, read-only recon",
                 "evidence_ids": [], "frameworks": ["T1071"]}
                for i in range(1, 11)
            ])
        return json.dumps({
                "themes": ["cache side-channels", "QUIC C2"],
                "gaps": [{"area": "QUIC-based evasion",
                          "why_neglected": "detection tooling lags",
                          "evidence_ids": []}],
            })


def _cfg(tmp: Path) -> Config:
    raw = {
        "run": {"min_novelty": 0.0, "top_n": 10, "per_source_limit": 40,
                "lookback_days": 21, "write_report": True},
        "storage": {"db_path": "data/ideas_fairy.sqlite", "raw_dir": "data/raw"},
        "sources": {"fake": {"enabled": True}},
        "gap_analysis": {"max_themes": 25},
    }
    return Config(raw=raw, root=tmp)


def test_end_to_end(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(collect_mod, "build_sources", lambda c: [FakeSource()])

    store = Store(cfg.path("storage", "db_path"), cfg.path("storage", "raw_dir"))
    run_id = store.start_run()

    eligible, counts = collect(cfg, store, run_id)
    assert counts == {"fake": 2}
    assert len(eligible) == 2
    assert all(s.is_new for s in eligible)

    # raw evidence vaulted with hash
    raw_files = list((tmp_path / "data" / "raw").glob("ev_*.json"))
    assert len(raw_files) == 2
    rec = json.loads(raw_files[0].read_text())
    assert len(rec["raw_sha256"]) == 64

    ideas, gaps = analyze(FakeLlama(), eligible, top_n=10,
                          max_context_items=120, max_themes=25)
    assert len(ideas) == 10
    assert ideas[0].rank == 1
    assert gaps["gaps"]

    store.save_ideas(run_id, ideas)
    row = store.conn.execute("SELECT COUNT(*) c FROM ideas").fetchone()
    assert row["c"] == 10

    report = write_report(tmp_path / "reports", run_id, ideas, gaps, counts)
    assert report.exists() and report.stat().st_size > 500

    embeds = build_embeds(ideas)
    assert len(embeds) == 10
    assert embeds[0]["title"].startswith("#1")

    # second run: same items must now be non-novel (dedupe works)
    run2 = store.start_run()
    eligible2, _ = collect(cfg, store, run2)
    assert eligible2 == []
    store.close()


def test_discord_requires_webhook():
    from ideas_fairy.deliver.discord import deliver
    assert deliver("", [], run_id=1) is False  # no webhook -> no send


def test_gap_pass_normalizes_string_gaps():
    """Model returning gaps as a list of strings must become list[dict]."""
    import json as _json
    from ideas_fairy.analyze import gap_pass

    class FL:
        def chat(self, system, user, *, json_mode=False, max_tokens=2048):
            return _json.dumps({"themes": ["a", "b"],
                                "gaps": ["QUIC evasion", "firmware supply chain"]})
    out = gap_pass(FL(), "corpus", 25)
    assert isinstance(out["gaps"], list)
    assert all(isinstance(g, dict) and "area" in g for g in out["gaps"])
    assert out["gaps"][0]["area"] == "QUIC evasion"


def test_rank_parser_handles_messy_output():
    """rank parser must survive odd wrapper keys, truncated arrays, single objects."""
    from ideas_fairy.analyze import _coerce_idea_list

    # wrapped under an unexpected key
    wrapped = '{"top_10_ideas": [{"title": "A"}, {"title": "B"}]}'
    assert len(_coerce_idea_list(wrapped)) == 2

    # truncated array (token cutoff): 2 complete objects, 3rd cut off, no closing ]
    truncated = ('[{"title": "A", "rationale": "x"}, '
                 '{"title": "B", "rationale": "y"}, {"title": "C", "ration')
    got = _coerce_idea_list(truncated)
    assert [g["title"] for g in got] == ["A", "B"]   # salvaged the 2 complete ones

    # single object instead of a list
    single = '{"title": "Solo idea", "rationale": "z"}'
    assert _coerce_idea_list(single)[0]["title"] == "Solo idea"

    # fenced + wrapped
    fenced = '```json\n{"ideas":[{"title":"A"}]}\n```'
    assert _coerce_idea_list(fenced)[0]["title"] == "A"
