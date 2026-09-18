#!/usr/bin/env python3
"""Pack your ledger into one file you can send to whoever compiles the team numbers.

    export.py                          # everything → ~/Desktop/ledger-<name>.jsonl
    export.py --name pond --since 2026-09-01
    export.py --refresh                # re-read transcripts + IDE history first (old installs)
    export.py --hide-repos             # repo names become short hashes

What leaves your machine: per-turn numbers (tokens, credits, time, tool-call and error counts),
model, agent, date, repo name and a work category worked out here from your prompt.
What does not: prompts, file paths, tool names, error text, transcript locations.

The first line is a `_meta` record with your plan settings from pricing.json, so the compiler
prices your Claude/Codex plan as you pay it instead of assuming theirs.
"""
import argparse, datetime, getpass, hashlib, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cost_paths import ledger_dir
import categories

KEEP = ("turn_key", "agent", "ts", "credit", "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "total_tokens", "usage_v", "elapsed_sec", "model", "n_subagents",
        "subagent_tokens", "subagent_models", "via", "source", "resumed", "n_tool_errors",
        "n_api_errors", "n_tool_calls")


def short_hash(s, n=10):
    return hashlib.sha1((s or "").encode()).hexdigest()[:n]


def default_name():
    try:
        out = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True, timeout=5)
        email = out.stdout.strip()
        if email:
            return email.split("@")[0]
    except Exception:
        pass
    return getpass.getuser()


def refresh():
    """Old installs recorded rows before prompts/categories/errors existed — re-derive them."""
    for cmd in (["capture.py", "--rebuild"], ["ide_sync.py", "--resync"], ["codex_capture.py"]):
        path = os.path.join(HERE, cmd[0])
        print(f"→ {' '.join(cmd)}", file=sys.stderr)
        subprocess.run([sys.executable, path, *cmd[1:]], stdout=sys.stderr, stderr=sys.stderr)


def read_rows(d):
    rows = []
    for name in ("ledger.jsonl", "codex-ledger.jsonl"):
        p = os.path.join(d, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if isinstance(r, dict) and r.get("ts") and "_meta" not in r:
                    rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", help="your name in the team report (default: git email before @)")
    ap.add_argument("--since", help="YYYY-MM-DD"); ap.add_argument("--until", help="YYYY-MM-DD")
    ap.add_argument("--out", help="output file (default ~/Desktop/ledger-<name>.jsonl)")
    ap.add_argument("--hide-repos", action="store_true", help="replace repo names with hashes")
    ap.add_argument("--refresh", action="store_true",
                    help="run capture.py --rebuild and ide_sync.py --resync first")
    a = ap.parse_args()

    name = "".join(c for c in (a.name or default_name()).lower() if c.isalnum() or c in "._") or "me"
    d = ledger_dir()
    if a.refresh:
        refresh()

    rows = read_rows(d)
    rows = [r for r in rows if (not a.since or r["ts"][:10] >= a.since) and (not a.until or r["ts"][:10] <= a.until)]
    if not rows:
        sys.exit(f"nothing to export in {d}")

    cats = categories.assign(rows)
    no_prompt = sum(1 for r in rows if not (r.get("prompt") or "").strip())

    try:
        with open(os.path.join(d, "pricing.json"), encoding="utf-8") as f:
            pricing = json.load(f)
    except Exception:
        pricing = {}
    try:
        rev = subprocess.run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        rev = ""

    days = sorted({r["ts"][:10] for r in rows})
    meta = {"_meta": {"owner": name, "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                      "first_day": days[0], "last_day": days[-1], "rows": len(rows),
                      "plans": pricing.get("_plans") or {}, "fx": pricing.get("_fx") or {},
                      "skill_rev": rev, "rows_without_prompt": no_prompt}}

    out_path = os.path.expanduser(a.out or (
        f"~/Desktop/ledger-{name}.jsonl" if os.path.isdir(os.path.expanduser("~/Desktop")) else f"~/ledger-{name}.jsonl"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in sorted(rows, key=lambda r: r["ts"]):
            o = {k: r[k] for k in KEEP if k in r}
            o["owner"] = name
            o["session_id"] = short_hash(r.get("session_id"), 12)
            repo = r.get("repo") or ""
            o["repo"] = short_hash(repo, 8) if a.hide_repos and repo else repo
            o["category"] = cats.get(r.get("turn_key") or id(r), "other")
            o["has_error"] = bool(r.get("error"))
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    by = {}
    for r in rows:
        k = r.get("agent", "?") + (" ide" if r.get("source") == "ide" else "")
        by[k] = by.get(k, 0) + 1
    print(f"✓ {out_path}")
    print(f"  {len(rows)} turns · {days[0]} → {days[-1]} · " + " · ".join(f"{k} {v}" for k, v in sorted(by.items())))
    print(f"  plans: {json.dumps(meta['_meta']['plans'], ensure_ascii=False) or '{}'}")
    if no_prompt > len(rows) * 0.3 and not a.refresh:
        print(f"  ⚠ {no_prompt} turns have no prompt, so they can't be categorised — "
              "run again with --refresh (after git pull)", file=sys.stderr)
    print("  No prompts, paths or tool names are in the file. Send it to whoever compiles the report.")


if __name__ == "__main__":
    main()
