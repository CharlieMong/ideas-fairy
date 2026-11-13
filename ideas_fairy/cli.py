"""ideas-fairy command-line entrypoint."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .analyze import analyze
from .collect import collect
from .config import load_config
from .llm import LlamaClient, LLMError
from .deliver.discord import deliver as discord_deliver
from .report import write_report
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _open_store(cfg) -> Store:
    import sqlite3
    db = cfg.path("storage", "db_path", default="data/ideas_fairy.sqlite")
    try:
        return Store(db, cfg.path("storage", "raw_dir", default="data/raw"))
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"ERROR: cannot open SQLite store at {db} ({exc}).\n"
            "If this path is on a network/FUSE mount that lacks file locking, "
            "point storage.db_path in config.yaml at a local filesystem."
        ) from exc


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.root)
    store = _open_store(cfg)
    run_id = store.start_run()
    log = logging.getLogger("ideas_fairy")
    log.info("run #%s started", run_id)

    try:
        eligible, counts = collect(cfg, store, run_id)
        log.info("collected %s novel items (%s)", len(eligible),
                 ", ".join(f"{k}={v}" for k, v in counts.items()))

        if args.dry_run:
            store.finish_run(run_id, collected=sum(counts.values()), novel=len(eligible),
                             delivered=0, status="dry-run")
            print(f"[dry-run] {len(eligible)} novel items; no LLM/delivery. "
                  f"DB: {cfg.path('storage', 'db_path')}")
            return 0

        if not eligible:
            store.finish_run(run_id, collected=sum(counts.values()), novel=0,
                             delivered=0, status="no-novel", notes="nothing new this run")
            print("No novel items this run; nothing to rank.")
            return 0

        s = cfg.llm_settings()
        client = LlamaClient(s["base_url"], s["model"], s["api_key"],
                             s["temperature"], s["timeout_s"])
        if not client.health():
            store.finish_run(run_id, collected=sum(counts.values()), novel=len(eligible),
                             delivered=0, status="failed", notes="llama-server unreachable")
            print(f"ERROR: local llama-server not reachable at {s['base_url']}. "
                  "Recorded status and stopped (no delivery).", file=sys.stderr)
            return 2

        top_n = int(cfg.get("run", "top_n", default=10))
        ideas, gaps = analyze(
            client, eligible, top_n=top_n,
            max_context_items=s["max_context_items"],
            max_themes=int(cfg.get("gap_analysis", "max_themes", default=25)),
        )
        store.save_ideas(run_id, ideas)
        log.info("ranked %s ideas", len(ideas))

        # Deliver first: it is the primary output. A report-render issue must never
        # block or lose the Discord push.
        delivered = 0
        if not args.no_deliver:
            webhook = cfg.discord_webhook()
            ok = discord_deliver(
                webhook, ideas,
                username=cfg.get("discord", "username", default="ideas-fairy"),
                mention=cfg.get("discord", "mention", default=""),
                run_id=run_id,
            )
            delivered = len(ideas) if ok else 0
            print("Delivered to Discord." if ok else
                  "Discord delivery skipped/failed (check IDEAS_FAIRY_DISCORD_WEBHOOK).")

        report_path = None
        if cfg.get("run", "write_report", default=True):
            try:
                report_path = write_report(
                    cfg.path("storage", "raw_dir", default="data").parent / "reports",
                    run_id, ideas, gaps, counts)
                log.info("report written: %s", report_path)
            except Exception as exc:  # report is secondary; ideas already delivered/saved
                log.warning("report generation failed (ideas delivered + saved): %s", exc)

        store.finish_run(run_id, collected=sum(counts.values()), novel=len(eligible),
                         delivered=delivered, status="ok")
        print(f"\nRun #{run_id}: {len(ideas)} ideas"
              + (f", report {report_path}" if report_path else ""))
        for idea in ideas:
            print(f"  #{idea.rank} {idea.title}")
        return 0

    except LLMError as exc:
        store.finish_run(run_id, collected=0, novel=0, delivered=0,
                         status="failed", notes=f"llm: {exc}")
        print(f"ERROR (LLM): {exc}", file=sys.stderr)
        return 3
    finally:
        store.close()


def cmd_collect(args: argparse.Namespace) -> int:
    """Collect only — no LLM, no delivery. Useful for seeding the store."""
    cfg = load_config(args.root)
    store = _open_store(cfg)
    run_id = store.start_run()
    try:
        eligible, counts = collect(cfg, store, run_id)
        store.finish_run(run_id, collected=sum(counts.values()), novel=len(eligible),
                         delivered=0, status="collect-only")
        print(f"Collected {sum(counts.values())} items, {len(eligible)} novel.")
        for k, v in sorted(counts.items()):
            print(f"  {k}: {v}")
        return 0
    finally:
        store.close()


def cmd_deliver_last(args: argparse.Namespace) -> int:
    """Push the most recently ranked ideas to Discord without re-collecting."""
    cfg = load_config(args.root)
    store = _open_store(cfg)
    try:
        rid = store.latest_run_with_ideas()
        if rid is None:
            print("No stored ideas to deliver. Run `ideas-fairy run` first.")
            return 1
        ideas = store.load_ideas(rid)
        ok = discord_deliver(
            cfg.discord_webhook(), ideas,
            username=cfg.get("discord", "username", default="ideas-fairy"),
            mention=cfg.get("discord", "mention", default=""),
            run_id=rid,
        )
        print(f"Delivered {len(ideas)} ideas from run #{rid} to Discord." if ok else
              "Delivery failed (check IDEAS_FAIRY_DISCORD_WEBHOOK).")
        return 0 if ok else 2
    finally:
        store.close()


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load_config(args.root)
    s = cfg.llm_settings()
    client = LlamaClient(s["base_url"], s["model"], s["api_key"], s["temperature"], 10.0)
    srcs = cfg.get("sources", default={}) or {}
    enabled = [k for k, v in srcs.items() if (v or {}).get("enabled")]
    print("ideas-fairy configuration check")
    print(f"  root:            {cfg.root}")
    print(f"  db:              {cfg.path('storage', 'db_path')}")
    print(f"  sources enabled: {', '.join(enabled) or '(none)'}")
    print(f"  llm endpoint:    {s['base_url']}  model={s['model']}")
    print(f"  llm reachable:   {'yes' if client.health() else 'NO'}")
    print(f"  discord webhook: {'configured' if cfg.discord_webhook() else 'NOT set'}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    env = root / ".env"
    example = root / ".env.example"
    if env.exists():
        print(".env already exists; leaving it untouched.")
        return 0
    if example.exists():
        env.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote {env} from .env.example. Fill in your Discord webhook.")
    else:
        print("No .env.example found.", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ideas-fairy",
                                description="Offensive-security research-idea scout.")
    p.add_argument("--root", default=".", help="project directory (default: cwd)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="full pipeline: collect -> gap analysis -> rank -> Discord")
    r.add_argument("--no-deliver", action="store_true", help="skip Discord push")
    r.add_argument("--dry-run", action="store_true", help="collect only, no LLM or delivery")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("collect", help="collect sources only (no LLM, no delivery)")
    c.set_defaults(func=cmd_collect)

    d = sub.add_parser("deliver-last",
                       help="re-send the most recent run's ideas to Discord")
    d.set_defaults(func=cmd_deliver_last)

    ch = sub.add_parser("check", help="show config, source and endpoint status")
    ch.set_defaults(func=cmd_check)

    i = sub.add_parser("init", help="create .env from .env.example")
    i.set_defaults(func=cmd_init)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
