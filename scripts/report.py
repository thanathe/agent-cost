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
import categories

LEDGER_DIR = ledger_dir()
LEDGER = os.path.join(LEDGER_DIR, "ledger.jsonl")
PRICING = os.path.join(LEDGER_DIR, "pricing.json")


def load_pricing():
    """Optional {model: {input, output, cache_read, cache_write}} in USD per 1M tokens.

    cache_write defaults to input × 1.25 (5-minute cache) when omitted.

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
        + rec.get("cache_read_tokens", 0) / 1e6 * p.get("cache_read", 0)
        + rec.get("cache_write_tokens", 0) / 1e6 * p.get("cache_write", p.get("input", 0) * 1.25),
        4,
    )


OWNER_PLANS = {}   # owner → `_plans` from the `_meta` line of an exported ledger (export.py)
TEAM_DEFAULT = {}  # --team-claude / --team-codex: plan for teammates whose file carries none


def plan_of(pricing, agent, owner=None):
    """`_plans` in pricing.json: {"claude": {"name": "Max 5x", "usd_per_month": 100}}.

    A flat plan means per-token prices say nothing about what you actually paid.
    The honest number is the plan fee spread over the days the ledger covers.
    """
    own = (OWNER_PLANS.get(owner) or {}).get(agent)
    if own and (own.get("usd_per_month") or own.get("usd_per_credit")):
        return own
    if owner is not None and agent in TEAM_DEFAULT:
        return TEAM_DEFAULT[agent]
    return (pricing.get("_plans") or {}).get(agent) or {}


def plan_cost(rows, plan):
    """(usd, first_day, last_day, n_days) — plan fee pro-rated.

    Default: every calendar day in range pays its share, used or not.
    With `days_per_month: N` a month counts as N working days and only days
    that actually have usage are charged plan/N each.
    """
    days = sorted({(r.get("ts") or "")[:10] for r in rows if r.get("ts")})
    if not days or not plan.get("usd_per_month"):
        return None
    first = datetime.date.fromisoformat(days[0])
    last = datetime.date.fromisoformat(days[-1])
    if plan.get("days_per_month"):
        return plan["usd_per_month"] / plan["days_per_month"] * len(days), first, last, len(days)
    usd_total, d = 0.0, first
    while d <= last:
        nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        usd_total += plan["usd_per_month"] / (nxt - d.replace(day=1)).days
        d += datetime.timedelta(days=1)
    return usd_total, first, last, (last - first).days + 1


def surface(r):
    """Row label that splits CodeBuddy by where it ran: CLI (Stop hook) or IDE (ide_sync)."""
    ag = r.get("agent", "?")
    if ag == "codebuddy":
        return "codebuddy ide" if r.get("source") == "ide" else "codebuddy cli"
    return ag


def base_agent(label):
    return label.split(" ", 1)[0]


def grouped(rows):
    """{label: rows} in display order, plus a `codebuddy (รวม)` total when both surfaces are present."""
    by = defaultdict(list)
    for r in rows:
        by[surface(r)].append(r)
    out = {k: by[k] for k in sorted(by)}
    if "codebuddy cli" in out and "codebuddy ide" in out:
        merged = {}
        for k, v in out.items():
            merged[k] = v
            if k == "codebuddy ide":
                merged["codebuddy (รวม)"] = out["codebuddy cli"] + out["codebuddy ide"]
        out = merged
    return out


def real_cost_section(rows, pricing):
    """What each agent really cost: plan fee pro-rated, or credits (× price if known)."""
    by = grouped(rows)
    lines = []
    for ag in by:
        grp, plan = by[ag], plan_of(pricing, base_agent(ag))
        a = agg(grp, pricing)
        mtok = (a["tok"] or 0) / 1e6
        owners = defaultdict(list)
        for r in grp:
            owners[r.get("_owner") or "me"].append(r)
        own_plans = {o: plan_of(pricing, base_agent(ag), o) for o in owners}
        if not plan.get("usd_per_month"):
            plan = next((p for p in own_plans.values() if p.get("usd_per_month")), plan)
        if plan.get("usd_per_month"):
            # Everyone pays their own plan — pro-rate per person, then add up.
            parts = [plan_cost(rs, own_plans[o] if own_plans[o].get("usd_per_month") else plan)
                     for o, rs in owners.items()]
            parts = [p for p in parts if p]
            if not parts:
                continue
            cost = sum(p[0] for p in parts)
            first, last = min(p[1] for p in parts), max(p[2] for p in parts)
            n = sum(p[3] for p in parts)
            who = f" · {len(parts)} คน" if len(parts) > 1 else ""
            per_m = f"${cost/mtok:.4f}" if mtok else "—"
            row = (f"| **{ag}** | {plan.get('name') or 'แพ็กเหมา'} ${plan['usd_per_month']:g}/เดือน "
                   + (f"÷ {plan['days_per_month']:g} วัน × ใช้ {n} วัน" if plan.get("days_per_month") else f"× {n} วันปฏิทิน")
                   + f"{who} ({first:%d/%m}–{last:%d/%m}) | **${cost:.2f}** "
                   f"| ${cost/a['turns']:.3f} | {per_m} |")
            if a["has_usd"] and cost:
                row += f" ${a['usd']:.2f} · คุ้มกว่า API {a['usd']/cost:.0f}× |"
            else:
                row += " — |"
            lines.append(row)
        elif a["has_credit"]:
            ucr = plan.get("usd_per_credit")
            cost = f"**${a['credit']*ucr:.2f}** ({a['credit']:.2f} credit)" if ucr else f"**{a['credit']:.2f} credit**"
            per_turn = f"${a['credit']*ucr/a['turns']:.3f}" if ucr else f"{a['credit']/a['turns']:.2f} cr"
            per_m = (f"${a['credit']*ucr/mtok:.4f}" if ucr else f"{a['credit']/mtok:.2f} cr") if mtok else "—"
            lines.append(f"| **{ag}** | ตาม credit ที่ใช้{'' if ucr else ' (ยังไม่รู้ราคา credit)'} "
                         f"| {cost} | {per_turn} | {per_m} | — |")
    if not lines:
        return []
    return ["## จ่ายจริงเท่าไหร่", "",
            "| Agent | คิดเงินแบบ | ช่วงนี้จ่าย | ต่อรอบ | ต่อ 1M token | ถ้าจ่ายราคา API |",
            "|---|---|--:|--:|--:|---|", *lines, "",
            "> แพ็กเหมาเฉลี่ยทุกวันในปฏิทิน (นับวันที่ไม่ได้ใช้ด้วย) หรือใส่ `_plans.claude.days_per_month` "
            "เพื่อคิดเฉพาะวันที่ใช้ วันละ ค่าแพ็ก ÷ จำนวนวันนั้น "
            "· ใส่ราคา credit ได้ที่ `_plans.codebuddy.usd_per_credit` ใน `pricing.json`", ""]


def failed(r):
    """A turn that hit an API/tool error, or is the retry after one."""
    return bool((r.get("n_api_errors") or 0) or (r.get("n_tool_errors") or 0) or r.get("resumed"))


def error_section(rows, pricing):
    """What the failures cost. A 502/504 bills you: the gateway dies after the model has worked."""
    bad = [r for r in rows if failed(r)]
    if not bad:
        return []
    out = ["## รอบที่ error", "",
           "| Agent | รอบที่ error | จากทั้งหมด | API error | tool error | resume ต่อ | Credit | Token |",
           "|---|--:|--:|--:|--:|--:|--:|--:|"]
    by = grouped(rows)
    for ag in by:
        grp = by[ag]
        b = [r for r in grp if failed(r)]
        if not b:
            continue
        a, ab = agg(grp, pricing), agg(b, pricing)
        share = f"{len(b)/len(grp)*100:.0f}%" if grp else "—"
        out.append(f"| **{ag}** | {len(b)} | {share} | {sum(r.get('n_api_errors') or 0 for r in b)} "
                   f"| {sum(r.get('n_tool_errors') or 0 for r in b)} | {sum(1 for r in b if r.get('resumed'))} "
                   f"| {round(ab['credit'],2) if ab['has_credit'] else '—'} | {fmt(ab['tok'])} |")
    worst = sorted((r for r in bad if r.get("credit")), key=lambda r: -(r.get("credit") or 0))[:5]
    if worst:
        out += ["", "รอบที่ error แล้วเสีย credit มากสุด:", ""]
        for r in worst:
            why = r.get("error") or (f"tool error ×{r['n_tool_errors']}" if r.get("n_tool_errors") else "resume ต่อจากรอบที่ล่ม")
            out.append(f"- `{(r.get('ts') or '')[:16].replace('T',' ')}` **{r['credit']:g} credit** · {r.get('repo','')} "
                       f"— {' '.join(str(why).split())[:110]}")
    return out + ["", "> 502/504 คือ gateway ล่มหลังโมเดลทำงานไปแล้ว — token ถูกคิดไปแล้ว และกด Retry = ส่ง context เดิมไปใหม่ทั้งก้อน "
                  "· CodeBuddy IDE ไม่เก็บ body ของ 5xx ลงดิสก์ เลยเห็นทางอ้อมจากรอบ `resume` เท่านั้น", ""]


def category_section(rows, pricing):
    """Where the turns (and the money) went, by kind of work."""
    if not rows:
        return []
    cat = categories.assign(rows)
    by = defaultdict(list)
    for r in rows:
        by[cat.get(r.get("turn_key") or id(r), "other")].append(r)
    out = ["## ใช้ไปกับงานแบบไหน", "",
           "| หมวด | รอบ | % | Credit | USD ราคา API | Token |", "|---|--:|--:|--:|--:|--:|"]
    for key in sorted(by, key=lambda k: -len(by[k])):
        a = agg(by[key], pricing)
        out.append(f"| {categories.LABELS.get(key, key)} | {a['turns']} | {a['turns']/len(rows)*100:.0f}% "
                   f"| {round(a['credit'],2) if a['has_credit'] else '—'} "
                   f"| {round(a['usd'],2) if a['has_usd'] else '—'} | {fmt(a['tok'])} |")
    return out + ["", "> เดาจาก prompt + tool + นามสกุลไฟล์ (คร่าว ๆ) · รอบที่ prompt สั้นจนจับไม่ได้ "
                  "ใช้หมวดของรอบก่อนหน้าใน session เดียวกัน · แก้คำที่ใช้จับได้ใน `scripts/categories.py`", ""]


def ledger_files(args):
    """(path, owner) pairs to read.

    Default: this machine's own ledger. With --ledger you can point at a pile of
    files collected from other people — owner comes from the filename
    (`ledger-somchai.jsonl` → somchai), so nobody has to edit anyone's data to merge it.
    """
    if not args.ledger:
        return [(LEDGER, "me")] + [(p, "me") for p in [os.path.join(LEDGER_DIR, "codex-ledger.jsonl")] if os.path.exists(p)]
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
            if isinstance(r, dict) and "_meta" in r:
                m = r["_meta"] or {}
                OWNER_PLANS[m.get("owner") or owner] = m.get("plans") or {}
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
            if args.source and r.get("agent") == "codebuddy" and (r.get("source") == "ide") != (args.source == "ide"):
                continue
            if args.owner and r["_owner"] != args.owner:
                continue
            rows.append(r)
    return rows


def team_section(rows, pricing):
    """One line per person: CodeBuddy money next to their own Claude plan, on the same days.

    The team method: credits × the compiler's credit price (one rate for everyone), against the
    Claude plan counted only on days that person spent CodeBuddy credits — plan ÷ working days
    per month (their `days_per_month`, else 22) × those days. Spreading the plan over days they
    never opened CodeBuddy would make CodeBuddy look cheaper than it is.
    """
    ucr = plan_of(pricing, "codebuddy").get("usd_per_credit")
    by = defaultdict(list)
    for r in rows:
        by[r["_owner"]].append(r)
    out = ["## เทียบรายคน (เฉพาะวันที่ใช้ CodeBuddy)", "",
           "| คน | ช่วงข้อมูล | CodeBuddy credit | ≈ USD | วันที่ใช้ CB | credit/วัน | CLI | Claude วันเดียวกัน | CB ÷ Claude | รอบ CB / Claude / Codex |",
           "|---|---|--:|--:|--:|--:|--:|--:|--:|---|"]
    for who in sorted(by):
        rs = by[who]
        days = sorted({r["ts"][:10] for r in rs})
        cb = [r for r in rs if (r.get("agent") or "").startswith("codebuddy")]
        cr = sum(r.get("credit") or 0 for r in cb)
        cr_cli = sum(r.get("credit") or 0 for r in cb if r.get("source") != "ide")
        cb_days = len({r["ts"][:10] for r in cb if r.get("credit")})
        cplan = plan_of(pricing, "claude", who)
        per_day = cplan["usd_per_month"] / (cplan.get("days_per_month") or 22) if cplan.get("usd_per_month") else None
        claude_usd = per_day * cb_days if per_day and cb_days else None
        cb_usd = cr * ucr if ucr else None
        ratio = f"**{cb_usd/claude_usd:.1f}×**" if cb_usd and claude_usd else "—"
        n = lambda a: sum(1 for r in rs if (r.get("agent") or "").startswith(a))
        claude_cell = f"${claude_usd:.2f} (${cplan['usd_per_month']:g}/ด.)" if claude_usd else "— (ไม่มีแพ็ก)"
        out.append(f"| **{who}** | {days[0][5:]}→{days[-1][5:]} | {cr:,.0f} | {f'${cb_usd:,.2f}' if cb_usd is not None else '—'} "
                   f"| {cb_days} | {f'{cr/cb_days:,.0f}' if cb_days else '—'} | {f'{cr_cli/cr*100:.0f}%' if cr else '—'} "
                   f"| {claude_cell} "
                   f"| {ratio} | {n('codebuddy')} / {n('claude')} / {n('codex')} |")
    rate = f"${ucr:g}/credit" if ucr else "ยังไม่ได้ตั้งราคา credit"
    return out + ["", f"> credit ทุกคนคิดที่ {rate} · Claude = แพ็ก ÷ วันทำงานต่อเดือน (ค่าเริ่ม 22) × วันที่ใช้ CodeBuddy "
                  "· แพ็กมาจากไฟล์ export ของแต่ละคน ถ้าไม่มีใช้ของคนรวม", ""]


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
    by = grouped(rows)
    out = ["| Agent | รอบ | Credit | Token รวม | Output tok | เวลารวม | เฉลี่ย/รอบ | Tool calls | รอบที่ error | USD ราคา API |",
           "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for ag in by:
        a = agg(by[ag], pricing)
        avg = f"{a['sec']/a['turns']:.1f}s" if a["turns"] else "—"
        out.append(
            f"| **{ag}** | {a['turns']} | {round(a['credit'],2) if a['has_credit'] else '—'} "
            f"| {fmt(a['tok'])} | {fmt(a['out'])} | {a['sec']/60:.1f} นาที | {avg} "
            f"| {fmt(a['tools'])} | {sum(1 for r in by[ag] if failed(r)) or '—'} "
            f"| {round(a['usd'],2) if a['has_usd'] else '—'} |"
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
            + f" {surface(r)} "
            f"| {r.get('repo','')} | {prompt} | {r.get('credit') if r.get('credit') is not None else '—'} "
            f"| {fmt(r.get('total_tokens',0))} | {el} | {r.get('n_tool_calls',0)} "
            f"| {len(r.get('files_touched') or [])} |"
        )
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month"); ap.add_argument("--since"); ap.add_argument("--until")
    ap.add_argument("--repo"); ap.add_argument("--agent", choices=["codebuddy", "claude", "codex"])
    ap.add_argument("--source", choices=["cli", "ide"], help="CodeBuddy เฉพาะ CLI หรือ IDE")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--ledger", nargs="+", metavar="PATH",
                    help="อ่าน ledger จากไฟล์/glob อื่น เช่น --ledger 'team/ledger-*.jsonl' "
                         "(เจ้าของมาจากชื่อไฟล์ ledger-<ชื่อ>.jsonl)")
    ap.add_argument("--by-owner", action="store_true",
                    help="แยกตามคน (อัตโนมัติอยู่แล้วถ้ามีมากกว่า 1 คน)")
    ap.add_argument("--owner", help="เอาเฉพาะคนนี้")
    ap.add_argument("--team-claude", type=float, metavar="USD",
                    help="แพ็ก Claude/เดือน ของคนที่ไฟล์ export ไม่มีแพ็กติดมา (เช่น 20)")
    ap.add_argument("--team-codex", type=float, metavar="USD", help="เหมือนกันสำหรับ Codex")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    if not args.month and not args.since:
        args.month = datetime.date.today().strftime("%Y-%m")

    pricing = load_pricing()
    if args.team_claude:
        TEAM_DEFAULT["claude"] = {"usd_per_month": args.team_claude, "days_per_month": 22}
    if args.team_codex:
        TEAM_DEFAULT["codex"] = {"usd_per_month": args.team_codex, "days_per_month": 22}
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
    md += real_cost_section(rows, pricing)
    md += category_section(rows, pricing)
    md += error_section(rows, pricing)

    if args.compare:
        by = defaultdict(lambda: defaultdict(list))
        for r in rows:
            by[r.get("repo", "?")][surface(r)].append(r)
        md += ["## แยกตาม repo", "", "| Repo | Agent | รอบ | Credit | Token | เวลารวม |", "|---|---|--:|--:|--:|--:|"]
        for repo in sorted(by):
            for ag in sorted(by[repo]):
                a = agg(by[repo][ag], pricing)
                md.append(f"| {repo} | {ag} | {a['turns']} "
                          f"| {round(a['credit'],2) if a['has_credit'] else '—'} "
                          f"| {fmt(a['tok'])} | {a['sec']/60:.1f} นาที |")
        md.append("")

    owners = {r["_owner"] for r in rows}
    if len(owners) > 1:
        md += team_section(rows, pricing)
    if args.by_owner or len(owners) > 1:
        by = defaultdict(lambda: defaultdict(list))
        for r in rows:
            by[r["_owner"]][surface(r)].append(r)
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
