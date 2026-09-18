#!/usr/bin/env python3
"""Score PR-review benchmark runs.

    score-review.py <results-dir> --src <repo>            mechanical checks per review
    score-review.py <results-dir> --src <repo> --pool     + pool findings per PR for verification
    score-review.py <results-dir> --verdicts verdicts.json  final quality / time / cost table

Mechanical: does each finding cite a file and line that exist at the pinned commit, and does the
line fall inside the change under review (origin/sit...sha)? A finding that points outside the
change is not necessarily wrong, but it cannot be anchored as an inline PR comment.

Pooling: findings from all arms on the same PR are grouped when they cite the same file within a
few lines, so one real issue raised by three arms is verified once. The grouping is a starting
point — the verifier merges or splits groups and records a verdict per group.
"""
import argparse, collections, json, os, re, subprocess, sys

SEV_WEIGHT = {"blocker": 5, "major": 3, "minor": 1, "nit": 0}
NEAR = 8   # lines


def git(src, *args):
    return subprocess.run(["git", "-C", src, *args], capture_output=True, text=True).stdout


def changed_lines(src, sha):
    """{path: set(new-side line numbers)} for origin/sit...sha."""
    out, path, n = collections.defaultdict(set), None, 0
    for line in git(src, "diff", "-U0", f"origin/sit...{sha}").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m and path:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out[path].update(range(start, start + max(count, 1)))
    return out


def file_len(src, sha, path, cache={}):
    key = (sha, path)
    if key not in cache:
        r = subprocess.run(["git", "-C", src, "show", f"{sha}:{path}"], capture_output=True, text=True)
        cache[key] = r.stdout.count("\n") + 1 if r.returncode == 0 else None
    return cache[key]


def load_runs(d):
    rows = [json.loads(l) for l in open(os.path.join(d, "results.jsonl")) if l.strip()]
    for r in rows:
        f = os.path.join(d, f"{r['agent']}-{r['pr']}.review.json")
        r["review"] = json.load(open(f)) if os.path.exists(f) else None
    return rows


def mechanical(rows, src):
    hunks = {}
    for r in rows:
        fs = (r["review"] or {}).get("findings") or []
        if r.get("sha") and r["sha"] not in hunks:
            hunks[r["sha"]] = changed_lines(src, r["sha"])
        h = hunks.get(r.get("sha"), {})
        m = collections.Counter()
        for f in fs:
            sev = str(f.get("severity", "")).lower()
            m[sev] += 1
            path, line = f.get("path") or "", f.get("line")
            n = file_len(src, r["sha"], path) if path else None
            if n is None:
                m["bad_path"] += 1
            elif not isinstance(line, int) or not (1 <= line <= n):
                m["bad_line"] += 1
            elif line in h.get(path, set()):
                m["in_change"] += 1
            else:
                m["outside_change"] += 1
        r["mech"] = {"findings": len(fs), **m}
    return rows


def pool(rows):
    """Group findings per PR by file + nearby line. Returns {pr: [group,...]}."""
    groups = collections.defaultdict(list)
    for r in rows:
        for i, f in enumerate((r["review"] or {}).get("findings") or []):
            fid = f"{r['agent']}#{i}"
            item = {"id": fid, "agent": r["agent"], **f}
            placed = False
            for g in groups[r["pr"]]:
                if g["path"] == f.get("path") and isinstance(f.get("line"), int) and \
                        any(abs(f["line"] - x["line"]) <= NEAR for x in g["items"] if isinstance(x.get("line"), int)):
                    g["items"].append(item); placed = True; break
            if not placed:
                groups[r["pr"]].append({"path": f.get("path"), "items": [item]})
    for pr, gs in groups.items():
        for k, g in enumerate(gs, 1):
            g["group"] = f"{pr}-G{k:02d}"
            g["agents"] = sorted({x["agent"] for x in g["items"]})
    return groups


def final(rows, verdicts, pricing):
    """verdicts: {finding_id or group: {"real": true/false, "severity": "..."}} keyed by '<pr>:<agent>#<i>'."""
    usd_per_credit = ((pricing.get("_plans") or {}).get("codebuddy") or {}).get("usd_per_credit") or 0
    arms = collections.defaultdict(lambda: {"raised": 0, "real": 0, "fp": 0, "score": 0, "wall": [], "turns": [],
                                            "credits": 0.0, "usd": 0.0, "runs": 0, "failed": 0, "real_ids": set()})
    all_real = collections.defaultdict(set)   # pr -> set(group ids that are real)
    for key, v in verdicts.items():
        if v.get("real"):
            all_real[key.split(":")[0]].add(v["group"])
    for r in rows:
        a = arms[r["agent"]]
        a["runs"] += 1
        if not r.get("valid_review"):
            a["failed"] += 1
        a["wall"].append(r.get("wall_s") or 0)
        a["turns"].append(r.get("turns") or 0)
        a["credits"] += r.get("credits") or 0
        a["usd"] += (r.get("credits") or 0) * usd_per_credit if r["cli"] == "codebuddy" else (r.get("cost_usd") or 0)
        for i, f in enumerate((r["review"] or {}).get("findings") or []):
            v = verdicts.get(f"{r['pr']}:{r['agent']}#{i}")
            if not v:
                continue
            a["raised"] += 1
            if v.get("real"):
                a["real"] += 1
                a["real_ids"].add((str(r["pr"]), v["group"]))
                a["score"] += SEV_WEIGHT.get(v.get("severity", "minor"), 1)
            else:
                a["fp"] += 1
                a["score"] -= 1
    total_real = sum(len(s) for s in all_real.values())
    out = {}
    for name, a in arms.items():
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0
        caught = len(a["real_ids"])
        out[name] = {
            "runs": a["runs"], "failed": a["failed"], "raised": a["raised"], "real": a["real"], "false_pos": a["fp"],
            "precision": round(a["real"] / a["raised"], 2) if a["raised"] else None,
            "recall": round(caught / total_real, 2) if total_real else None,
            "score": a["score"], "median_wall_s": med(a["wall"]), "median_turns": med(a["turns"]),
            "credits": round(a["credits"], 2), "usd": round(a["usd"], 3),
            "usd_per_real_finding": round(a["usd"] / caught, 3) if caught else None,
        }
    return out, total_real


def default_pricing():
    """pricing.json next to the agent-cost ledger, found the same way the rest of the skill does."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "scripts"))
    try:
        from cost_paths import ledger_dir
        return os.path.join(ledger_dir(), "pricing.json")
    except Exception:
        return os.path.expanduser("~/.agent-cost/pricing.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--src")
    ap.add_argument("--pool", action="store_true")
    ap.add_argument("--verdicts")
    ap.add_argument("--pricing", default=default_pricing())
    a = ap.parse_args()
    rows = load_runs(a.dir)
    if a.src:
        mechanical(rows, a.src)
        print(f"{'arm':12} {'pr':>4} {'ok':>3} {'wall':>6} {'turns':>5} {'credits':>8} {'usd':>7}  findings")
        for r in rows:
            m = r.get("mech", {})
            sev = " ".join(f"{k[0].upper()}{m[k]}" for k in ("blocker", "major", "minor", "nit") if m.get(k))
            print(f"{r['agent']:12} {r['pr']:>4} {'✓' if r.get('valid_review') else '✗':>3} {r.get('wall_s', 0):>5}s "
                  f"{r.get('turns') or 0:>5} {r.get('credits') or 0:>8.2f} {r.get('cost_usd') or 0:>7.3f}  "
                  f"{m.get('findings', 0)} [{sev}] in-change {m.get('in_change', 0)} · outside {m.get('outside_change', 0)}"
                  f" · bad {m.get('bad_path', 0) + m.get('bad_line', 0)}")
    if a.pool:
        groups = pool(rows)
        json.dump(groups, open(os.path.join(a.dir, "pool.json"), "w"), ensure_ascii=False, indent=1)
        print(f"\npooled → {os.path.join(a.dir, 'pool.json')}: " +
              ", ".join(f"#{pr} {len(gs)} groups" for pr, gs in groups.items()))
    if a.verdicts:
        pricing = json.load(open(a.pricing)) if os.path.exists(a.pricing) else {}
        table, total = final(rows, json.load(open(a.verdicts)), pricing)
        print(f"\nreal issues found by anyone: {total}")
        print(json.dumps(table, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
