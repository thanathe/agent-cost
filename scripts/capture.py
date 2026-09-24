#!/usr/bin/env python3
"""Append one turn's cost record to the agent-cost ledger.

Runs as a `Stop` hook in BOTH CodeBuddy CLI and Claude Code — the two share a
hook schema, so one script covers both and the numbers stay comparable.

Reads the hook payload on stdin: {session_id, transcript_path, cwd, ...}
Writes one JSON object per turn to  <LEDGER_DIR>/ledger.jsonl

A "turn" = everything from the last human prompt to the end of the transcript.
Tool results, local-command echoes (/model, /clear, /usage) and injected skill
bodies are NOT human prompts, so a 40-tool-call turn stays one record.

Token fields use ONE definition for both agents (usage_v 2):
  input_tokens       uncached input only
  cache_read_tokens  input served from cache
  cache_write_tokens input written to cache
  total_tokens       input + cache_read + cache_write + output  (everything processed)
CodeBuddy's raw prompt_tokens already include the cache hits; Claude's don't —
we subtract on the CodeBuddy side so the two columns mean the same thing.

    capture.py              hook mode (stdin payload)
    capture.py --rebuild    re-derive every ledger row from its transcript (backs up first)

Never fails loudly in hook mode: a hook that errors would interrupt the agent,
so every failure path exits 0. Set AGENT_COST_DEBUG=1 to see why nothing was written.
"""
import collections, json, os, re, sys, time, datetime, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_paths import load_config, ledger_dir, record_prompts, ledger_lock

CFG = load_config()
LEDGER_DIR = ledger_dir(CFG)
LEDGER = os.path.join(LEDGER_DIR, "ledger.jsonl")
RECORD_PROMPTS = record_prompts(CFG)
DEBUG = os.environ.get("AGENT_COST_DEBUG") == "1"
USAGE_V = 2
IDLE_GAP_SEC = 1800  # a silence longer than this inside a turn is waiting, not working
# What a failed turn leaves behind: Claude writes a synthetic isApiErrorMessage, CodeBuddy CLI
# writes an "aborted" reply plus an error-recovery reminder. Gateway 5xx look the same as any
# other API error from here — the tokens were already spent either way.
API_ERROR_RE = re.compile(r"^(API Error|aborted$|<html>|\s*<system-reminder data-role=\"error-recovery)", re.I)
WORK_EVENTS = {"user", "assistant", "message", "reasoning", "function_call", "function_call_result"}
# Serena is an MCP server (oraios/serena) driven from Claude Code / CodeBuddy, so its
# cost is already inside the parent turn — but "how much of my work went through
# serena" is a question of its own. Any tool name containing "serena" counts
# (Claude writes mcp__serena__<tool>; custom server names keep the word "serena").
SERENA_TOOL_RE = re.compile(r"serena", re.I)
SERENA_FILES_MAX = 40


def serena_short_name(name):
    """mcp__serena__find_symbol / serena__find_symbol → find_symbol, so both agents share one key."""
    return name.rsplit("__", 1)[-1]


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


def wait_for_settle(path, quiet=0.5, limit=3.0):
    """CodeBuddy fires Stop before it writes the turn's final assistant message
    (and that message carries its own credit). Wait until the file stops growing."""
    end = time.time() + limit
    last = -1
    while time.time() < end:
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size == last:
            return
        last = size
        time.sleep(quiet)


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


def user_content(ev):
    """Content of a user-role message, or None if the event isn't one."""
    t = ev.get("type")
    # CodeBuddy: {"type":"message","role":"user"}   Claude: {"type":"user","message":{"role":"user"}}
    if t == "message" and ev.get("role") == "user":
        return ev.get("content")
    if t == "user":
        msg = ev.get("message") or {}
        if msg.get("role") == "user":
            return msg.get("content")
    return None


def prompt_text(ev):
    content = user_content(ev)
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


def is_user_message(ev):
    """A user-role message that isn't a tool result."""
    content = user_content(ev)
    if content is None:
        return False
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") in ("tool_result", "function_call_result"):
                return False
    return True


# Messages the harness writes in the user's name that don't start a turn.
_ECHO = re.compile(
    r"^\s*(<local-command-(stdout|stderr|caveat)>|<bash-(input|stdout|stderr)>"
    r"|<system-reminder[^>]*command-caveat"
    r"|\[Request interrupted by user)"
)
_TAG_ONLY_COMMAND = re.compile(
    r"^\s*(<(command-name|command-message|command-args)>[^<]*</\2>\s*)+$"
)


def human_prompt_indices(events):
    """Indices of events that really start a turn.

    Skipped: tool results; Claude `isMeta` injections (skill bodies, caveats,
    "Continue from where you left off"); local-command echoes; and a bare
    `<command-name>/model</command-name>` whose output is a local-command echo —
    that's a built-in like /model or /clear, not work. A slash command that runs
    a skill (/pr-review) has no such echo, so it still counts.
    Typing /model mid-turn used to end the turn early and drop its credits.
    """
    users = [i for i, ev in enumerate(events) if is_user_message(ev)]
    out = []
    for n, i in enumerate(users):
        ev = events[i]
        if ev.get("isMeta"):
            continue
        text = prompt_text(ev)
        if not text or _ECHO.match(text):
            continue
        if _TAG_ONLY_COMMAND.match(text):
            following = [prompt_text(events[j]) for j in users[n + 1:n + 3]]
            if any(t.lstrip().startswith("<local-command-std") for t in following):
                continue
        out.append(i)
    return out


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
        a = re.search(r"<command-args>([^<]*)</command-args>", raw) or re.search(r"ARGUMENTS:\s*(.+)", raw)
        return f"{cmd} {a.group(1).strip()}".strip()[:400] if a else cmd
    m = re.search(r"^Base directory for this skill:\s*(\S+)", raw)
    if m:
        name = os.path.basename(m.group(1).rstrip("/"))
        a = re.search(r"ARGUMENTS:\s*(.+)", raw)
        return f"skill:{name} {a.group(1).strip()}".strip()[:400] if a else f"skill:{name}"
    return " ".join(raw.split())[:400]


def turn_bounds(events, prompts, start):
    """(start, end) of the turn that begins at `start`: up to the next real prompt."""
    nxt = next((i for i in prompts if i > start), len(events))
    return start, nxt


def collect(turn):
    """Pull cost/usage/tool facts out of one turn, handling both transcript dialects."""
    credit = 0.0
    have_credit = False
    tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    model = None
    tools, files = [], []
    serena_calls = 0
    serena_counts = collections.Counter()
    serena_ids, serena_files = set(), set()
    serena_errors = 0
    stamps = []
    # Claude Code writes ONE event per content block (thinking / text / tool_use)
    # and repeats the same `message.usage` on each — summing blindly overcounts
    # by ~2-3x. Count each assistant message, and each tool_use block, once.
    seen_msgs, seen_credit, seen_tools = set(), set(), set()
    tool_errors, api_errors = 0, []

    for k, ev in enumerate(turn):
        # Only work events set elapsed — queue-operation / attachment / system rows and
        # skipped echoes (a /model typed the next morning) can land hours after the turn.
        working = ev.get("type") in WORK_EVENTS and (k == 0 or not is_user_message(ev))
        e = ts_to_epoch(ev.get("timestamp")) if working else None
        if e:
            stamps.append(e)

        pd = ev.get("providerData") or {}
        raw = pd.get("rawUsage") or {}
        if "credit" in raw and ev.get("id") not in seen_credit:
            seen_credit.add(ev.get("id"))
            try:
                credit += float(raw["credit"])
                have_credit = True
            except Exception:
                pass

        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        text = prompt_text(ev) if is_user_message(ev) else ""
        if ev.get("isApiErrorMessage") or (text and API_ERROR_RE.match(text)):
            blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
            said = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
            api_errors.append(" ".join((text or said or "error").split())[:160])
        for blk in (msg.get("content") if isinstance(msg.get("content"), list) else []):
            if isinstance(blk, dict):
                if blk.get("type") == "tool_result" and blk.get("is_error"):
                    tool_errors += 1
                    if blk.get("tool_use_id") in serena_ids:
                        serena_errors += 1
                elif blk.get("type") == "text" and API_ERROR_RE.match(blk.get("text") or ""):
                    api_errors.append(" ".join(blk["text"].split())[:160])
        if ev.get("type") == "function_call_result":
            res = ev.get("result")
            # CodeBuddy writes the outcome on the event itself (status); older rows nest it in result.
            failed = ev.get("status") in ("error", "failed") or (
                isinstance(res, dict) and (res.get("isError") or res.get("status") == "error"))
            if failed:
                tool_errors += 1
                if ev.get("callId") in serena_ids or SERENA_TOOL_RE.search(ev.get("name") or ""):
                    serena_errors += 1
        u = msg.get("usage") or {}
        msg_key = msg.get("id") or ev.get("id")
        if u and msg_key not in seen_msgs:
            seen_msgs.add(msg_key)
            inp = u.get("input_tokens") or u.get("prompt_tokens") or 0
            cr = u.get("cache_read_input_tokens") or 0
            cw = u.get("cache_creation_input_tokens") or 0
            if raw:
                # CodeBuddy (OpenAI-style): prompt_tokens already include cache hits.
                cr = cr or raw.get("prompt_cache_hit_tokens") or 0
                cw = cw or raw.get("prompt_cache_write_tokens") or 0
                inp = max(0, inp - cr - cw)
            tok["input"] += inp
            tok["output"] += u.get("output_tokens") or u.get("completion_tokens") or 0
            tok["cache_read"] += cr
            tok["cache_write"] += cw
        m = msg.get("model") or pd.get("model")
        if m and m != "<synthetic>":   # Claude writes "<synthetic>" for harness-made messages
            model = m

        # tool calls: CodeBuddy = function_call events; Claude = tool_use content blocks
        if ev.get("type") == "function_call":
            name = ev.get("name")
            args = ev.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if name:
                tools.append(name)
                if SERENA_TOOL_RE.search(name):
                    serena_calls += 1
                    serena_counts[serena_short_name(name)] += 1
                    if ev.get("callId"):
                        serena_ids.add(ev["callId"])
                    if isinstance(args, dict) and args.get("relative_path"):
                        serena_files.add(args["relative_path"])
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
                    inp = blk.get("input") or {}
                    if blk.get("name"):
                        tools.append(blk["name"])
                        if SERENA_TOOL_RE.search(blk["name"]):
                            serena_calls += 1
                            serena_counts[serena_short_name(blk["name"])] += 1
                            if tkey:
                                serena_ids.add(tkey)
                            if isinstance(inp, dict) and inp.get("relative_path"):
                                serena_files.add(inp["relative_path"])
                    p = inp.get("file_path") or inp.get("path")
                    if p:
                        files.append(p)

    # Sum the gaps between work events, skipping idle ones: a turn left open can pick up
    # a notification or a resumed reply days later, and last − first counted all of it.
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    elapsed = round(sum(g for g in gaps if g <= IDLE_GAP_SEC), 1) if gaps else None
    return {
        "credit": round(credit, 4) if have_credit else None,
        "tokens": tok,
        "model": model,
        "tools": tools,
        "files": sorted(set(files)),
        "tool_errors": tool_errors,
        "api_errors": api_errors,
        "elapsed_sec": elapsed,
        "serena_calls": serena_calls,
        "serena_counts": dict(serena_counts.most_common()),
        "serena_errors": serena_errors,
        "serena_files": sorted(serena_files)[:SERENA_FILES_MAX],
    }


def agent_of(tpath):
    """Which harness wrote the transcript (stable — used in turn_key)."""
    return "codebuddy" if "/.codebuddy/" in tpath else "claude"


def agent_label(tpath, model):
    """Claude Code pointed at another provider (e.g. a qwen gateway) is not Claude spend.

    Keep it out of the `claude` bucket so plan cost and per-token numbers stay honest.
    """
    base = agent_of(tpath)
    if base == "claude" and model and not model.startswith("claude"):
        family = re.match(r"[a-zA-Z]+", model)
        return f"claude-code:{family.group(0).lower() if family else model}"
    return base


def collect_subagents(tpath, t0, t1):
    """Usage from Claude subagent transcripts that ran inside [t0, t1).

    Subagents (Agent tool, workflows) write <session>/subagents/*.jsonl and never fire
    Stop, so without this their tokens vanish from the ledger. We credit them to the
    turn whose window their messages fall in.
    """
    out = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "n": 0, "models": set()}
    folder = os.path.join(tpath[:-len(".jsonl")] if tpath.endswith(".jsonl") else tpath, "subagents")
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(folder, name)
        try:
            if t0 and os.path.getmtime(path) < t0:
                continue
        except OSError:
            continue
        seen, used = set(), False
        for ev in load_events(path):
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            u = msg.get("usage")
            if not u:
                continue
            ts = ts_to_epoch(ev.get("timestamp"))
            if ts is None or (t0 and ts < t0) or (t1 and ts >= t1):
                continue
            key = msg.get("id") or ev.get("uuid")
            if key in seen:
                continue
            seen.add(key)
            used = True
            out["input"] += u.get("input_tokens") or 0
            out["output"] += u.get("output_tokens") or 0
            out["cache_read"] += u.get("cache_read_input_tokens") or 0
            out["cache_write"] += u.get("cache_creation_input_tokens") or 0
            if msg.get("model") and msg["model"] != "<synthetic>":
                out["models"].add(msg["model"])
        if used:
            out["n"] += 1
    return out


def via_from_env(agent, session_id):
    """(via, parent_session) when this turn ran as a child of a Claude Code session.

    Claude Code exports CLAUDE_CODE_SESSION_ID to everything it spawns, so a CodeBuddy
    or `claude -p` started from Claude's Bash tool inherits the parent's id. A normal
    Claude session sees its own id, which is not a parent.
    """
    parent = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if parent and parent != session_id:
        return "claude", parent
    return None, None


def make_key(agent, session_id, idx):
    return hashlib.sha1(f"{agent}:{session_id}:{idx}".encode()).hexdigest()[:16]


def build_record(events, prompts, start, tpath, session_id, cwd, ts):
    s, e = turn_bounds(events, prompts, start)
    turn = events[s:e]
    facts = collect(turn)
    t = dict(facts["tokens"])
    sub = {"n": 0, "models": set(), "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    if agent_of(tpath) == "claude":
        t0 = ts_to_epoch(turn[0].get("timestamp")) if turn else None
        t1 = ts_to_epoch(events[e].get("timestamp")) if e < len(events) else None
        sub = collect_subagents(tpath, t0, t1)
        for k in ("input", "output", "cache_read", "cache_write"):
            t[k] += sub[k]
    if facts["credit"] is None and t["output"] == 0:
        return None
    return {
        "turn_key": make_key(agent_of(tpath), session_id, s),
        "agent": agent_label(tpath, facts["model"]),
        "session_id": session_id,
        "ts": ts,
        "cwd": cwd,
        "repo": os.path.basename(cwd.rstrip("/")),
        "prompt": (label_of(prompt_text(turn[0])) if turn and s in prompts else "") if RECORD_PROMPTS else "",
        "credit": facts["credit"],
        "input_tokens": t["input"],
        "output_tokens": t["output"],
        "cache_read_tokens": t["cache_read"],
        "cache_write_tokens": t["cache_write"],
        "total_tokens": t["input"] + t["cache_read"] + t["cache_write"] + t["output"],
        "usage_v": USAGE_V,
        "elapsed_sec": facts["elapsed_sec"],
        "model": facts["model"],
        "n_subagents": sub["n"],
        "subagent_tokens": sub["input"] + sub["output"] + sub["cache_read"] + sub["cache_write"],
        "subagent_models": sorted(sub["models"]),
        "via": None,
        "parent_session": None,
        "n_tool_errors": facts["tool_errors"],
        "n_api_errors": len(facts["api_errors"]),
        "error": facts["api_errors"][0] if facts["api_errors"] else None,
        "n_tool_calls": len(facts["tools"]),
        "tools": sorted(set(facts["tools"])),
        "n_serena_calls": facts["serena_calls"],
        "serena_tools": sorted({t for t in facts["tools"] if SERENA_TOOL_RE.search(t)}),
        "serena_tool_counts": facts["serena_counts"],
        "n_serena_errors": facts["serena_errors"],
        "serena_files": facts["serena_files"],
        "files_touched": facts["files"][:40],
        "transcript": tpath,
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

    if agent_of(tpath) == "codebuddy":
        wait_for_settle(tpath)

    events = load_events(tpath)
    if not events:
        log("empty transcript")
        return

    prompts = human_prompt_indices(events)
    start = prompts[-1] if prompts else 0
    session_id = payload.get("session_id") or os.path.basename(tpath).replace(".jsonl", "")
    rec = build_record(
        events, prompts, start, tpath, session_id,
        payload.get("cwd") or "",
        datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    # Nothing was actually spent (e.g. Stop fired on a no-op) → don't pollute the ledger.
    if rec is None:
        log("turn had no usage — skipped")
        return
    rec["via"], rec["parent_session"] = via_from_env(rec["agent"], session_id)

    os.makedirs(LEDGER_DIR, exist_ok=True)
    with ledger_lock(LEDGER_DIR):
        # Cheap dedupe: turn keys only repeat within the same session, so the tail is enough.
        if os.path.exists(LEDGER):
            try:
                with open(LEDGER, "rb") as f:
                    f.seek(max(0, os.path.getsize(LEDGER) - 200_000))
                    if rec["turn_key"].encode() in f.read():
                        log("duplicate turn_key — skipped")
                        return
            except Exception:
                pass
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log("wrote", rec["turn_key"], rec["agent"], rec["credit"], rec["total_tokens"])


def normalize_without_transcript(r):
    """Old row whose transcript is gone: fix the token definition arithmetically."""
    if r.get("usage_v") == USAGE_V:
        return r
    if r.get("agent") == "codebuddy":
        r["input_tokens"] = max(0, (r.get("input_tokens") or 0) - (r.get("cache_read_tokens") or 0)
                                - (r.get("cache_write_tokens") or 0))
    r["total_tokens"] = sum((r.get(k) or 0) for k in
                            ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"))
    r["usage_v"] = USAGE_V
    return r


_DELEGATE_CMD = re.compile(r"cb\.sh|\bcodebuddy\b|\bcbc\b|claude-9arm|\bclaude\s+(-p|--print)\b")


def _norm(text):
    return " ".join(re.sub(r"[\\'\"`]", "", text or "").lower().split())


def delegation_index(since_epoch):
    """[(normalized command, claude session id)] for every CLI delegation Claude ran.

    Rebuild can't see the env a past hook ran with, so it recovers `via` by finding
    the child's prompt inside a Bash command from a Claude transcript (main or subagent).
    """
    root = os.path.expanduser("~/.claude/projects")
    found = []
    for dirpath, _, names in os.walk(root):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(path) < since_epoch:
                    continue
            except OSError:
                continue
            if os.path.basename(dirpath) == "subagents":
                sid = os.path.basename(os.path.dirname(dirpath))
            else:
                sid = name[:-len(".jsonl")]
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if '"tool_use"' not in line or not _DELEGATE_CMD.search(line):
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        for blk in ((ev.get("message") or {}).get("content") or []):
                            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                                cmd = (blk.get("input") or {}).get("command") or ""
                                if _DELEGATE_CMD.search(cmd):
                                    found.append((_norm(cmd), sid))
            except OSError:
                continue
    return found


def via_from_index(rec, index):
    needle = _norm(rec.get("prompt"))[:60]
    if len(needle) < 15:
        return None, None
    for cmd, sid in index:
        if sid != rec.get("session_id") and needle in cmd:
            return "claude", sid
    return None, None


def rebuild():
    """Re-derive every row from its transcript with the current turn rules.

    Old keys hashed the raw index of whatever message started the turn; we find
    that index again, walk back to the real prompt, and recompute. Rows that turn
    out to be pieces of one turn (split by a mid-turn /model) merge into one.
    Manual rows (no transcript) are kept, with token fields normalized.
    """
    if not os.path.exists(LEDGER):
        print("no ledger at", LEDGER)
        return 1
    with open(LEDGER, "rb") as f:
        raw = f.read()
    read_upto = len(raw)          # rows appended after this are picked up under the lock below
    rows = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
    stamps = [ts_to_epoch(r.get("ts")) for r in rows if r.get("ts")]
    index = delegation_index((min(stamps) - 86400) if stamps else 0)
    cache, out, seen = {}, [], set()
    stats = {"rebuilt": 0, "merged": 0, "kept": 0, "relabeled": 0, "via": 0, "subagent_turns": 0}

    for r in rows:
        tpath = r.get("transcript") or ""
        if not tpath or not os.path.isfile(tpath):
            out.append(normalize_without_transcript(r))
            stats["kept"] += 1
            continue
        if tpath not in cache:
            ev = load_events(tpath)
            cache[tpath] = (ev, human_prompt_indices(ev))
        events, prompts = cache[tpath]
        sid = r.get("session_id")
        agent = agent_of(tpath)
        old = next((i for i in range(len(events)) if make_key(agent, sid, i) == r.get("turn_key")), None)
        if old is None:
            out.append(normalize_without_transcript(r))
            stats["kept"] += 1
            continue
        start = max([p for p in prompts if p <= old], default=0)
        new = build_record(events, prompts, start, tpath, sid, r.get("cwd") or "", r.get("ts"))
        if new is None:
            out.append(normalize_without_transcript(r))
            stats["kept"] += 1
            continue
        if new["turn_key"] in seen:
            stats["merged"] += 1
            continue
        seen.add(new["turn_key"])
        if RECORD_PROMPTS and new["prompt"] != r.get("prompt"):
            stats["relabeled"] += 1
        for k in ("owner",):
            if k in r:
                new[k] = r[k]
        # A hook-captured `via` came from the real env — trust it over the index guess.
        if r.get("via"):
            new["via"], new["parent_session"] = r["via"], r.get("parent_session")
        else:
            new["via"], new["parent_session"] = via_from_index(new, index)
        stats["via"] += bool(new["via"])
        stats["subagent_turns"] += bool(new["n_subagents"])
        out.append(new)
        stats["rebuilt"] += 1

    # Recomputing can take a while; writers kept appending meanwhile. Take the lock only
    # for the swap: fold in whatever arrived after our read, then replace the file.
    with ledger_lock(LEDGER_DIR, timeout=60):
        late = []
        with open(LEDGER, "rb") as f:
            f.seek(read_upto)
            for line in f.read().decode("utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("turn_key") not in seen:
                    seen.add(r.get("turn_key"))
                    late.append(r)
        out += late
        backup = LEDGER + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        os.replace(LEDGER, backup)
        tmp = LEDGER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, LEDGER)
    # Belt and braces: if anything still slipped past, the next IDE sync rescans every
    # history file instead of trusting its mtime cache (dedupe by turn_key keeps it safe).
    try:
        os.remove(os.path.join(LEDGER_DIR, ".ide_sync.json"))
    except OSError:
        pass
    if late:
        print(f"picked up {len(late)} row(s) written during rebuild")
    print(f"{len(rows)} rows → {len(out)} · rebuilt {stats['rebuilt']} · merged {stats['merged']} "
          f"· kept as-is {stats['kept']} · prompt changed {stats['relabeled']}\n"
          f"delegated (via claude) {stats['via']} · turns with subagents {stats['subagent_turns']}")
    print("backup:", backup)
    return 0


if __name__ == "__main__":
    if "--rebuild" in sys.argv[1:]:
        sys.exit(rebuild())
    try:
        main()
    except Exception as e:      # a hook must never break the agent's turn
        log("unhandled:", e)
    sys.exit(0)
