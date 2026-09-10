#!/usr/bin/env python3
"""Append one turn's cost record to the agent-cost ledger.

Runs as a `Stop` hook in BOTH CodeBuddy CLI and Claude Code — the two share a
hook schema, so one script covers both and the numbers stay comparable.

Reads the hook payload on stdin: {session_id, transcript_path, cwd, ...}
Writes one JSON object per turn to  <LEDGER_DIR>/ledger.jsonl

A "turn" = everything from the last human prompt to the end of the transcript.
Tool results are NOT human prompts, so a 40-tool-call turn stays one record.

Never fails loudly: a hook that errors would interrupt the agent, so every
failure path exits 0. Set AGENT_COST_DEBUG=1 to see why nothing was written.
"""
import json, os, re, sys, datetime, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_paths import load_config, ledger_dir, record_prompts

CFG = load_config()
LEDGER_DIR = ledger_dir(CFG)
LEDGER = os.path.join(LEDGER_DIR, "ledger.jsonl")
RECORD_PROMPTS = record_prompts(CFG)
DEBUG = os.environ.get("AGENT_COST_DEBUG") == "1"


def log(*a):
    if DEBUG:
        print("[agent-cost]", *a, file=sys.stderr)


def ts_to_epoch(v):
    """Accept ISO-8601 strings or epoch millis; return float seconds or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e11 else float(v)
    try:
        s = str(v).replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def load_events(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        log("cannot read transcript:", e)
    return out


def is_human_prompt(ev):
    """True only for a real user message — not a tool result, not a sidecar record."""
    t = ev.get("type")
    # CodeBuddy: {"type":"message","role":"user"}   Claude: {"type":"user","message":{"role":"user"}}
    if t == "message" and ev.get("role") == "user":
        content = ev.get("content")
    elif t == "user":
        msg = ev.get("message") or {}
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
    else:
        return False
    # A tool result arrives shaped like a user message — exclude it.
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") in ("tool_result", "function_call_result"):
                return False
    return True


def prompt_text(ev):
    msg = ev.get("message") if ev.get("type") == "user" else ev
    content = (msg or {}).get("content") if isinstance(msg, dict) else None
    if content is None:
        content = ev.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for blk in content:
            # CodeBuddy emits "input_text"; Claude emits "text".
            if isinstance(blk, dict) and blk.get("type") in ("text", "input_text"):
                parts.append(blk.get("text", ""))
            elif isinstance(blk, str):
                parts.append(blk)
        return " ".join(parts).strip()
    return ""


def label_of(raw):
    """Turn a raw prompt into something readable in a report.

    A slash command reaches the transcript as the skill's whole SKILL.md body,
    which is useless as a label — collapse those back to the command name.
    """
    if not raw:
        return ""
    m = re.search(r"<command-name>\s*(/?[\w:-]+)\s*</command-name>", raw)
    if m:
        cmd = m.group(1)
        a = re.search(r"ARGUMENTS:\s*(.+)", raw)
        return f"{cmd} {a.group(1).strip()}".strip()[:400] if a else cmd
    m = re.search(r"^Base directory for this skill:\s*(\S+)", raw)
    if m:
        name = os.path.basename(m.group(1).rstrip("/"))
        a = re.search(r"ARGUMENTS:\s*(.+)", raw)
        return f"skill:{name} {a.group(1).strip()}".strip()[:400] if a else f"skill:{name}"
    return " ".join(raw.split())[:400]


def turn_slice(events):
    """Events from the last human prompt onward."""
    last = None
    for i, ev in enumerate(events):
        if is_human_prompt(ev):
            last = i
    return (events[last:], last) if last is not None else (events, 0)


def collect(turn):
    """Pull cost/usage/tool facts out of one turn, handling both transcript dialects."""
    credit = 0.0
    have_credit = False
    tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    model = None
    tools, files = [], []
    stamps = []
    # Claude Code writes ONE event per content block (thinking / text / tool_use)
    # and repeats the same `message.usage` on each — summing blindly overcounts
    # by ~2-3x. Count each assistant message, and each tool_use block, once.
    seen_msgs, seen_tools = set(), set()

    for ev in turn:
        e = ts_to_epoch(ev.get("timestamp"))
        if e:
            stamps.append(e)

        pd = ev.get("providerData") or {}
        raw = pd.get("rawUsage") or {}
        if "credit" in raw and (ev.get("id") not in seen_msgs):
            try:
                credit += float(raw["credit"])
                have_credit = True
            except Exception:
                pass

        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        u = msg.get("usage") or {}
        msg_key = msg.get("id") or ev.get("id")
        if u and msg_key not in seen_msgs:
            seen_msgs.add(msg_key)
            tok["input"] += u.get("input_tokens") or u.get("prompt_tokens") or 0
            tok["output"] += u.get("output_tokens") or u.get("completion_tokens") or 0
            tok["cache_read"] += u.get("cache_read_input_tokens") or 0
            tok["cache_write"] += u.get("cache_creation_input_tokens") or 0
        model = msg.get("model") or pd.get("model") or model

        # tool calls: CodeBuddy = function_call events; Claude = tool_use content blocks
        if ev.get("type") == "function_call":
            name = ev.get("name")
            if name:
                tools.append(name)
            args = ev.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if isinstance(args, dict):
                p = args.get("file_path") or args.get("path")
                if p:
                    files.append(p)
        content = msg.get("content")
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    tkey = blk.get("id")
                    if tkey and tkey in seen_tools:
                        continue
                    if tkey:
                        seen_tools.add(tkey)
                    if blk.get("name"):
                        tools.append(blk["name"])
                    inp = blk.get("input") or {}
                    p = inp.get("file_path") or inp.get("path")
                    if p:
                        files.append(p)

    elapsed = round(max(stamps) - min(stamps), 1) if len(stamps) >= 2 else None
    return {
        "credit": round(credit, 4) if have_credit else None,
        "tokens": tok,
        "model": model,
        "tools": tools,
        "files": sorted(set(files)),
        "elapsed_sec": elapsed,
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        log("no/invalid stdin payload:", e)
        return

    tpath = payload.get("transcript_path") or ""
    if not tpath or not os.path.isfile(tpath):
        log("no transcript_path:", tpath)
        return

    events = load_events(tpath)
    if not events:
        log("empty transcript")
        return

    turn, turn_idx = turn_slice(events)
    facts = collect(turn)

    # Nothing was actually spent (e.g. Stop fired on a no-op) → don't pollute the ledger.
    if facts["credit"] is None and facts["tokens"]["output"] == 0:
        log("turn had no usage — skipped")
        return

    agent = "codebuddy" if "/.codebuddy/" in tpath else "claude"
    session_id = payload.get("session_id") or os.path.basename(tpath).replace(".jsonl", "")
    # Stable per-turn key so a re-fired hook cannot double-count.
    turn_key = hashlib.sha1(f"{agent}:{session_id}:{turn_idx}".encode()).hexdigest()[:16]

    rec = {
        "turn_key": turn_key,
        "agent": agent,
        "session_id": session_id,
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "cwd": payload.get("cwd") or "",
        "repo": os.path.basename((payload.get("cwd") or "").rstrip("/")),
        "prompt": (label_of(prompt_text(turn[0])) if turn else "") if RECORD_PROMPTS else "",
        "credit": facts["credit"],
        "input_tokens": facts["tokens"]["input"],
        "output_tokens": facts["tokens"]["output"],
        "cache_read_tokens": facts["tokens"]["cache_read"],
        "cache_write_tokens": facts["tokens"]["cache_write"],
        "total_tokens": facts["tokens"]["input"] + facts["tokens"]["output"],
        "elapsed_sec": facts["elapsed_sec"],
        "model": facts["model"],
        "n_tool_calls": len(facts["tools"]),
        "tools": sorted(set(facts["tools"])),
        "files_touched": facts["files"][:40],
        "transcript": tpath,
    }

    os.makedirs(LEDGER_DIR, exist_ok=True)
    # Cheap dedupe: turn keys only repeat within the same session, so the tail is enough.
    if os.path.exists(LEDGER):
        try:
            with open(LEDGER, "rb") as f:
                f.seek(max(0, os.path.getsize(LEDGER) - 200_000))
                if turn_key.encode() in f.read():
                    log("duplicate turn_key — skipped")
                    return
        except Exception:
            pass

    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log("wrote", turn_key, agent, rec["credit"], rec["total_tokens"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # a hook must never break the agent's turn
        log("unhandled:", e)
    sys.exit(0)
