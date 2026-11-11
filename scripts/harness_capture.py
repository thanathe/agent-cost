#!/usr/bin/env python3
"""Read SKY AI Harness (dsh) turns from its local session journals; no network or hooks.

Journals live at $DSH_HOME/sessions/<workspace>/session-<id>/session.v4.jsonl.zstd
(DSH_HOME defaults to ~/.dsh-org/dsh). Every assistant/message carries its own usage
(inputTokens already excludes cacheReadTokens), so no deltas are estimated.

1 row = 1 turn (turn/start .. turn/end). Subagent sessions (origin "subagent") are
rolled into the parent turn they ran inside, like Claude subagents in capture.py.
Turns that end in error / aborted / interrupted are still written: tokens were billed.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from cost_paths import ledger_dir, record_prompts, ledger_lock

AGENT = "sky-harness"
IDLE_GAP_MS = 30 * 60 * 1000
# user/message source kinds that a person (or their schedule / goal) actually sent.
PROMPT_KINDS = ("user", "schedule", "goal")
# Turns the harness starts by itself when a subagent / background job reports back.
WAKE_KINDS = ("subagent-settled", "agent-message", "tool-jobs")
# Events that mark "a request to the model is about to go out" — the clock for TTFT.
REQUEST_EVENTS = ("step/start", "request/context", "llm/retry-started")
WORK_EVENTS = ("assistant/message", "assistant/attempt", "tool/call", "tool/result", "user/message")


def harness_home():
    return Path(os.environ.get("DSH_HOME", "~/.dsh-org/dsh")).expanduser()


def decompress(path):
    """Return the journal's text lines. Python 3.11 has no zstd, so shell out to `zstd`."""
    try:
        import zstandard  # optional
        with path.open("rb") as raw:
            return zstandard.ZstdDecompressor().stream_reader(raw).read().decode("utf-8", "replace").splitlines()
    except ImportError:
        pass
    exe = shutil.which("zstd")
    if not exe:
        raise RuntimeError("need `zstd` on PATH (brew install zstd) or `pip install zstandard`")
    out = subprocess.run([exe, "-dcq", "--", str(path)], capture_output=True, check=False)
    return out.stdout.decode("utf-8", "replace").splitlines()


def local_iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def norm_session(sid):
    return sid[len("session-"):] if sid and sid.startswith("session-") else sid


def text_of(message):
    parts = [c.get("text", "") for c in (message.get("content") or []) if isinstance(c, dict) and c.get("type") == "text"]
    return " ".join(" ".join(parts).split())


def call_timing(stream, sent):
    """(ttft_ms, gen_ms) for one model call from its streamed chunks.

    ttft = first content chunk − when the request went out; gen = finish − first chunk,
    i.e. pure generation time with tool waits and queueing taken out."""
    first = last = None
    for c in stream or []:
        t = c.get("time", c.get("time0"))
        if not isinstance(t, (int, float)):
            continue
        kind = (c.get("chunk") or {}).get("type")
        if first is None and (c.get("type") != "chunk" or kind == "block-start"):
            first = t
        if kind == "finish":
            last = t
    if first is None or sent is None or first < sent:
        return None, None
    return int(first - sent), (int(last - first) if last and last >= first else None)


def read_session(path):
    """Parse one journal → (header, {turn: turn-dict})."""
    header, turns, cur, sent = {}, {}, None, None
    for line in decompress(path):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind, d, tm = ev.get("type"), ev.get("data") or {}, ev.get("time")
        if kind == "session":
            header = ev
            continue
        if kind == "turn/start":
            cur = d.get("turn")
            turns[cur] = {"start": tm, "end": None, "reason": None, "times": [tm], "usage": {},
                          "model": None, "tools": [], "tool_errors": 0, "api_errors": 0,
                          "error": None, "prompt": "", "prompt_kind": None, "wake": None, "sub": [],
                          "ttft": [], "gen_ms": 0, "gen_out": 0}
            continue
        turn = turns.get(d.get("turn", cur)) if isinstance(d, dict) else None
        if turn is None:
            turn = turns.get(cur)
        if turn is None:
            continue
        if kind in WORK_EVENTS and tm:
            turn["times"].append(tm)
        if kind in REQUEST_EVENTS and tm:
            sent = tm
        if kind == "turn/end":
            turn["end"] = tm
            turn["reason"] = (d.get("reason") or {}).get("kind")
            if turn["reason"] != "completed":
                fail = (d.get("reason") or {}).get("failure") or {}
                turn["error"] = (fail.get("message") or turn["reason"] or "")[:300]
        elif kind == "assistant/message":
            msg = d.get("message") or {}
            key = msg.get("id") or f"{d.get('step')}:{tm}"
            turn["usage"][key] = d.get("usage") or {}
            ttft, gen = call_timing(d.get("stream"), sent)
            if ttft is not None:
                turn["ttft"].append(ttft)
                if gen:
                    turn["gen_ms"] += gen
                    turn["gen_out"] += (d.get("usage") or {}).get("outputTokens", 0) or 0
            sent = None
            model = (msg.get("source") or {}).get("model")
            if model:
                turn["model"] = model
        elif kind == "assistant/attempt":
            # A failed attempt (429 / 5xx) before a retry; usage is usually 0 but keep it if billed.
            for c in d.get("stream") or []:
                chunk = c.get("chunk") or {}
                if chunk.get("type") == "usage":
                    turn["usage"][f"attempt:{d.get('step')}:{tm}"] = chunk.get("usage") or {}
        elif kind == "llm/retry":
            turn["api_errors"] += 1
            turn["error"] = turn["error"] or ((d.get("failure") or {}).get("message") or "")[:300]
        elif kind == "tool/call":
            turn["tools"].append(d.get("name") or "unknown")
        elif kind == "tool/result":
            if (d.get("message") or {}).get("isError"):
                turn["tool_errors"] += 1
        elif kind == "user/message":
            src = (d.get("source") or {}).get("kind")
            if src in PROMPT_KINDS and not turn["prompt"]:
                turn["prompt"] = text_of(d)
                turn["prompt_kind"] = src
            elif src in WAKE_KINDS and not turn["wake"]:
                turn["wake"] = src
    # Auto-woken turns carry no prompt of their own: label them with what woke them and
    # the last thing the person asked in this session, so the row still says what it was for.
    last = None
    for tno in sorted(turns):
        t = turns[tno]
        if t["prompt"]:
            last = t
        elif t["wake"]:
            t["prompt_kind"] = f"auto: {t['wake']}"
            t["prompt"] = last["prompt"] if last else ""
            t["auto"] = True
    return header, turns


def sum_usage(usages):
    tot = {"in": 0, "cr": 0, "cw": 0, "out": 0}
    for u in usages:
        tot["in"] += u.get("inputTokens", 0) or 0
        tot["cr"] += u.get("cacheReadTokens", 0) or 0
        tot["cw"] += u.get("cacheWriteTokens", 0) or 0
        tot["out"] += u.get("outputTokens", 0) or 0
    return tot


def elapsed_sec(times):
    ts = sorted(t for t in times if t)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b - a <= IDLE_GAP_MS]
    return round(sum(gaps) / 1000, 1)


def settled(turn, now_ms=None):
    """Ended, or silent long enough that it never will (app crashed / quit mid-turn).
    Those tokens were billed, so they are kept as end_reason "unfinished"."""
    if turn["end"] is not None:
        return True
    now_ms = now_ms or time.time() * 1000
    if now_ms - max(turn["times"]) > IDLE_GAP_MS:
        turn["reason"] = turn["reason"] or "unfinished"
        return True
    return False


def build_records(sessions, prompts=True):
    """sessions: {path: (header, turns)} → ledger rows."""
    by_id = {}
    for p, (h, t) in sessions.items():
        if not h:
            continue
        if h.get("isSeeded") and h.get("createdAt"):
            # A forked ("seeded") session replays its parent's turns before createdAt;
            # those are the parent's spend, already counted there.
            t = {k: v for k, v in t.items() if (v["start"] or 0) >= h["createdAt"]}
        by_id[norm_session(h.get("id"))] = (p, h, t)
    orphans = []
    # Roll subagent turns into the parent turn whose window contains them.
    for sid, (path, h, turns) in by_id.items():
        parent = norm_session(h.get("parentSession"))
        if not parent or h.get("origin") != "subagent":
            continue
        ptuple = by_id.get(parent)
        for turn in turns.values():
            home = None
            if ptuple:
                for pt in ptuple[2].values():
                    end = pt["end"] or max(pt["times"])
                    if pt["start"] <= turn["start"] <= end:
                        home = pt
                        break
            if home is not None:
                home["sub"].append((sid, turn))
            else:
                orphans.append((sid, path, h, turn))
    rows = []

    def row(sid, path, h, tno, turn, extra=None):
        usages = list(turn["usage"].values())
        for _, st in turn["sub"]:
            usages += list(st["usage"].values())
        u = sum_usage(usages)
        if not u["out"] and not turn["sub"]:
            return None
        cwd = h.get("cwd", "")
        sub_tok = sum(sum(sum_usage(st["usage"].values()).values()) for _, st in turn["sub"])
        tools = list(turn["tools"]) + [t for _, st in turn["sub"] for t in st["tools"]]
        prompt = turn["prompt"] if prompts else ""
        if turn["prompt_kind"] and turn["prompt_kind"] != "user":
            prompt = f"[{turn['prompt_kind']}] {prompt}".strip()
        rec = {"turn_key": hashlib.sha1(f"{AGENT}:{sid}:{tno}".encode()).hexdigest()[:16],
               "agent": AGENT, "session_id": sid, "ts": local_iso(turn["end"] or max(turn["times"])),
               "cwd": cwd, "repo": os.path.basename(cwd.rstrip("/")) or "?", "prompt": prompt[:400],
               "credit": None, "input_tokens": u["in"], "cache_read_tokens": u["cr"],
               "cache_write_tokens": u["cw"], "output_tokens": u["out"],
               "total_tokens": u["in"] + u["cr"] + u["cw"] + u["out"], "usage_v": 2,
               "elapsed_sec": elapsed_sec(turn["times"]), "model": turn["model"],
               "n_tool_calls": len(tools), "tools": sorted(set(tools)), "files_touched": [],
               "n_tool_errors": turn["tool_errors"] + sum(st["tool_errors"] for _, st in turn["sub"]),
               "n_api_errors": turn["api_errors"] + sum(st["api_errors"] for _, st in turn["sub"]),
               "end_reason": turn["reason"], "transcript": str(path)}
        if turn["error"]:
            rec["error"] = turn["error"]
        if turn.get("auto"):
            rec["auto"] = True
        # Model speed with tool waits removed: one TTFT per model call, plus generation time.
        ttft = list(turn["ttft"]) + [x for _, st in turn["sub"] for x in st["ttft"]]
        if ttft:
            rec["ttft_ms"] = ttft
            rec["gen_sec"] = round((turn["gen_ms"] + sum(st["gen_ms"] for _, st in turn["sub"])) / 1000, 1)
            rec["gen_output_tokens"] = turn["gen_out"] + sum(st["gen_out"] for _, st in turn["sub"])
        if turn["sub"]:
            rec["n_subagents"] = len({s for s, _ in turn["sub"]})
            rec["subagent_tokens"] = sub_tok
            rec["subagent_models"] = sorted({st["model"] for _, st in turn["sub"] if st["model"]})
        rec.update(extra or {})
        return rec

    for sid, (path, h, turns) in by_id.items():
        if h.get("origin") == "subagent" and h.get("parentSession"):
            continue
        for tno, turn in turns.items():
            if not settled(turn):   # still running — pick it up next sync
                continue
            r = row(sid, path, h, tno, turn)
            if r:
                rows.append(r)
    for sid, path, h, turn in orphans:   # parent journal gone: keep the spend as its own row
        if not settled(turn):
            continue
        tno = next(k for k, v in by_id[sid][2].items() if v is turn)
        r = row(sid, path, h, tno, turn, {"parent_session": norm_session(h.get("parentSession")), "via": AGENT})
        if r:
            rows.append(r)
    return rows


class HarnessCollector:
    def __init__(self, home=None, directory=None):
        self.home = Path(home) if home else harness_home()
        self.target = Path(directory or ledger_dir()) / "harness-ledger.jsonl"
        self.cache = {}     # path → (size, mtime) → (header, turns)

    def sync(self):
        root = self.home / "sessions"
        if not root.is_dir():
            return 0
        self.target.parent.mkdir(parents=True, exist_ok=True)
        changed = False
        seen = set()
        # Top-level sessions sit in session-<id>/, subagents in a bare <uuid>/ beside them.
        for path in root.glob("*/*/session.v4.jsonl.zstd"):
            try:
                st = path.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime_ns)
            seen.add(str(path))
            hit = self.cache.get(str(path))
            if hit and hit[0] == sig:
                continue
            self.cache[str(path)] = (sig, read_session(path))
            changed = True
        if not changed and self.target.exists():
            return None
        sessions = {p: v[1] for p, v in self.cache.items() if p in seen}
        fresh = build_records(sessions, record_prompts())
        with ledger_lock(str(self.target.parent), name="harness-ledger.lock"):
            return self._merge(fresh)

    def _merge(self, fresh):
        rows = {}
        if self.target.exists():
            for line in self.target.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    rows[r["turn_key"]] = r
                except (ValueError, KeyError):
                    continue
        dirty = False
        for r in fresh:
            if rows.get(r["turn_key"]) != r:
                rows[r["turn_key"]] = r
                dirty = True
        if dirty or not self.target.exists():
            tmp = self.target.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for r in sorted(rows.values(), key=lambda r: r["ts"]):
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp, self.target)
        return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", help="DSH_HOME (default: $DSH_HOME or ~/.dsh-org/dsh)")
    ap.add_argument("--watch", action="store_true", help="re-sync every 10 s")
    args = ap.parse_args()
    c = HarnessCollector(home=args.home)
    while True:
        try:
            n = c.sync()
        except RuntimeError as exc:
            print(f"SKY AI Harness: {exc}", file=sys.stderr)
            return 1
        if n is not None:
            print(f"SKY AI Harness: {n} turns → {c.target}", flush=True)
        if not args.watch:
            break
        time.sleep(10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
