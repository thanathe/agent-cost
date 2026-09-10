#!/usr/bin/env python3
"""Render the agent-cost ledger into a markdown report.

The ledger is append-only JSONL written by capture.py; this script is the only
thing that produces .md, so a report can always be regenerated and never drifts
from the raw data.

  report.py                      # this month, summary + per-turn table
  report.py --month 2026-09      # a specific month
  report.py --since 2026-09-01   # free date range
  report.py --repo my-service  # one repo
  report.py --compare            # codebuddy vs claude, side by side
  report.py --write              # also write <LEDGER_DIR>/<month>.md
"""
import json, os, sys, glob, argparse, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_paths import ledger_dir

LEDGER_DIR = ledger_dir()
LEDGER = os.path.join(LEDGER_DIR, "ledger.jsonl")
PRICING = os.path.join(LEDGER_DIR, "pricing.json")


def load_pricing():
    """Optional {model: {input, output, cache_read}} in USD per 1M tokens.

    Claude transcripts carry no credit field, so USD is only ever an estimate
    from token counts. Absent pricing → the report shows tokens and no money,
    which is better than a confidently wrong number.
    """
    try:
        with open(PRICING) as f:
            return json.load(f)
    except Exception:
        return {}


def usd(rec, pricing):
    p = pricing.get(rec.get("model") or "")
    if not p:
        return None
    return round(
        rec.get("input_tokens", 0) / 1e6 * p.get("input", 0)
        + rec.get("output_tokens", 0) / 1e6 * p.get("output", 0)
        + rec.get("cache_read_tokens", 0) / 1e6 * p.get("cache_read", 0),
        4,
    )


def ledger_files(args):
    """(path, owner) pairs to read.

    Default: this machine's own ledger. With --ledger you can point at a pile of
    files collected from other people — owner comes from the filename
    (`ledger-preaw.jsonl` → preaw), so nobody has to edit anyone's data to merge it.
    """
    if not args.ledger:
        return [(LEDGER, "me")]
    out = []
    for pat in args.ledger:
        hits = sorted(glob.glob(os.path.expanduser(pat))) or [os.path.expanduser(pat)]
        for p in hits:
            base = os.path.basename(p)
            for ext in (".jsonl", ".json"):
                base = base[: -len(ext)] if base.endswith(ext) else base
            owner = base.split("-", 1)[1] if base.startswith("ledger-") else base
            out.append((p, owner or "?"))
    return out


def load(args):
    rows = []
    seen = set()
    for path, owner in ledger_files(args):
        if not os.path.exists(path):
            print(f"ข้าม (ไม่เจอไฟล์): {path}", file=sys.stderr)
            continue
        rows += load_one(path, owner, args, seen)
    return rows


def load_one(path, owner, args, seen):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            r["_owner"] = r.get("owner") or owner
            # turn_key = sha1(agent:session:turn) — a session id belongs to one
            # person, so a repeat means the same file arrived twice under two
            # names, not two people doing the same turn. Count it once.
            key = r.get("turn_key")
            if key:
                if key in seen:
                    continue
                seen.add(key)
            d = (r.get("ts") or "")[:10]
            if args.month and not d.startswith(args.month):
                continue
            if args.since and d < args.since:
                continue
            if args.until and d > args.until:
                continue
            if args.repo and args.repo not in (r.get("repo") or ""):
                continue
            if args.agent and r.get("agent") != args.agent:
                continue
            if args.owner and r["_owner"] != args.owner:
                continue
            rows.append(r)
    return rows


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else ("—" if n is None else str(n))


def agg(rows, pricing):
    a = {"turns": len(rows), "credit": 0.0, "has_credit": False, "tok": 0,
         "out": 0, "sec": 0.0, "tools": 0, "usd": 0.0, "has_usd": False}
    for r in rows:
        if r.get("credit") is not None:
            a["credit"] += r["credit"]; a["has_credit"] = True
        a["tok"] += r.get("total_tokens", 0)
        a["out"] += r.get("output_tokens", 0)
        a["sec"] += r.get("elapsed_sec") or 0
        a["tools"] += r.get("n_tool_calls", 0)
        u = usd(r, pricing)
        if u is not None:
            a["usd"] += u; a["has_usd"] = True
    return a


def summary_table(rows, pricing):
    by = defaultdict(list)
    for r in rows:
        by[r.get("agent", "?")].append(r)
    out = ["| Agent | รอบ | Credit | Token รวม | Output tok | เวลารวม | เฉลี่ย/รอบ | Tool calls | USD (ประมาณ) |",
           "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for ag in sorted(by):
        a = agg(by[ag], pricing)
        avg = f"{a['sec']/a['turns']:.1f}s" if a["turns"] else "—"
        out.append(
            f"| **{ag}** | {a['turns']} | {round(a['credit'],2) if a['has_credit'] else '—'} "
            f"| {fmt(a['tok'])} | {fmt(a['out'])} | {a['sec']/60:.1f} นาที | {avg} "
            f"| {fmt(a['tools'])} | {round(a['usd'],2) if a['has_usd'] else '—'} |"
        )
    return "\n".join(out)


def detail_table(rows, pricing, limit=200):
    multi = len({r.get("_owner") for r in rows}) > 1
    head = "| วันเวลา |" + (" คน |" if multi else "") + " Agent | Repo | งานที่สั่ง | Credit | Token | เวลา | Tools | ไฟล์ที่แตะ |"
    out = [head, "|---|" + ("---|" if multi else "") + "---|---|---|--:|--:|--:|--:|--:|"]
    for r in sorted(rows, key=lambda x: x.get("ts", ""), reverse=True)[:limit]:
        prompt = (r.get("prompt") or "").replace("|", "\\|").replace("\n", " ")[:90]
        el = f"{r['elapsed_sec']:.0f}s" if r.get("elapsed_sec") else "—"
        out.append(
            f"| {(r.get('ts') or '')[:16].replace('T',' ')} |"
            + (f" {r.get('_owner','')} |" if multi else "")
            + f" {r.get('agent','')} "
            f"| {r.get('repo','')} | {prompt} | {r.get('credit') if r.get('credit') is not None else '—'} "
            f"| {fmt(r.get('total_tokens',0))} | {el} | {r.get('n_tool_calls',0)} "
            f"| {len(r.get('files_touched') or [])} |"
        )
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month"); ap.add_argument("--since"); ap.add_argument("--until")
    ap.add_argument("--repo"); ap.add_argument("--agent", choices=["codebuddy", "claude"])
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--ledger", nargs="+", metavar="PATH",
                    help="อ่าน ledger จากไฟล์/glob อื่น เช่น --ledger 'team/ledger-*.jsonl' "
                         "(เจ้าของมาจากชื่อไฟล์ ledger-<ชื่อ>.jsonl)")
    ap.add_argument("--by-owner", action="store_true",
                    help="แยกตามคน (อัตโนมัติอยู่แล้วถ้ามีมากกว่า 1 คน)")
    ap.add_argument("--owner", help="เอาเฉพาะคนนี้")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    if not args.month and not args.since:
        args.month = datetime.date.today().strftime("%Y-%m")

    pricing = load_pricing()
    rows = load(args)
    if not rows:
        print(f"ไม่มีข้อมูลใน ledger ({LEDGER})")
        print("ยังไม่มีรอบไหนถูกบันทึก — เช็คว่า Stop hook ติดตั้งแล้วหรือยัง")
        return

    scope = args.month or f"{args.since}..{args.until or 'now'}"
    md = [f"# Agent cost — {scope}", ""]
    if args.repo:
        md.append(f"repo: `{args.repo}`\n")
    md += [f"_{len(rows)} รอบ · สร้างเมื่อ {datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}_", ""]
    md += ["## สรุปรวม", "", summary_table(rows, pricing), ""]

    if not pricing:
        md += ["> USD เป็น `—` เพราะยังไม่ได้ตั้งราคา — เติม `pricing.json` "
               "(`{\"claude-opus-5\": {\"input\": 0, \"output\": 0, \"cache_read\": 0}}` USD ต่อ 1M token) แล้วรันใหม่", ""]

    if args.compare:
        by = defaultdict(lambda: defaultdict(list))
        for r in rows:
            by[r.get("repo", "?")][r.get("agent", "?")].append(r)
        md += ["## แยกตาม repo", "", "| Repo | Agent | รอบ | Credit | Token | เวลารวม |", "|---|---|--:|--:|--:|--:|"]
        for repo in sorted(by):
            for ag in sorted(by[repo]):
                a = agg(by[repo][ag], pricing)
                md.append(f"| {repo} | {ag} | {a['turns']} "
                          f"| {round(a['credit'],2) if a['has_credit'] else '—'} "
                          f"| {fmt(a['tok'])} | {a['sec']/60:.1f} นาที |")
        md.append("")

    owners = {r["_owner"] for r in rows}
    if args.by_owner or len(owners) > 1:
        by = defaultdict(lambda: defaultdict(list))
        for r in rows:
            by[r["_owner"]][r.get("agent", "?")].append(r)
        md += ["## แยกตามคน", "", "| คน | Agent | รอบ | Credit | Token | เวลารวม | Repo ที่แตะ |",
               "|---|---|--:|--:|--:|--:|---|"]
        for who in sorted(by):
            for ag in sorted(by[who]):
                grp = by[who][ag]
                a = agg(grp, pricing)
                repos = sorted({g.get("repo") or "?" for g in grp})
                shown = ", ".join(repos[:4]) + (f" +{len(repos)-4}" if len(repos) > 4 else "")
                md.append(f"| {who} | {ag} | {a['turns']} "
                          f"| {round(a['credit'],2) if a['has_credit'] else '—'} "
                          f"| {fmt(a['tok'])} | {a['sec']/60:.1f} นาที | {shown} |")
        md.append("")

    md += ["## รายรอบ", "", detail_table(rows, pricing, args.limit), ""]
    text = "\n".join(md)
    print(text)

    if args.write:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        path = os.path.join(LEDGER_DIR, f"{args.month or 'range'}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n→ เขียนแล้ว: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
