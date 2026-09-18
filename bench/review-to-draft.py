#!/usr/bin/env python3
"""Turn a benchmark review.json into the pr-review skill's draft.json (for post_review.py).

    review-to-draft.py <review.json> <owner/repo> <out.json> [--drop GROUP_IDX ...]

Findings become inline comments; the summary, verdict and any finding the caller drops or that
has no usable line go into the review body. Event is always COMMENT.
"""
import argparse, json

EMOJI = {"blocker": "🔴 **Blocker**", "major": "🟠 **Major**", "minor": "🟡 **Minor**", "nit": "⚪ **Nit**"}
ORDER = ["blocker", "major", "minor", "nit"]
VERDICT = {"ship": "ship", "fix then ship": "แก้แล้ว ship", "rework": "รื้อ"}


def comment_body(f):
    sev = EMOJI.get(str(f.get("severity", "")).lower(), "⚪ **Nit**")
    parts = [f"{sev} — {f.get('title', '').strip()}"]
    if f.get("how_it_breaks"):
        parts.append(f"พังยังไง: {f['how_it_breaks'].strip()}")
    if f.get("evidence"):
        parts.append(f"หลักฐาน: {f['evidence'].strip()}")
    if f.get("fix"):
        parts.append(f"แก้: {f['fix'].strip()}")
    return "\n\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("review"); ap.add_argument("repo"); ap.add_argument("out")
    ap.add_argument("--drop", type=int, nargs="*", default=[], help="finding indexes to leave out")
    a = ap.parse_args()
    r = json.load(open(a.review))
    findings = [f for i, f in enumerate(r.get("findings") or []) if i not in set(a.drop)]
    findings.sort(key=lambda f: ORDER.index(str(f.get("severity", "nit")).lower())
                  if str(f.get("severity", "nit")).lower() in ORDER else 9)
    counts = {s: sum(1 for f in findings if str(f.get("severity", "")).lower() == s) for s in ORDER}
    tally = " · ".join(f"{EMOJI[s].split()[0]} {counts[s]}" for s in ORDER if counts[s]) or "ไม่พบประเด็น"
    comments = [{"path": f["path"], "line": f["line"], "side": "RIGHT", "body": comment_body(f)}
                for f in findings if f.get("path") and isinstance(f.get("line"), int)]
    body = ["## สรุปรีวิว", "", r.get("summary", "").strip(), "", f"พบ: {tally}", "",
            f"**สรุป:** {VERDICT.get(r.get('verdict'), r.get('verdict', ''))}"]
    json.dump({"repo": a.repo, "pr": r["pr"], "event": "COMMENT", "body": "\n".join(body), "comments": comments},
              open(a.out, "w"), ensure_ascii=False, indent=1)
    print(f"#{r['pr']}: {len(comments)} inline comment(s), verdict {r.get('verdict')} → {a.out}")


if __name__ == "__main__":
    main()
