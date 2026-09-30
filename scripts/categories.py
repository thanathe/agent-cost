#!/usr/bin/env python3
"""What kind of work a turn was — one rough bucketing shared by report.py and the dashboard.

Readable, rule-based classification: prompt terms first, then recognizable infrastructure file
paths, then editing tools and source file extensions. Anything still undecided (a short "ok, go
ahead") inherits whatever that session was already doing.

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
    ("environment", "DevOps · Env / Config",
                   r"\.env(?:\.[\w-]+)?|environment variables?|env vars?"
                   r"|configmaps?|config map|runtime config|environment config|configuration variable"
                   r"|secret manager|secrets? store|vault secret|parameter store|ssm parameter"
                   r"|(?:edit|update|modify|change|set|configure|check|inspect|review).{0,20}\benv(?:ironment)?\b"
                   r"|(?:แก้|ปรับ|ตั้งค่า|ตรวจสอบ|เช็ก|เช็ค|ดู).{0,16}\benv(?:ironment)?\b"),
    ("reliability", "DevOps · Monitoring / SRE",
                    r"\bsre\b|observability|monitor(?:ing)?|prometheus|grafana|datadog|cloudwatch|new relic|splunk"
                    r"|elastic(?:search)?|\belk\b|loki|opentelemetry|\botel\b|logging|logs?|metrics|tracing|traces?"
                    r"|\bslo\b|\bsli\b|alert(?:ing)?|incident|on.?call|uptime|health check|disaster recovery|\bdr\b"),
    ("cloud_resources", "DevOps · Cloud Resource",
                         r"\bresources?\b|cloud resource|resource group|resource inventory|resource status|resource usage"
                         r"|resource list|resource check|resource inspection|ทรัพยากร"
                         r"|\b(?:ec2|s3|rds|ecs|eks|gke|aks|vpc|iam|lambda|cloudformation)\b"),
    ("cicd", "DevOps · CI/CD",
             r"\bci\s*/\s*cd\b|\bcicd\b|continuous integration|continuous delivery|continuous deployment"
             r"|pipeline|github actions|gitlab ci|jenkins|buildkite|circleci|teamcity|azure pipelines?"
             r"|argo ?cd|flux ?cd|gitops|build workflow|release automation|runner"),
    ("deploy", "DevOps · Deploy / Release",
               r"deploy(?:ment)?|release|rollout|rollback|blue.?green|canary|promotion to (?:sit|staging|prod)"
               r"|\b(?:sit|staging|production|prod) environment\b|\bprod\b|\bsit\b|migrat(?:e|ion)"),
    ("platform", "DevOps · IaC / Platform",
                r"\bdevops\b|\binfra(?:structure)?\b|infrastructure as code|\biac\b|\bcloud\b|\baws\b|\bgcp\b|\bazure\b"
                r"|\bterraform\b|\bopentofu\b|\bansible\b|\bpuppet\b|\bchef\b|\bkubernetes\b|\bk8s\b|\bhelm\b"
                r"|docker|containers?|\bnetwork(?:ing)?\b|\bdns\b|load balancers?|ingress|firewalls?|\biam\b"
                r"|\bvault\b|certificates?|\bssl\b|\btls\b|\bvpc\b|subnets?|security groups?"
                r"|digitalocean|cloudflare|\bec2\b|\beks\b|\baks\b|\bgke\b|\brds\b|\bvm\b|virtual machines?"
                r"|autoscal(?:e|ing)|backups?|storage"),
    # Keep the old key so exported ledgers with a precomputed "ops" category still render.
    ("ops", "DevOps · Git / Operations",
            r"\bgit\b|\bmerge\b|\bpush\b|\btag\b|cherry.?pick|rebase|branch protection|\bprod\b|\bsit\b"),
    ("debug", "แก้บั๊ก", r"bug|debug|error|exception|\bfix\b|พัง|ไม่ขึ้น|ไม่ทำงาน|แก้ปัญหา|fail"),
    ("design", "design / UI", r"figma|design|\bui\b|\bux\b|หน้าจอ|layout|\bcss\b|สไตล์|ปุ่ม|ธีม|theme|icon"),
    ("docs", "เอกสาร / สรุป", r"readme|document|\bdocs?\b|สรุป|report|wiki|เขียนเอกสาร|changelog|post-?mortem"),
    # Messages people send, not mail infrastructure.
    ("admin", "งานออฟฟิศ", r"meegle|ลงเวลา|time ?record|timelog|tech task|lark|jira|ticket"
                           r"|ส่ง ?(อี)?เมล|ตอบ ?(อี)?เมล|ร่าง ?(อี)?เมล|inbox"),
    ("data", "ข้อมูล / query", r"\bsql\b|query|ดึงข้อมูล|export|dataset|วิเคราะห์ข้อมูล|report ข้อมูล"),
]
LABELS = dict([(k, l) for k, l, _ in CATS] + [("code", "เขียนโค้ด"), ("other", "อื่น ๆ")])
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "replace_in_file", "write_to_file"}
CODE_EXT = r"\.(ts|tsx|js|jsx|py|go|java|rb|php|rs|c|cpp|cs|sql|sh|proto|astro|vue|swift|kt)$"

_COMPILED = [(k, re.compile(p, re.I)) for k, _, p in CATS]
_CODE = re.compile(CODE_EXT, re.I)
_CODE_REQUEST = re.compile(r"\b(?:implement|write|edit|modify|refactor|add|remove|create|build|update|change|convert|upgrade|replace|delete|generate|develop|coding|code)\b|เขียน|แก้ไข|ปรับปรุง|เพิ่ม|ลบ|สร้าง|เปลี่ยน|อัปเดต|อัพเดต", re.I)
_ENV_FILE = re.compile(r"(^|/)\.env(?:\.[^/]+)?$|(^|/)(?:configmaps?|secrets?|environment|env-vars?)(?:[-_.][^/]+)?\.(?:ya?ml|json|toml)$", re.I)
_CICD_FILE = re.compile(r"(^|/)\.github/workflows/|(^|/)(?:\.gitlab-ci\.ya?ml|Jenkinsfile|azure-pipelines\.ya?ml)$|(^|/)\.circleci/config\.ya?ml$", re.I)
_RELIABILITY_PATH = re.compile(r"(^|/)(?:monitoring|observability|prometheus|grafana|alerts?)(/|$)", re.I)
_PLATFORM_FILE = re.compile(r"(^|/)(?:terraform|infra|infrastructure|k8s|kubernetes|helm|ansible)(/|$)|(^|/)(?:Dockerfile|docker-compose(?:\.[^/]+)?|Chart\.ya?ml|kustomization\.ya?ml)$|\.(?:tf|tfvars|hcl)$", re.I)


def own_category(row):
    """The category this turn declares by itself, or None when it doesn't say."""
    prompt = row.get("prompt") or ""
    for key, pattern in _COMPILED:
        if pattern.search(prompt):
            return key
    for path in row.get("files_touched") or []:
        path = str(path).replace("\\", "/")
        for pattern, key in ((_ENV_FILE, "environment"), (_CICD_FILE, "cicd"),
                             (_RELIABILITY_PATH, "reliability"), (_PLATFORM_FILE, "platform")):
            if pattern.search(path):
                return key
    if _CODE_REQUEST.search(prompt):
        return "code"
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
