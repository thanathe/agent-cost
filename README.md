# agent-cost

*English · [ไทย](README.th.md)*

**Know what your coding agents actually cost you.**

Every time Claude Code, CodeBuddy or Codex finishes a turn, agent-cost writes one line to a local
ledger: credits, tokens, seconds, tools used, files touched, and what you asked for. Then it shows
you the money — in a live dashboard or a monthly markdown report — so "is CodeBuddy cheaper than
Claude for this kind of work?" becomes a number instead of a feeling.

One capture script, one definition of a turn and of a token, for every agent. That is the whole
point: numbers you can put side by side.

Everything stays on your machine. No server, no account, no telemetry.

---

## Contents

- [What you get](#what-you-get) · [Supported agents](#supported-agents) · [Install](#install)
- [Set your prices](#set-your-prices) · [The dashboard](#the-dashboard) · [Reports](#reports)
- [Collectors: CodeBuddy IDE & Codex](#collectors-codebuddy-ide--codex) · [Backfill](#backfill-older-history)
- [Team roll-up](#team-roll-up) · [How counting works](#how-counting-works) · [Privacy](#privacy)
- [Troubleshooting](#troubleshooting) · [Uninstall](#uninstall) · [Contributing](#contributing)

---

## What you get

- **A live dashboard on localhost** — spend per agent, cost per turn, cost per million tokens, daily
  charts, your most expensive turns, and a per-repo breakdown. New turns appear within seconds; no
  refresh.
- **Monthly markdown reports** — `2026-09.md` next to your ledger, regenerated from raw data any time.
- **Real money, not just tokens** — flat subscriptions are pro-rated over the days you used them,
  credits are multiplied by the price you set, and API rates sit beside them so you can see how much
  the subscription saved you.
- **An honest comparison** — the same turn boundary, the same token definition, and cache
  reads/writes counted the same way on every agent.
- **A ledger you own** — plain JSONL, one line per turn, easy to query with `jq`.

## Supported agents

| | Claude Code | CodeBuddy CLI | CodeBuddy IDE | Codex |
|---|---|---|---|---|
| How turns are captured | `Stop` hook | `Stop` hook | reads the app's history | reads local rollouts |
| Credits / cost from the vendor | ❌ not in transcripts | ✅ real credits | ✅ real credits | ❌ none |
| Tokens · duration · tools · prompt | ✅ | ✅ | ✅ | ✅ |
| Money comes from | your plan, or API prices | credits × your price | credits × your price | your plan, or API prices |
| History from before install | `backfill.py` | `backfill.py` | `backfill.py` | `codex_capture.py --month` |

Also handled: Claude subagents (their tokens roll into the turn that launched them), CLI runs that
Claude itself started (tagged `via: claude`), and Claude Code pointed at a non-Claude model through a
gateway (kept out of Claude's totals as `claude-code:<model>`).

---

## Install

Requires `python3` (macOS and most Linux ship with it).

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/thanathe/agent-cost.git ~/.claude/skills/agent-cost
python3 ~/.claude/skills/agent-cost/scripts/install.py
```

The installer asks two questions:

1. **Where should the ledger live?** Enter for `~/.agent-cost`. Pick a folder *outside* any git repo.
2. **Record the prompts you type?** Answer `n` to keep numbers only.

It then appends a `Stop` hook to `~/.claude/settings.json` and `~/.codebuddy/settings.json` (existing
hooks are kept, and each file is backed up first) and links the skill into both agents so
`/agent-cost` works. Running it twice is safe.

```bash
python3 ~/.claude/skills/agent-cost/scripts/install.py --dry-run          # show what it would touch
python3 ~/.claude/skills/agent-cost/scripts/install.py --only claude      # one agent only
python3 ~/.claude/skills/agent-cost/scripts/install.py -y --no-prompts    # unattended, numbers only
```

**Start a new agent session** — sessions already open still run the old hooks.

### Updating

```bash
git -C ~/.claude/skills/agent-cost pull
python3 ~/.claude/skills/agent-cost/scripts/capture.py --rebuild   # recompute old rows under the current rules
```

`--rebuild` backs the ledger up first (`ledger.jsonl.bak-*`). You only need it when the counting
rules change.

---

## Set your prices

Optional, but without it you see tokens and credits — no money. Create `pricing.json` next to your
ledger:

```json
{
  "_plans": {
    "claude":    { "name": "Max 5x", "usd_per_month": 100, "days_per_month": 20 },
    "codex":     { "usd_per_month": 20 },
    "codebuddy": { "usd_per_credit": 0.005 }
  },
  "_fx": { "thb_per_usd": 32.5 },

  "claude-opus-5":    { "input": 5,  "output": 25, "cache_read": 0.5,  "cache_write": 6.25 },
  "claude-fable-5-1": { "input": 10, "output": 50, "cache_read": 0.25, "cache_write": 12.5 }
}
```

| Key | Meaning |
|---|---|
| `_plans.<agent>.usd_per_month` | What you really pay per month for a flat subscription |
| `_plans.<agent>.days_per_month` | Omit → the fee is spread over every calendar day. Set 20 → a month counts as 20 working days and only the days you used it are charged |
| `_plans.codebuddy.usd_per_credit` | What one credit costs |
| `_fx.thb_per_usd` | Optional local-currency estimate shown beside USD (dashboard only) |
| `<model>` | API rates per 1M tokens, for the "if you paid per token" column. `cache_write` defaults to `input × 1.25` |

Prices change — check the vendor's page rather than trusting the example. Everything here is a
display setting; this tool never bills anything.

---

## The dashboard

```bash
python3 ~/.claude/skills/agent-cost/scripts/dashboard.py --open
```

Opens <http://127.0.0.1:8791> (`--port` to change). Leave it running: it re-checks the ledger every
few seconds and new turns show up on their own.

- **Headline** — what each agent cost over the selected range, side by side.
- **Date range** — all / today / 7 / 30 days, or pick your own start and end.
- **Split by surface** — CodeBuddy CLI vs IDE, and how much of it Claude delegated.
- **Fair-comparison row** — total money, per turn, per 1M output tokens, per 1M tokens, plus the
  break-even credit price. Read them together: the winner changes with the measure.
- **Daily charts, priciest turns, per-repo table**, and notes the data suggests (for example that
  switching model mid-session forces a full cache miss).
- **Settings** — credit price, plan price, currency rate. Typed values are remembered in your browser
  and fall back to `pricing.json`.

It binds to 127.0.0.1 and rejects any request whose `Host` is not localhost, because the ledger
contains your prompts and file paths.

## Reports

```bash
R=~/.claude/skills/agent-cost/scripts/report.py
python3 $R                             # this month
python3 $R --month 2026-09 --write     # also write 2026-09.md next to the ledger
python3 $R --compare                   # per repo
python3 $R --since 2026-09-01 --until 2026-09-15
python3 $R --agent codebuddy --source ide
python3 $R --repo my-service --limit 50
```

Each report has a summary table, a "what it really cost" table (plan pro-rated, credits converted,
API equivalent), optional per-repo and per-person tables, and the most recent turns.

Or just ask your agent — *"how many credits did I use this month?"* — and the bundled skill runs the
right command for you.

---

## Collectors: CodeBuddy IDE & Codex

Neither has a hook, so both are read from files they already write locally. The dashboard collects
them while it runs; otherwise run them yourself before a report.

```bash
S=~/.claude/skills/agent-cost/scripts
python3 $S/ide_sync.py                 # CodeBuddy IDE  (--dry-run, --watch, --resync)
python3 $S/codex_capture.py            # Codex, current month  (--month YYYY-MM, --watch)
```

- **CodeBuddy IDE** lands as `agent: codebuddy`, `source: ide`, so it adds up with the CLI but can be
  split anywhere. It reads the extension's history under `~/Library/Application Support` (macOS),
  `%APPDATA%` (Windows) or `~/.config` (Linux) — override with `CODEBUDDY_APPDATA`. It imports
  everything it finds, which can be months of history on the first run.
- **Codex** writes its own `codex-ledger.jsonl` from `$CODEX_HOME` (default `~/.codex`). Only turns
  with real per-response usage and an explicit completion are counted; aborted turns are skipped.
  Month selection follows the rollout folders, so import older months explicitly.

## Backfill older history

Hooks only see turns that finish after install. Everything before that is still on disk:

```bash
S=~/.claude/skills/agent-cost/scripts
python3 $S/backfill.py --dry-run --since 2026-08-01   # count first
python3 $S/backfill.py --since 2026-08-01             # then write
python3 $S/capture.py --rebuild                       # optional: tag which CLI runs Claude started
```

- **Always pass `--since`** unless you really want everything; a few months of Claude Code can be
  thousands of turns.
- Safe to re-run: it reuses the hook's own turn keys, so nothing is counted twice.
- It skips the last turn of any transcript touched in the past 10 minutes — that one may still be
  running, and its hook will record it.
- On a flat plan, don't backfill past the start of your current subscription, or those months are
  priced wrong.
- `--only claude|codebuddy|ide` limits the source.

---

## Team roll-up

There is no server. Each person sends their ledger to whoever compiles the numbers.

**Sending** — one command, after `git pull`:

```bash
cd ~/.claude/skills/agent-cost && git pull
python3 scripts/export.py --refresh --name <yourname>     # → ~/Desktop/ledger-<yourname>.jsonl
```

`--refresh` re-reads your transcripts and IDE history with the current rules first (needed once if
you installed before work types and error tracking existed). Add `--since 2026-09-01` to limit the
range, `--hide-repos` to replace repo names with hashes.

The file holds per-turn numbers only — tokens, credits, time, tool-call and error counts, model, date,
repo and a work category worked out on your machine. **No prompts, file paths, tool names or error
text.** Its first line carries your plan settings from `pricing.json`, so the compiler prices your
Claude/Codex plan as you actually pay it.

**Compiling** — drop the files in one folder (outside any git repo):

```bash
R=~/.claude/skills/agent-cost/scripts/report.py
python3 $R --ledger 'team/ledger-*.jsonl' --month 2026-09        # totals plus a per-person table
python3 $R --ledger 'team/ledger-*.jsonl' --owner somchai        # one person
```

With more than one person the report adds a **per-person table**: CodeBuddy credits (priced at the
compiler's credit rate for everyone), credits per day used, IDE share, each person's own Claude plan,
and the ratio between them. Re-sending a whole file is fine — turns are de-duplicated by key.
Plain ledgers without a plan line fall back to the compiler's `pricing.json`.

The dashboard only ever reads the ledger on your own machine.

---

## How counting works

These rules are what make the numbers comparable — worth two minutes before you quote them to anyone.

- **One row = one turn you asked for**, not one API call. Forty tool calls in a row is still one turn.
  Tool results, injected skill bodies, local command echoes (`/model`, `/clear`, `!bash`) and
  interruption markers do not start a new turn.
- **Tokens use one definition everywhere** (`usage_v: 2`): `input_tokens` is uncached input only,
  `cache_read_tokens` and `cache_write_tokens` are separate, and
  `total_tokens = input + cache read + cache write + output`. Vendors that bundle cache hits into
  their input count are corrected on the way in.
- **Claude subagents** fold into the turn that launched them (`n_subagents`, `subagent_tokens`).
- **Duration** is the sum of gaps between work events, ignoring silences longer than 30 minutes, so a
  session left open overnight doesn't become a 14-hour turn. It includes waiting on tools, so it is
  not pure inference time.
- **Flat plans are spread over days, never over tokens**: every calendar day in range, or only the
  days you used it when `days_per_month` is set. A plan does not get cheaper because you typed less.
- **Failed turns still cost.** A 502/504 is the gateway dying *after* the model worked — the tokens
  were already billed. Rows carry `n_api_errors` + `error`, `n_tool_errors`, and `resumed` (an IDE
  turn that begins by resuming an unfinished one, i.e. the retry after a failure), and the dashboard
  totals what share of your spend went to them. The IDE never writes the 5xx body to disk, so there
  it is only visible through the resumed turn that follows.
- **A turn with no output and no credit is skipped** — nothing happened.
- **Nothing is estimated silently.** No guessed prices, no guessed credits; missing data shows as `—`.

Field-by-field detail lives in [SKILL.md](SKILL.md).

---

## Privacy

The ledger never leaves your machine on its own — but it holds **your prompts, the paths of files the
agent touched, and your repo names**.

- **Never commit `ledger.jsonl`, `*.bak-*`, the generated `*.md` reports, `pricing.json`,
  `.ide_sync.json` or `codex-ledger.jsonl`.** If your ledger folder sits inside a repo (a wiki, say),
  add this first:

  ```gitignore
  agent-cost/*.jsonl
  agent-cost/*.jsonl.*
  agent-cost/*.md
  agent-cost/pricing.json
  agent-cost/.ide_sync.json*
  agent-cost/.ledger.lock
  ```

  ⚠️ `git rm --cached ledger.jsonl && git commit -- ledger.jsonl` **re-adds the file** — a commit with
  a path takes that file from the working tree. Commit with no path, then check `git ls-files`.
- **Don't want prompts recorded?** `install.py --no-prompts` (applies to later turns); strip older
  ones with the `jq` command above before sharing.
- **Moving the ledger later:** edit `~/.config/agent-cost/config.json`.
- This repo is public and contains code only — no one's data.

---

## Troubleshooting

```bash
# fire the hook by hand against your latest transcript and see what it says
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "{\"session_id\":\"test\",\"transcript_path\":\"$T\",\"cwd\":\"$PWD\"}" \
  | AGENT_COST_DEBUG=1 python3 ~/.claude/skills/agent-cost/scripts/capture.py
```

| Symptom | Likely cause |
|---|---|
| `turn had no usage — skipped` | Normal — that turn really spent nothing |
| No output at all | Hook not installed → re-run `install.py`, check its `settings:` line, start a new session |
| IDE turns missing | `ide_sync.py --dry-run` returns 0 → history not found; set `CODEBUDDY_APPDATA` |
| Codex turns missing | Aborted turns and older rollout formats are skipped by design |
| Numbers look wrong after an update | `capture.py --rebuild`, or `ide_sync.py --resync` for IDE rows |
| Claude's cost jumped after a backfill | You backfilled past the start of your current plan — restore the backup and use `--since` |
| Dashboard badge turns red | The server stopped; start `dashboard.py` again |

A broken hook never breaks your agent: `capture.py` always exits 0.

## Uninstall

```bash
python3 ~/.claude/skills/agent-cost/scripts/install.py --uninstall
```

Removes the hooks and the skill links. Your ledger stays; delete it yourself if you want it gone.

## Benchmarks

`bench/` holds small harnesses for comparing agents head-to-head on the same task — see [bench/README.md](bench/README.md).

## Contributing

Issues and pull requests are welcome — wrong numbers, another agent, a confusing paragraph. See
[CONTRIBUTING.md](CONTRIBUTING.md); the short version is: no personal data in the repo, the hook must
never stall an agent, standard library only. Every PR is reviewed before merge.

When reporting a bug, include the `AGENT_COST_DEBUG=1` output above — **with paths, repo names and
prompts removed**.

[MIT](LICENSE)
