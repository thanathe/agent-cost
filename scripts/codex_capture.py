#!/usr/bin/env python3
"""Read completed Codex turns from local rollouts; no network or Codex hooks.

Only explicit per-response token_usage_record events are supported. Older
rollouts without them are skipped rather than estimating cumulative deltas.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import time

from cost_paths import ledger_dir, record_prompts, ledger_lock


def local_iso(value):
    """Rollouts stamp UTC; every other ledger row uses the local offset. Days are cut with
    ts[:10], so a UTC stamp would put late-night turns on the wrong day or month."""
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().isoformat(timespec="seconds")
    except (ValueError, TypeError, AttributeError):
        return value


def user_text(payload):
    """Text from either the legacy user_message event or current rollout message item."""
    message = payload.get("message")
    if isinstance(message, str):
        return " ".join(message.split())
    content = payload.get("content") or []
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content
                 if isinstance(item, dict) and item.get("type") == "input_text"
                 and isinstance(item.get("text"), str)]
        return " ".join(" ".join(parts).split())
    return ""


def read_turns(path):
    turns = {}
    prompts = record_prompts()   # reads the config file — once per rollout, not per event
    session = None
    cwd = ""
    active = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict):
                continue
            p = event.get("payload") or {}
            if not isinstance(p, dict):
                continue
            kind = event.get("type")
            if kind == "session_meta":
                session = p.get("id") or p.get("session_id")
                cwd = p.get("cwd", "")
            turn_id = p.get("turn_id")
            if kind == "event_msg" and p.get("type") == "task_started":
                active = turn_id
            if turn_id and (kind in ("turn_context", "token_usage_record") or
                            kind == "event_msg" and p.get("type") in ("task_started", "task_complete")):
                turn = turns.setdefault(turn_id, {"usage": {}, "tools": {}, "model": None,
                    "cwd": cwd, "start": event.get("timestamp"), "end": None, "prompt": ""})
                if kind == "turn_context":
                    active = turn_id
                    turn["model"] = p.get("model")
                    turn["cwd"] = p.get("cwd") or cwd
                elif kind == "token_usage_record":
                    response = p.get("response_id")
                    if response and p.get("thread_id", session) == session:
                        turn["usage"][response] = p.get("usage") or {}
                elif p.get("type") == "task_complete":
                    completed = p.get("completed_at") or event.get("timestamp")
                    if isinstance(completed, (int, float)):
                        completed = datetime.datetime.fromtimestamp(completed / 1000 if completed > 1e11 else completed, datetime.timezone.utc).isoformat()
                    turn["end"] = completed
                    turn["duration_ms"] = p.get("duration_ms")
            turn = turns.get(active)
            if not turn:
                continue
            is_user_message = (kind == "event_msg" and p.get("type") == "user_message" or
                               kind == "response_item" and p.get("type") == "message" and p.get("role") == "user")
            if is_user_message and prompts:
                text = user_text(p)
                if text:
                    turn["prompt"] = " ".join((turn["prompt"] + " " + text).split())[:400]
            if kind == "response_item" and p.get("type") in ("function_call", "custom_tool_call"):
                call = p.get("call_id") or p.get("id")
                if call:
                    turn["tools"][call] = p.get("name", "unknown")
    records = []
    for tid, turn in turns.items():
        if not session or not turn["end"] or not turn["usage"]:
            continue
        usage = {key: sum(u.get(key, 0) or 0 for u in turn["usage"].values()) for key in
                 ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")}
        cr, cw = usage["cached_input_tokens"], usage["cache_write_input_tokens"]
        inp = max(0, usage["input_tokens"] - cr - cw)
        elapsed = turn.get("duration_ms")
        if elapsed is not None:
            elapsed = round(elapsed / 1000, 1)
        else:
            try:
                elapsed = round((datetime.datetime.fromisoformat(turn["end"].replace("Z", "+00:00")) -
                    datetime.datetime.fromisoformat(turn["start"].replace("Z", "+00:00"))).total_seconds(), 1)
            except (ValueError, TypeError):
                elapsed = None
        records.append({"turn_key": hashlib.sha1(f"codex:{session}:{tid}".encode()).hexdigest()[:16],
            "agent": "codex", "session_id": session, "ts": local_iso(turn["end"]), "cwd": turn["cwd"],
            "repo": os.path.basename(turn["cwd"].rstrip("/")), "prompt": turn["prompt"],
            "credit": None, "input_tokens": inp, "cache_read_tokens": cr, "cache_write_tokens": cw,
            "output_tokens": usage["output_tokens"], "total_tokens": inp + cr + cw + usage["output_tokens"],
            "usage_v": 2, "elapsed_sec": elapsed, "model": turn["model"],
            "n_tool_calls": len(turn["tools"]), "tools": sorted(set(turn["tools"].values())),
            "files_touched": [], "transcript": str(path)})
    return records


class CodexCollector:
    def __init__(self, home=None, directory=None, month=None):
        self.home = Path(home or os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        self.target = Path(directory or ledger_dir()) / "codex-ledger.jsonl"
        self.month = month
        self.cache = {}

    def sync(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        # Parsing rollouts can take seconds; do it with no lock held. Then take a lock of
        # our own (not .ledger.lock, which the Stop hook waits on) just to merge and write.
        fresh, signatures = self._collect()
        with ledger_lock(str(self.target.parent), name="codex-ledger.lock"):
            count = self._merge(fresh)
        self.cache.update(signatures)
        return count

    def _collect(self):
        month = self.month or datetime.date.today().strftime("%Y-%m")
        paths = list((self.home / "sessions" / month.replace("-", "/")).glob("**/*.jsonl"))
        paths += list((self.home / "archived_sessions").glob(f"rollout-{month}-*.jsonl"))
        fresh, signatures = [], {}
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            if self.cache.get(str(path)) == signature:
                continue
            fresh += read_turns(path)
            signatures[str(path)] = signature
        return fresh, signatures

    def _merge(self, fresh):
        rows = {}
        if self.target.exists():
            for line in self.target.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    rows[row["turn_key"]] = row
                except (ValueError, KeyError):
                    continue
        changed = False
        for row in fresh:
            if rows.get(row["turn_key"]) != row:
                rows[row["turn_key"]] = row
                changed = True
        if changed:
            temp = self.target.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as stream:
                for row in sorted(rows.values(), key=lambda row: row["ts"]):
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.replace(temp, self.target)
        return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="month to import, YYYY-MM (default: current month)")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    collector = CodexCollector(month=args.month)
    while True:
        print(f"Codex: {collector.sync()} completed turns", flush=True)
        if not args.watch:
            break
        time.sleep(10)


if __name__ == "__main__":
    main()
