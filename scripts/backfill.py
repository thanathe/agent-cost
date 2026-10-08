#!/usr/bin/env python3
"""Fill the ledger with history from before the hook was installed.

    python3 backfill.py                    # Claude Code + CodeBuddy CLI transcripts + IDE / VS Code history
    python3 backfill.py --dry-run          # show what would be added, write nothing
    python3 backfill.py --since 2026-08-01
    python3 backfill.py --only claude      # claude | codebuddy | ide  (repeatable)

Reads whatever is on THIS machine for whoever runs it — nothing is hard-coded to
one person, so a teammate who installed the skill runs the same command and gets
their own history.

Transcript turns go through capture.py's own build_record, so turn keys and the
token definition are exactly what the Stop hook writes: turns the hook already
logged are skipped, and running backfill twice adds nothing.

Subagent transcripts (projects/<p>/<session>/subagents/) aren't walked on their own:
build_record already folds their tokens into the parent turn, as the hook does.

Left alone on purpose: the last turn of a transcript written to in the past
10 minutes — it may still be running, and its Stop hook will write the finished record.
`via` (a CLI turn Claude started) can't be seen from history; `capture.py --rebuild`
recovers it from Claude's Bash commands.
"""
import argparse, collections, datetime, glob, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capture as C
import ide_sync
from cost_paths import ledger_lock

LIVE_SEC = 600


def transcript_globs():
    claude = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    return {
        "claude": os.path.join(claude, "projects", "*", "*.jsonl"),
        "codebuddy": os.path.join(os.path.expanduser("~/.codebuddy"), "projects", "*", "*.jsonl"),
    }


def turns(tpath, since):
    events = C.load_events(tpath)
    prompts = C.human_prompt_indices(events)
    if not prompts:
        return
    session_id = os.path.basename(tpath)[:-len(".jsonl")]
    cwd = next((e.get("cwd") for e in events if e.get("cwd")), "")
    live = time.time() - os.path.getmtime(tpath) < LIVE_SEC
    for n, start in enumerate(prompts):
        if live and n == len(prompts) - 1:
            continue
        s, e = C.turn_bounds(events, prompts, start)
        stamps = [t for t in (C.ts_to_epoch(ev.get("timestamp")) for ev in events[s:e]) if t]
        if not stamps:
            continue
        # The hook stamps a row when the turn ends; the last event is the closest we have.
        ended = datetime.datetime.fromtimestamp(max(stamps)).astimezone()
        if since and ended.date() < since:
            continue
        rec = C.build_record(events, prompts, start, tpath, session_id, cwd,
                             ended.isoformat(timespec="seconds"))
        if rec:
            yield rec


def summarize(label, rows):
    if not rows:
        print(f"  {label:<16} 0 รอบ")
        return
    credit = sum(r.get("credit") or 0 for r in rows)
    tok = sum(r.get("total_tokens") or 0 for r in rows)
    days = sorted({r["ts"][:10] for r in rows})
    print(f"  {label:<16} {len(rows):>5} รอบ · {tok:>14,} token"
          + (f" · {credit:,.2f} credit" if credit else "")
          + f" · {days[0]} → {days[-1]}")


def main():
    ap = argparse.ArgumentParser(description="backfill the agent-cost ledger from local history")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", type=datetime.date.fromisoformat, help="YYYY-MM-DD")
    ap.add_argument("--only", action="append", choices=["claude", "codebuddy", "ide"])
    args = ap.parse_args()
    only = set(args.only or ["claude", "codebuddy", "ide"])

    seen = ide_sync.ledger_keys(C.LEDGER)
    found = collections.defaultdict(list)
    for agent, pattern in transcript_globs().items():
        if agent not in only:
            continue
        for tpath in sorted(glob.glob(pattern)):
            for rec in turns(tpath, args.since):
                if rec["turn_key"] in seen:
                    continue
                seen.add(rec["turn_key"])
                found[agent].append(rec)

    new = sorted((r for rows in found.values() for r in rows), key=lambda r: r["ts"])
    if new and not args.dry_run:
        os.makedirs(C.LEDGER_DIR, exist_ok=True)
        with ledger_lock(C.LEDGER_DIR), open(C.LEDGER, "a", encoding="utf-8") as f:
            fresh = ide_sync.ledger_keys(C.LEDGER)
            new = [r for r in new if r["turn_key"] not in fresh]
            for rec in new:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ide = ide_sync.sync(dry_run=args.dry_run, since=args.since) if "ide" in only else []

    print(("จะเพิ่ม" if args.dry_run else "เพิ่มแล้ว") + f" → {C.LEDGER}")
    if "claude" in only:
        summarize("Claude Code", found["claude"])
    if "codebuddy" in only:
        summarize("CodeBuddy CLI", found["codebuddy"])
    if "ide" in only:
        summarize("CodeBuddy IDE / VS Code", ide)
    if not C.RECORD_PROMPTS:
        print("\n(record_prompts ปิดอยู่ — ไม่ได้จดว่าสั่งอะไร เปิดได้ใน ~/.config/agent-cost/config.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
