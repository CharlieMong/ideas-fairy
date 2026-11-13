"""Discord delivery: chunking must keep every message within Discord's limits."""
from __future__ import annotations

from ideas_fairy.deliver import discord as dmod
from ideas_fairy.deliver.discord import deliver, build_embeds, _embed_chars
from ideas_fairy.models import Idea


def _verbose_ideas(n=10):
    return [Idea(rank=i, title="X" * 80, rationale="R" * 1500,
                 attack_surface="A" * 800, novelty_note="N" * 800,
                 first_steps="S" * 900, evidence_ids=["ev_" + "a" * 16] * 5,
                 frameworks=["T1071"] * 10) for i in range(1, n + 1)]


class _Resp:
    status_code = 204
    def raise_for_status(self): return None
    def json(self): return {}


def test_every_message_within_limits(monkeypatch):
    sent = []
    monkeypatch.setattr(dmod.httpx, "post",
                        lambda url, json=None, timeout=None: (sent.append(json), _Resp())[1])
    ok = deliver("http://x.test/webhook/y", _verbose_ideas(10), run_id=1)
    assert ok is True
    assert len(sent) >= 2                      # had to split across messages
    for msg in sent:
        assert len(msg["embeds"]) <= 10
        total = sum(_embed_chars(e) for e in msg["embeds"])
        assert total <= 6000                   # Discord's hard limit
    # all 10 ideas delivered exactly once
    titles = [e["title"] for m in sent for e in m["embeds"]]
    assert len(titles) == 10
    assert sent[0]["content"].startswith("**ideas-fairy**")  # header only on first


def test_429_retry(monkeypatch):
    calls = {"n": 0}
    class R429:
        status_code = 429
        def raise_for_status(self): return None
        def json(self): return {"retry_after": 0.01}
    class ROK(_Resp): pass
    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return R429() if calls["n"] == 1 else ROK()
    monkeypatch.setattr(dmod.httpx, "post", fake_post)
    monkeypatch.setattr(dmod.time, "sleep", lambda s: None)
    assert deliver("http://x.test/webhook/y", _verbose_ideas(1), run_id=1) is True
    assert calls["n"] == 2                      # retried after 429
