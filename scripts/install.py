#!/usr/bin/env python3
"""Install (or remove) agent-cost on this machine.

    python3 install.py                 # both agents, default ledger dir
    python3 install.py --dir ~/agent-cost --no-prompts
    python3 install.py --uninstall

What it does, for each agent whose config dir exists (~/.claude, ~/.codebuddy):

  1. appends a `Stop` hook that runs capture.py — existing hooks are kept,
     because Stop is an array and other tools (pet, statusline…) live there too
  2. links this skill into that agent's skills/ dir so `/agent-cost` resolves
  3. writes ~/.config/agent-cost/config.json so the hook and the report agree
     on where the ledger lives

Idempotent: running it twice changes nothing the second time. Every settings
file is backed up next to itself before it is touched.
"""
import argparse, datetime, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SKILL_NAME = os.path.basename(SKILL_DIR)
CAPTURE = os.path.join(HERE, "capture.py")
CONFIG = os.path.expanduser("~/.config/agent-cost/config.json")
DEFAULT_LEDGER_DIR = "~/.agent-cost"

AGENTS = {
    "claude": os.path.expanduser("~/.claude"),
    "codebuddy": os.path.expanduser("~/.codebuddy"),
}


def hook_command():
    # Absolute path: a hook runs with the agent's cwd, not the skill's.
    return f"{sys.executable} {CAPTURE}"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def backup(path):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-agentcost-{stamp}"
    shutil.copy2(path, dst)
    return dst


def is_ours(entry):
    cmd = entry.get("command") or ""
    return "agent-cost" in cmd and "capture.py" in cmd


def patch_settings(agent_home, remove, dry):
    path = os.path.join(agent_home, "settings.json")
    settings = load_json(path, {})
    hooks = settings.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])

    present = any(is_ours(h) for grp in stop for h in grp.get("hooks", []))

    if remove:
        if not present:
            return "not installed"
        for grp in stop:
            grp["hooks"] = [h for h in grp.get("hooks", []) if not is_ours(h)]
        hooks["Stop"] = [g for g in stop if g.get("hooks")]
        if not hooks["Stop"]:
            hooks.pop("Stop")
        if not hooks:
            settings.pop("hooks")
        action = "hook removed"
    else:
        if present:
            return "hook already there"
        stop.append({"hooks": [{"type": "command", "command": hook_command()}]})
        action = "hook added"

    if dry:
        return action + " (dry-run, nothing written)"
    if os.path.exists(path):
        backup(path)
    os.makedirs(agent_home, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return action


def link_skill(agent_home, remove, dry):
    skills = os.path.join(agent_home, "skills")
    link = os.path.join(skills, SKILL_NAME)

    if os.path.realpath(link) == os.path.realpath(SKILL_DIR) and not remove:
        return "skill already linked"
    if remove:
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(SKILL_DIR):
            if not dry:
                os.unlink(link)
            return "skill unlinked"
        return "skill link left alone"
    if os.path.exists(link):
        return f"skill NOT linked — {link} already exists"
    if dry:
        return "would link skill"
    os.makedirs(skills, exist_ok=True)
    os.symlink(SKILL_DIR, link)
    return "skill linked"


def write_config(dirpath, prompts, dry):
    cfg = load_json(CONFIG, {})
    cfg["dir"] = dirpath
    cfg["record_prompts"] = prompts
    if dry:
        return f"would write {CONFIG}"
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.makedirs(os.path.expanduser(dirpath), exist_ok=True)
    return f"config → {CONFIG}"


def ask(default_prompts, quiet=False):
    """Let whoever owns the machine say where their ledger goes.

    The ledger holds what they asked their agent and which files it touched, so
    the location is theirs to pick — not something an installer should assume.
    Falls back to the defaults when there is no terminal to ask in (CI, piped).
    """
    default_dir = DEFAULT_LEDGER_DIR
    if quiet or not sys.stdin.isatty():
        return default_dir, default_prompts

    print("เก็บ ledger.jsonl ไว้ที่ไหนดี?")
    print(f"  Enter เฉย ๆ = {default_dir}   (พิมพ์ path เองได้ เช่น ~/notes/agent-cost)")
    ans = input("  path: ").strip()
    dirpath = ans or default_dir

    existing = os.path.join(os.path.expanduser(dirpath), "ledger.jsonl")
    if os.path.exists(existing):
        print(f"  (มี ledger เดิมอยู่แล้วที่นี่ — จะเขียนต่อท้ายไฟล์เดิม ไม่ทับ)")

    print("\nบันทึก prompt ที่สั่งไปด้วยไหม? (ช่วยให้อ่านรายงานรู้เรื่องว่ารอบไหนคืองานอะไร)")
    ans = input("  [Y/n]: ").strip().lower()
    prompts = not ans.startswith("n")
    print()
    return dirpath, prompts


def main():
    ap = argparse.ArgumentParser(description="install agent-cost hooks")
    ap.add_argument("--dir", default=None,
                    help="where ledger.jsonl lives (asked interactively; default ~/.agent-cost)")
    ap.add_argument("--no-prompts", action="store_true",
                    help="log numbers only — do not record what was asked")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="take the defaults, ask nothing")
    ap.add_argument("--only", choices=sorted(AGENTS), action="append",
                    help="install for one agent only (repeatable)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = args.only or [a for a, home in AGENTS.items() if os.path.isdir(home)]
    if not targets:
        print("ไม่เจอ ~/.claude หรือ ~/.codebuddy — ยังไม่ได้ลง agent สักตัว?")
        return 1

    print(f"skill:   {SKILL_DIR}")
    print(f"capture: {CAPTURE}\n")

    if not args.uninstall:
        dirpath, prompts = args.dir, not args.no_prompts
        if dirpath is None:
            dirpath, prompts = ask(prompts, quiet=args.yes)
        print(" config:", write_config(dirpath, prompts, args.dry_run))

    for agent in targets:
        home = AGENTS[agent]
        if not os.path.isdir(home) and args.uninstall:
            continue
        print(f"\n[{agent}]")
        print("  settings:", patch_settings(home, args.uninstall, args.dry_run))
        print("  skills:  ", link_skill(home, args.uninstall, args.dry_run))

    if args.uninstall:
        print("\nถอดออกแล้ว — ledger เดิมยังอยู่ ลบเองได้ถ้าไม่ใช้")
        return 0

    print(f"""
เสร็จแล้ว — เปิด session ใหม่ของ agent แล้วมันจะเริ่มเก็บเอง

เช็คว่าเวิร์ค:
  T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
  echo '{{"session_id":"test","transcript_path":"'$T'","cwd":"'$PWD'"}}' \\
    | AGENT_COST_DEBUG=1 python3 {CAPTURE}

ดูรายงาน:
  python3 {os.path.join(HERE, 'report.py')} --compare""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
