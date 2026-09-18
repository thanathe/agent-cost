#!/usr/bin/env python3
"""What kind of work a turn was — one rough bucketing shared by report.py and the dashboard.

Deliberately dumb and readable: keywords in the prompt first, then the tools used, then the
file extensions touched. Anything still undecided (a short "ok, go ahead") inherits whatever
that session was already doing, which is usually right and keeps "other" small.

The dashboard gets these patterns from the server, so the two views can never drift apart.
Patterns are plain regex, case-insensitive, and safe to edit — they are the whole config.
"""
import os
import re

# key, label shown to people, pattern matched against the prompt. First match wins.
CATS = [
    ("review", "รีวิวโค้ด", r"pr-review|review|pull request|\bpr ?#?\d|merge request"),
    # Before ops/code so "plan the deploy" or "estimate this feature" count as planning.
    ("plan", "วางแผน / ประชุม", r"\bplan\b|วางแผน|estimate|ประเมิน|roadmap|ประชุม|meeting|grill|\bspec\b|\bprd\b"
                                r"|requirement|architecture|ออกแบบระบบ|open question"),
    ("ops", "deploy / git", r"deploy|release|sit\b|prod|docker|migrat|rollback|\bmerge\b|\bpush\b|\btag\b|pipeline|ci\b"),
    ("debug", "แก้บั๊ก", r"bug|debug|error|exception|\bfix\b|พัง|ไม่ขึ้น|ไม่ทำงาน|แก้ปัญหา|fail"),
    ("design", "design / UI", r"figma|design|\bui\b|\bux\b|หน้าจอ|layout|\bcss\b|สไตล์|ปุ่ม|ธีม|theme|icon"),
    ("docs", "เอกสาร / สรุป", r"readme|document|\bdocs?\b|สรุป|report|wiki|เขียนเอกสาร|changelog|post-?mortem"),
    # Messages people send, not mail infrastructure (email routing / DNS belongs to ops).
    ("admin", "งานออฟฟิศ", r"meegle|ลงเวลา|time ?record|timelog|tech task|lark|jira|ticket"
                           r"|ส่ง ?(อี)?เมล|ตอบ ?(อี)?เมล|ร่าง ?(อี)?เมล|inbox"),
    ("data", "ข้อมูล / query", r"\bsql\b|query|ดึงข้อมูล|export|dataset|วิเคราะห์ข้อมูล|report ข้อมูล"),
]
LABELS = dict([(k, l) for k, l, _ in CATS] + [("code", "เขียนโค้ด"), ("other", "อื่น ๆ")])
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "replace_in_file", "write_to_file"}
CODE_EXT = r"\.(ts|tsx|js|jsx|py|go|java|rb|php|rs|c|cpp|cs|sql|sh|proto|astro|vue|swift|kt)$"

_COMPILED = [(k, re.compile(p, re.I)) for k, _, p in CATS]
_CODE = re.compile(CODE_EXT, re.I)


def own_category(row):
    """The category this turn declares by itself, or None when it doesn't say."""
    prompt = row.get("prompt") or ""
    for key, pattern in _COMPILED:
        if pattern.search(prompt):
            return key
    if set(row.get("tools") or []) & EDIT_TOOLS:
        return "code"
    if any(_CODE.search(f) for f in (row.get("files_touched") or [])):
        return "code"
    return None


def assign(rows):
    """{turn_key: category} — undecided turns inherit their session's last decided one."""
    out, last = {}, {}
    for r in sorted(rows, key=lambda r: r.get("ts") or ""):
        own = r.get("category") or own_category(r)   # exported rows carry it precomputed
        key = own or last.get(r.get("session_id")) or "other"
        if own:
            last[r.get("session_id")] = own
        out[r.get("turn_key") or id(r)] = key
    return out


def config():
    """Patterns for the dashboard, so it classifies exactly the same way."""
    return {"cats": [{"key": k, "label": l, "pattern": p} for k, l, p in CATS],
            "labels": LABELS, "edit_tools": sorted(EDIT_TOOLS), "code_ext": CODE_EXT}
