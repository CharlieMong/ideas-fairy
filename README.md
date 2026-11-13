# ideas-fairy ✨

An autonomous offensive-security research-idea scout.

It (1) collects new and emerging offensive-security material from multiple sources,
(2) runs a **gap analysis** to find what is *not* being looked at, (3) feeds both
streams to a **local llama-server**, which returns the **top 10 research ideas**, and
(4) pushes them to **Discord** via webhook.

Built read-only by default, with full provenance and chain-of-custody on collected
evidence. No secrets are ever logged or committed.

---

## Pipeline

```
sources ──► collect ──► dedupe + novelty ──► SQLite store (+ raw evidence vault)
                                   │
                 ┌─────────────────┴─────────────────┐
         emerging work                          gap analysis  (llama-server pass 1)
                 └─────────────────┬─────────────────┘
                                   ▼
                     top-10 ranking  (llama-server pass 2)
                                   ▼
                     Discord webhook  +  local HTML digest
```

The two input streams match the design: **emerging work** (what's being done) and
**gaps** (what's neglected). The model synthesises the top ideas from both.

## Sources (read-only, no paid keys)

| Source   | What it pulls                                   | Auth |
|----------|-------------------------------------------------|------|
| `arxiv`  | Recent papers in cs.CR / cs.SE / cs.NI          | none |
| `github` | Recently-active offensive tooling by topic/stars| optional token (higher rate limit) |
| `rss`    | Security research blogs & advisories            | none |
| `reddit` | r/netsec, r/ExploitDev, r/ReverseEngineering …  | optional OAuth (public JSON often 403s) |
| `nvd`    | Recently-published CVEs ≥ CVSS threshold        | none |
| `websearch` | General web search (**Tavily** or **Brave**)    | **opt-in**, needs key |

All sources, thresholds, feeds and categories are configured in `config.yaml`.

### Enabling web search

`websearch` is off by default. To turn it on, set a key and pick a provider:

```bash
# .env
IDEAS_FAIRY_SEARCH_API_KEY=your-provider-key
```
```yaml
# config.yaml -> sources.websearch
enabled: true
provider: "tavily"   # or "brave"
```

- **Tavily** — LLM-oriented search; returns clean content + publish dates. Honours
  `search_depth` (basic|advanced) and `topic` (general|news).
- **Brave** — web search API; honours `freshness` (pd|pw|pm|py) to bias toward recent
  results.

Switching providers is config-only; no code change. Results flow through the same
dedupe, novelty, provenance and gap-analysis path as every other source.

### Reddit access

Reddit now 403s most unauthenticated requests from datacenter IPs. The source works
without credentials (public JSON) but is unreliable; for consistent reads, add an
app-only OAuth client:

1. Create a **script** app at https://www.reddit.com/prefs/apps
2. Put its id/secret in `.env`:
   ```
   IDEAS_FAIRY_REDDIT_CLIENT_ID=...
   IDEAS_FAIRY_REDDIT_CLIENT_SECRET=...
   IDEAS_FAIRY_REDDIT_USER_AGENT=ideas-fairy/0.1 by /u/yourname
   ```

With credentials it uses `oauth.reddit.com` with a bearer token; without them it
falls back to the public endpoint automatically. If the token fetch fails, it also
falls back rather than erroring.

## Install

```bash
uv venv .venv && . .venv/bin/activate
uv pip install -e .
```

## Configure

```bash
ideas-fairy init          # writes .env from .env.example
# edit .env: set IDEAS_FAIRY_DISCORD_WEBHOOK and (if needed) the llama-server URL
```

Secrets come from the environment only. The Discord webhook can instead live in a
local file referenced by `discord.secret_file` in `config.yaml`. Nothing secret is
logged, printed, or committed (`.env`, `data/`, `reports/` are gitignored).

The local llama-server must expose an OpenAI-compatible API, e.g.:

```bash
llama-server -m your-model.gguf --port 8080   # serves /v1/chat/completions
```

## Run

```bash
ideas-fairy check               # config + endpoint + source status (no secrets shown)
ideas-fairy collect             # collect sources only (no LLM, no delivery)
ideas-fairy run --dry-run       # collect + score, no LLM/Discord
ideas-fairy run --no-deliver    # full analysis + HTML report, no Discord push
ideas-fairy run                 # full pipeline → Discord
```

### Scheduling

Run it on a schedule so the digest arrives automatically. On macOS, **`launchd`
is preferred over cron** — if the Mac is asleep at the scheduled time, launchd
runs the missed job on the next wake; plain cron silently skips it.

#### launchd (macOS, recommended) — weekdays at 07:00

A ready-to-use agent lives at `deploy/com.overflowbuff3r.ideas-fairy.plist` (schedule:
Mon–Fri 07:00; output to `data/launchd.log`). Install it:

```bash
cp deploy/com.overflowbuff3r.ideas-fairy.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.overflowbuff3r.ideas-fairy.plist
launchctl list | grep ideas-fairy        # confirm it registered
```

Test it now without waiting for the schedule:

```bash
launchctl start com.overflowbuff3r.ideas-fairy
tail -f data/launchd.log
```

Manage it:

```bash
launchctl unload -w ~/Library/LaunchAgents/com.overflowbuff3r.ideas-fairy.plist   # disable
# edit the plist, then unload + load again to apply changes
```

Edit the `StartCalendarInterval` block in the plist to change the time/days
(`Weekday` 1=Mon … 5=Fri). Paths in the plist are absolute — update them if the
project moves. **Ollama must be running** at fire time (it's a login item, so it is
while you're logged in), and macOS may prompt once for permissions on first run.

#### cron (Linux, or macOS if you prefer) — weekdays at 07:00

```cron
0 7 * * 1-5  cd /path/to/ideas_fairy && .venv/bin/ideas-fairy run >> data/cron.log 2>&1
```

Note: macOS cron does **not** run while the machine is asleep and won't catch up on
wake — use the launchd agent above for reliability.

## Evidence & provenance

- Raw payloads are vaulted verbatim under `data/raw/<evidence_id>.json` with a
  SHA-256, fetch method and retrieval time — kept **separate** from processed analysis.
- Each item carries a stable, greppable `evidence_id` (e.g. `ev_1a2b3c4d…`).
- Ranked ideas cite the evidence ids that motivated them; the model cannot invent ids.
- The SQLite store tracks runs, seen-fingerprints (novelty), items and ideas.

## Safety posture

- **Read-only collection.** No source performs writes, auth, or destructive actions.
- The only outbound write is the Discord push to **your own** webhook — the stated
  purpose. Disable it with `--no-deliver`.
- If the llama-server is unreachable, the run records status and stops; it never
  delivers partial or fabricated results.
- Bounded collection (`per_source_limit`, `lookback_days`); the `websearch` source is
  off by default and marked partial.

## Layout

```
ideas_fairy/
  config.py      store.py       models.py      collect.py
  llm.py         analyze.py     report.py      cli.py
  sources/       arxiv github rss reddit nvd websearch
  deliver/       discord
config.yaml      .env.example   data/   reports/   tests/
```

## Extending

- Add a source: subclass `sources.base.Source`, implement `collect()`, register it
  in `sources/__init__.py`, add a block under `sources:` in `config.yaml`.
- Add a delivery channel: drop a module in `deliver/` and wire it in `cli.cmd_run`.
