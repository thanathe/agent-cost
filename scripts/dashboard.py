#!/usr/bin/env python3
"""Live cost dashboard on localhost — reads the ledger, the page refreshes itself.

    python3 dashboard.py                 # http://127.0.0.1:8791
    python3 dashboard.py --port 9000 --open

The page polls /api/ledger every few seconds with an ETag (file size + mtime),
so a finished turn shows up within seconds and an idle poll costs a 304.

Local only on purpose: the ledger holds prompts and file paths. The server binds
127.0.0.1 and refuses any Host header that isn't localhost, so a web page can't
reach it through DNS rebinding.
"""
import argparse, json, os, sys, threading, time, webbrowser
from urllib.parse import parse_qs, quote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_paths import ledger_dir
import ide_sync
import categories
from codex_capture import CodexCollector
import export

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "dashboard.html")
KEEP = ("turn_key", "agent", "session_id", "ts", "repo", "credit", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "elapsed_sec", "model", "n_tool_calls",
        "via", "n_subagents", "subagent_tokens", "source",
        "n_tool_errors", "n_api_errors", "error", "resumed")


_ide = {"at": 0.0, "lock": threading.Lock()}


def sync_ide():
    """CodeBuddy IDE has no Stop hook — fold its on-disk history in, at most every 3 s."""
    if not _ide["lock"].acquire(blocking=False):
        return
    try:
        if time.time() - _ide["at"] >= 3:
            _ide["at"] = time.time()
            ide_sync.sync()
    except Exception as e:
        print("ide sync failed:", e, file=sys.stderr, flush=True)
    finally:
        _ide["lock"].release()


def paths():
    d = ledger_dir()
    return os.path.join(d, "ledger.jsonl"), os.path.join(d, "pricing.json"), os.path.join(d, "codex-ledger.jsonl")


def etag():
    parts = []
    for p in paths():
        try:
            st = os.stat(p)
            parts.append(f"{st.st_size}-{int(st.st_mtime_ns)}")
        except OSError:
            parts.append("0")
    return '"' + ".".join(parts) + '"'


def snapshot():
    ledger, pricing_path, codex_ledger = paths()
    full = []
    for source in (ledger, codex_ledger):
        try:
            with open(source, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(r, dict):
                        full.append(r)
        except OSError:
            pass
    # Classify here, on the whole row: the page only gets a slim copy (no tool names, no file
    # paths, a clipped prompt), so it can't tell a coding turn from "other" by itself.
    cats = categories.assign(full)
    rows = []
    for r in full:
        slim = {k: r.get(k) for k in KEEP}
        slim["prompt"] = (r.get("prompt") or "")[:160]
        slim["category"] = cats.get(r.get("turn_key") or id(r), "other")
        rows.append(slim)
    try:
        with open(pricing_path, encoding="utf-8") as f:
            pricing = json.load(f)
    except Exception:
        pricing = {}
    return {"ledger": ledger, "rows": rows, "pricing": pricing, "categories": categories.config()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden")
        path = self.path.split("?", 1)[0]
        if path == "/":
            try:
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, b"dashboard.html missing")
        if path == "/api/ledger":
            sync_ide()
            tag = etag()
            if self.headers.get("If-None-Match") == tag:
                return self._send(304, headers={"ETag": tag})
            body = json.dumps(snapshot(), ensure_ascii=False).encode()
            return self._send(200, body, "application/json; charset=utf-8", {"ETag": tag})
        if path == "/api/export":
            # A download of numbers only (no prompts/paths) for the team roll-up — see export.py.
            q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            try:
                num = lambda k: (lambda v: float(v) if v not in (None, "") else None)((q.get(k) or [None])[0])
                basis = (q.get("basis") or [""])[0]
                days = int(basis) if basis.isdigit() else None
                ui = {"claude": {"usd_per_month": num("claude"), "days_per_month": days},
                      "codex": {"usd_per_month": num("codex"), "days_per_month": days},
                      "codebuddy": {"usd_per_credit": num("credit")}}
                fname, text, _ = export.build(name=(q.get("name") or [None])[0],
                                              hide_repos=(q.get("hide") or ["0"])[0] == "1",
                                              auto_refresh=True, ui_plans=ui)
            except Exception as e:
                return self._send(500, f"export failed: {e}".encode())
            return self._send(200, text.encode(), "application/x-ndjson; charset=utf-8",
                              {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})
        return self._send(404, b"not found")


def main():
    ap = argparse.ArgumentParser(description="live agent-cost dashboard on localhost")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AGENT_COST_PORT", 8791)))
    ap.add_argument("--open", action="store_true", help="open the browser")
    args = ap.parse_args()
    collector = CodexCollector()
    stopped = threading.Event()
    def collect_codex():
        while not stopped.is_set():
            try:
                collector.sync()
            except Exception as exc:
                print("codex sync failed:", exc, file=sys.stderr, flush=True)
            stopped.wait(10)
    threading.Thread(target=collect_codex, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"agent-cost dashboard → {url}   (ledger: {paths()[0]})", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        srv.server_close()


if __name__ == "__main__":
    main()
