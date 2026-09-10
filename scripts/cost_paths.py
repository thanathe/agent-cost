#!/usr/bin/env python3
"""Where the ledger lives — one resolver shared by capture.py and report.py.

Order:  $AGENT_COST_DIR  →  ~/.config/agent-cost/config.json ("dir")  →  ~/.agent-cost

Keeping the answer in one place means the hook that writes and the report that
reads can never disagree about which file is the source of truth. The default is
deliberately machine-neutral so a teammate who just unzips the skill gets a
working ledger without editing anything.
"""
import json, os

CONFIG = os.path.expanduser("~/.config/agent-cost/config.json")
DEFAULT_DIR = "~/.agent-cost"


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ledger_dir(cfg=None):
    env = os.environ.get("AGENT_COST_DIR")
    if env:
        return os.path.expanduser(env)
    if cfg is None:
        cfg = load_config()
    return os.path.expanduser(cfg.get("dir") or DEFAULT_DIR)


def record_prompts(cfg=None):
    """False → the ledger keeps the numbers but not what was asked."""
    if os.environ.get("AGENT_COST_NO_PROMPT") == "1":
        return False
    if cfg is None:
        cfg = load_config()
    return bool(cfg.get("record_prompts", True))
