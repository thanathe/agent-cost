---
name: agent-cost
description: Track and compare what CodeBuddy vs Claude Code actually cost per turn — credits, tokens, elapsed time, tools used, and the task that was asked. A Stop hook appends every finished turn to an append-only ledger; a report command renders it to markdown in the wiki. Use when the user asks about agent cost / credit ใช้ไปเท่าไหร่ / เทียบ codebuddy กับ claude / รายงาน cost / ledger / "ใช้ credit กับอะไรบ้าง", or wants to install or debug the auto-capture hook.
---

# Agent Cost

เก็บว่า **แต่ละรอบที่สั่ง agent ไป กินไปเท่าไหร่** — credit, token, เวลา, tool, ไฟล์ที่แตะ, งานที่สั่ง
แล้วเอามาเทียบ CodeBuddy vs Claude ได้ตรง ๆ เพราะเก็บด้วยสคริปต์ตัวเดียวกัน นิยามเดียวกัน

## เก็บอะไรได้จริงบ้าง — อ่านก่อนใช้

| | CodeBuddy CLI (`cbc`) | CodeBuddy IDE | Claude Code |
|---|---|---|---|
| ข้อมูลบนดิสก์ | `~/.codebuddy/projects/*/*.jsonl` | `CodeBuddyExtension/Data/*/CodeBuddyIDE/*/history/**/index.json` | `~/.claude/projects/*/*.jsonl` |
| **credit** | ✅ `providerData.rawUsage.credit` | ✅ `requests[].usage.credit` | ❌ ไม่มี field นี้ |
| token / เวลา / tool / prompt | ✅ | ✅ | ✅ |
| เก็บยังไง | `Stop` hook | `ide_sync.py` (dashboard เรียกให้เอง) | `Stop` hook |
| ย้อนหลังก่อนติดตั้ง | `backfill.py` | `backfill.py` | `backfill.py` |

**CodeBuddy IDE ไม่ยิง hook** แต่เก็บ usage ต่อ request ไว้ใน history ของ extension
(`~/Library/Application Support` บน macOS, `%APPDATA%` บน Windows, `~/.config` บน Linux — ชี้เองได้ด้วย `$CODEBUDDY_APPDATA`)
`ide_sync.py` อ่านแล้วลง ledger เป็น `agent: "codebuddy"` + `source: "ide"` — รวมยอดกับ CLI ได้เลย
ถ้าต้องแยกให้กรอง `source`

⚠️ **Claude ไม่มี credit** — เทียบเป็นเงินได้ต่อเมื่อเติม `pricing.json` เอง ถ้าไม่เติม report จะโชว์ `—`
ไม่เดาราคาให้ ตัวเลขที่มั่วมาแพงกว่าช่องว่าง

**ใช้แพ็กเหมา (Pro / Max)** → ราคา API ไม่ใช่เงินที่จ่ายจริง ใส่ `_plans` แล้ว report จะเพิ่มหัวข้อ "จ่ายจริงเท่าไหร่"
ที่เฉลี่ยค่าแพ็กตามวันที่ ledger มีข้อมูล (ราคา API เหลือไว้ดูว่าคุ้มกว่ากี่เท่า)
ไม่ใส่ `days_per_month` = หารทุกวันในปฏิทิน · ใส่ 20 = คิดเฉพาะวันที่ใช้ วันละ ค่าแพ็ก ÷ 20:

```json
{"_plans": {"claude": {"name": "Max 5x", "usd_per_month": 100, "days_per_month": 20},
            "codebuddy": {"usd_per_credit": 0.01}},
 "claude-opus-5": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25}}
```

## โครงสร้าง

```
<LEDGER_DIR>/                    ← เจ้าของเครื่องเลือกเองตอนติดตั้ง (ดีฟอลต์ ~/.agent-cost)
├── ledger.jsonl     ← ข้อมูลดิบ append-only (hook เขียน) = source of truth
├── pricing.json     ← ราคา USD ต่อ 1M token ต่อ model + `_plans` แพ็กเหมา (ออปชัน กรอกเอง)
└── 2026-09.md       ← report ที่ render จาก ledger (สร้างใหม่ได้เสมอ)
```

`<LEDGER_DIR>` มาจาก (ตามลำดับ): `$AGENT_COST_DIR` → `~/.config/agent-cost/config.json` → `~/.agent-cost`
ทั้ง `capture.py` และ `report.py` อ่านจาก `cost_paths.py` ตัวเดียวกัน จะได้ไม่มีทางชี้คนละไฟล์
เครื่องนี้ตั้งไว้ที่ไหน เช็คด้วย `cat ~/.config/agent-cost/config.json`

**ทำไมไม่ให้ hook เขียน .md ตรง ๆ:** หลาย session จบพร้อมกันแล้วเขียนทับกัน และ markdown รวมยอดใหม่ไม่ได้
JSONL ต่อท้ายปลอดภัยกว่า แล้ว render .md ทีหลังกี่รอบก็ได้ ไม่มีวันหลุดจากข้อมูลดิบ

## ใช้งาน

```bash
python3 ~/.claude/skills/agent-cost/scripts/report.py                      # เดือนนี้
python3 ~/.claude/skills/agent-cost/scripts/report.py --compare            # เทียบ 2 agent + แยก repo
python3 ~/.claude/skills/agent-cost/scripts/report.py --month 2026-09 --write   # เขียนลง wiki
python3 ~/.claude/skills/agent-cost/scripts/report.py --repo my-service  # เฉพาะ repo
python3 ~/.claude/skills/agent-cost/scripts/report.py --since 2026-09-01 --agent codebuddy
python3 ~/.claude/skills/agent-cost/scripts/report.py --ledger 'team/ledger-*.jsonl' --by-owner  # รวมของทั้งทีม
```

**dashboard สด (localhost):** อ่าน ledger + `pricing.json` แล้วหน้าเว็บเช็คใหม่ทุก 4 วิ รอบที่เพิ่งจบขึ้นเองไม่ต้อง refresh

```bash
python3 ~/.claude/skills/agent-cost/scripts/dashboard.py --open          # http://127.0.0.1:8791  (--port เปลี่ยนได้)
```

bind แค่ 127.0.0.1 และตอบเฉพาะ Host ที่เป็น localhost — ledger มี prompt กับ path ไฟล์ ไม่ควรเปิดออกนอกเครื่อง
ราคา credit / ค่าแพ็กที่พิมพ์ในหน้าเก็บใน browser ถ้าไม่พิมพ์จะใช้ `_plans` จาก `pricing.json`
ทุกครั้งที่หน้าเว็บ poll จะดึงรอบใหม่จาก CodeBuddy IDE ให้ด้วย (≤ ทุก 3 วิ) ไม่ต้องรัน `ide_sync.py` แยก

**ย้อนเก็บของเก่า (ก่อนติดตั้ง hook):** อ่าน transcript ของ Claude Code + CodeBuddy CLI และ history ของ CodeBuddy IDE
ที่อยู่ในเครื่องคนรัน — ใครลง skill แล้วรันคำสั่งเดียวกันก็ได้ของตัวเอง

```bash
python3 ~/.claude/skills/agent-cost/scripts/backfill.py --dry-run         # ดูก่อนว่าจะเพิ่มกี่รอบ
python3 ~/.claude/skills/agent-cost/scripts/backfill.py                   # เขียนจริง
python3 ~/.claude/skills/agent-cost/scripts/backfill.py --since 2026-08-01 --only claude   # claude | codebuddy | ide
```

ใช้ `build_record` ของ `capture.py` ตรง ๆ → `turn_key` กับนิยาม token เหมือนที่ hook เขียนเป๊ะ
รอบที่ hook จดไปแล้วข้าม รันซ้ำกี่ครั้งก็ไม่เพิ่ม · ข้ามรอบสุดท้ายของ transcript ที่เพิ่งถูกเขียนใน 10 นาที
(อาจยังทำงานอยู่ ให้ hook จดตอนจบ) · token ของ subagent รวมเข้ารอบแม่ให้เหมือน hook
`via` ดูจากประวัติไม่ได้ → รัน `capture.py --rebuild` ต่อ ถ้าอยากรู้ว่ารอบไหน Claude เป็นคนสั่ง CLI

`--ledger` รับ glob ของ ledger คนอื่นที่ขอมา ชื่อคนอ่านจากชื่อไฟล์ `ledger-<ชื่อ>.jsonl`
ไฟล์ซ้ำไม่นับซ้ำ (dedupe ด้วย `turn_key`) · `--owner <ชื่อ>` เจาะรายคน

`--write` เขียน `<เดือน>.md` ลง wiki — ที่เหลือพิมพ์ออก stdout เฉย ๆ

ถาม ad-hoc ให้ query `ledger.jsonl` ด้วย `jq` ตรง ๆ ไม่ต้องผ่าน report:

```bash
L=$(python3 -c 'import sys;sys.path.insert(0,"'"$HOME"'/.claude/skills/agent-cost/scripts");import cost_paths,os;print(os.path.join(cost_paths.ledger_dir(),"ledger.jsonl"))')
jq -s 'map(select(.agent=="codebuddy")) | {credit: (map(.credit//0)|add), รอบ: length}' "$L"
jq -r 'select(.credit and .credit > 5) | [.ts, .repo, .credit, .prompt] | @tsv' "$L"   # รอบที่แพงผิดปกติ
```

## ติดตั้ง auto-capture

Stop hook ยิงทุกครั้งที่ agent จบ 1 รอบ **ทั้งสองตัวใช้ hook schema เดียวกัน** สคริปต์เดียวจึงคุมได้ทั้งคู่
ไม่ต้องแก้ `settings.json` เอง — `install.py` ต่อท้ายให้ (ไม่ทับ hook เดิม) แล้วถามว่าจะเก็บ ledger ที่ไหน:

```bash
python3 ~/.claude/skills/agent-cost/scripts/install.py             # ถาม path + จะเก็บ prompt ไหม
python3 ~/.claude/skills/agent-cost/scripts/install.py --dry-run   # ดูก่อนว่าจะแตะอะไร
python3 ~/.claude/skills/agent-cost/scripts/install.py --uninstall # ถอด hook + symlink (ledger ไม่ลบ)
```

`--dir <path>` / `--no-prompts` / `-y` ข้ามคำถามได้ · `--only claude|codebuddy` ลงตัวเดียว

- ลงซ้ำได้ไม่พัง — เจอ hook ของตัวเองแล้วข้าม และ backup `settings.json` ทุกครั้งก่อนแตะ
- hook ที่มีอยู่แล้ว (เช่น gumarn-pet) อยู่ครบ — `Stop` เป็น array รับได้หลายตัว
- `capture.py` ออกด้วย exit 0 เสมอแม้พัง — hook ที่ error จะไปขัดจังหวะ agent
- เงียบเกินไปจนสงสัย → `AGENT_COST_DEBUG=1` แล้วดู stderr

## แจกให้คนอื่นลง

`README.md` ในโฟลเดอร์นี้เขียนไว้ให้คนที่ไม่เคยเห็น skill นี้อ่านเองได้ ส่งทั้งโฟลเดอร์ไปเลย:

```bash
tar -czf agent-cost.tar.gz -C ~/.claude/skills agent-cost      # แล้วส่งไฟล์นี้ให้เขา
```

ฝั่งคนรับ: แตกลง `~/.claude/skills/` แล้วรัน `install.py` — ไม่มี path ผูกกับเครื่องใครใน repo นี้
ledger เป็นของเครื่องใครเครื่องมัน **อย่า commit ledger / backup / report `.md` ขึ้น git** — ในนั้นมี prompt กับ path ไฟล์ของเจ้าตัว
ถ้าเลือก `LEDGER_DIR` อยู่ใน git repo (เช่น wiki) ให้ใส่ `.gitignore` ก่อน: `*.jsonl` `*.jsonl.*` `*.md` `pricing.json` ของโฟลเดอร์นั้น

เทสโดยไม่ต้องรอรอบจริง:

```bash
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "{\"session_id\":\"test\",\"transcript_path\":\"$T\",\"cwd\":\"$PWD\"}" \
  | AGENT_COST_DEBUG=1 python3 ~/.claude/skills/agent-cost/scripts/capture.py
```

## กติกาการนับ (สำคัญตอนตีความตัวเลข)

- **1 record = 1 รอบ** (ตั้งแต่ prompt ของคนจนจบ) ไม่ใช่ 1 API call — เรียก tool 40 ครั้งก็ยังนับเป็นรอบเดียว
  สิ่งที่ **ไม่นับ** เป็น prompt: tool result, skill body ที่ถูกแทรก (`isMeta`), echo ของคำสั่ง local
  (`/model` `/clear` `/usage` → `<local-command-stdout>`), `!bash` echo, `[Request interrupted by user]`
  เคยนับผิด: พิมพ์ `/model` ระหว่างรอบ → รอบถูกตัดกลาง credit ครึ่งแรกหาย + label กลายเป็น "Switch model to …"
- **token นิยามเดียวกันทั้งสองฝั่ง** (`usage_v: 2`): `input_tokens` = input ที่ไม่ได้มาจาก cache เท่านั้น ·
  `cache_read_tokens` / `cache_write_tokens` แยก · `total_tokens` = input + cache read + cache write + output
  CodeBuddy ส่ง `prompt_tokens` ที่รวม cache hit มาแล้ว เลยต้องหักออก ส่วน Claude แยกมาให้อยู่แล้ว
  ⚠️ ก่อน v2 `total_tokens` ของ Claude ไม่รวม cache → ดูน้อยกว่าจริงหลายร้อยเท่า
- **เปลี่ยน model กลาง session = cache miss** — call แรกหลัง `/model` อ่าน context ใหม่ทั้งก้อน
  (เจอจริง: 102k token cache ได้แค่ 11k → 24.84 credit ใน call เดียว)
- **CodeBuddy ยิง Stop ก่อนเขียนข้อความสุดท้าย** (ซึ่งมี credit ของมันเอง) → hook รอไฟล์นิ่ง ≤3 วิ ก่อนอ่าน
- **subagent ของ Claude** (Agent tool / workflow) ไม่ยิง Stop — capture รวม token จาก `<session>/subagents/*.jsonl`
  ที่ timestamp อยู่ในช่วงรอบนั้นเข้าไปในรอบแม่ แล้วบอกไว้ใน `n_subagents` `subagent_tokens` `subagent_models`
- **CLI ที่ Claude เรียก** (`cb.sh -p`, `claude -p`, `claude-9arm`) ยิง Stop ของตัวเองอยู่แล้ว เลยได้แถวแยก
  hook ดู env `CLAUDE_CODE_SESSION_ID` ที่ Claude ส่งต่อให้ process ลูก → ถ้าไม่ใช่ session ตัวเอง ใส่ `via: "claude"`
  + `parent_session` · ตอน `--rebuild` ไม่มี env ให้ดู เลยหา prompt ของแถวนั้นในคำสั่ง Bash ของ transcript Claude แทน
- **Claude Code ที่ชี้ไป model อื่น** (เช่น gateway qwen) → `agent: "claude-code:qwen"` ไม่ปนยอด `claude`
- **serena (MCP)** — ตรวจ tool ที่ชื่อมี `serena` (เช่น `mcp__serena__*`) แล้วเก็บ `n_serena_calls` + `serena_tools`
  ต่อรอบ · dashboard มี checkbox "เฉพาะรอบที่ใช้ serena" และ checkbox เลือกประเภท (CLI / IDE / Claude / Codex)
  ตัวเลขเป็นการ tag รอบของ agent แม่เท่านั้น ไม่ได้แยกค่าใช้จ่ายออกมา เพราะ serena ไม่ได้จ่าย credit ของตัวเอง
- **dedupe usage ตาม message id** — Claude เขียน assistant event ซ้ำ 3 รอบต่อ 1 ข้อความ
  (event ละ content block: thinking / text / tool_use) ถ้าบวกดื้อ ๆ token จะเกินจริง ~2-3 เท่า
  เคยเจอ 151 events → 67 ข้อความจริง
- **`turn_key`** = sha1(agent+session+turn index) กัน hook ยิงซ้ำแล้วนับซ้ำ
- **elapsed** = ผลรวมช่วงห่างระหว่าง event ที่เป็นงาน (ข้อความ / tool call / tool result) รวมเวลาที่รอ tool ด้วย
  → ไม่เท่ากับเวลา inference ล้วน · ช่วงเงียบเกิน 30 นาที (`IDLE_GAP_SEC`) ไม่นับ — `queue-operation` `attachment`
  โผล่ทีหลังได้เป็นวัน และ session ที่เปิดค้างแล้วมี notification / ตอบต่อวันถัดไป เคยทำให้รอบเดียวยาว 300 ชม.
- **`model`** = model ล่าสุดของรอบที่ไม่ใช่ `<synthetic>` (ข้อความ error/แจ้งเตือนที่ harness เขียนเอง ไม่ได้ทำงานจริง)
- **ledger เก่าหรือ capture รุ่นก่อน** → `python3 scripts/capture.py --rebuild` คำนวณทุกแถวใหม่จาก transcript
  (backup เป็น `ledger.jsonl.bak-*` ก่อนเสมอ) แถวที่เป็นชิ้นของรอบเดียวกันจะรวมเป็นแถวเดียว
  แถวที่ transcript หายไปแล้ว / กรอกมือ → แก้แค่นิยาม token ด้วยการคำนวณ
- **`prompt`** ถ้าเป็น slash command จะย่อเหลือชื่อคำสั่ง (`/pr-review <args>`) เพราะ transcript เก็บ
  SKILL.md มาทั้งก้อน เอามาเป็น label ไม่ได้
- **รอบที่ error ก็ถูกคิดเงิน** — 502/504 คือ gateway ล่ม *หลัง* โมเดลทำงานไปแล้ว token ถูกคิดไปแล้ว
  เก็บไว้ใน `n_api_errors` + `error` (ข้อความ), `n_tool_errors` (tool call ที่ fail) และ `resumed`
  (รอบ IDE ที่เริ่มด้วย "Please resume the unfinished tasks" = retry ต่อจากรอบที่ล่ม)
  ⚠️ IDE ไม่เขียน body ของ 502 ลงดิสก์ (Request ID เห็นแค่บนจอ) → ดูทางอ้อมจากรอบ `resumed` เท่านั้น
  · dashboard สรุปให้ว่า credit กี่ % หมดไปกับรอบพวกนี้
- รอบที่ไม่มี output token และไม่มี credit → ข้าม ไม่เขียนลง ledger

## CodeBuddy IDE — อ่านตัวเลขยังไง

- **1 request ใน history = 1 รอบ** (prompt + ทุก tool call ที่ agent ทำต่อจากนั้น) หน่วยเดียวกับ hook
  request ที่ `state` ยังไม่ `complete` ข้ามไว้ รอบหน้าค่อยเก็บ · request ที่กดยกเลิกกลางทางยังนับ (credit ถูกหักจริง)
- `inputTokens` ของ IDE รวม cache hit/write มาแล้ว (เหมือน `prompt_tokens` ของ CLI) → หักออกให้เหลือ input ล้วน
  `total_tokens` จึงนิยามเดียวกับแถวอื่น (`usage_v: 2`)
- `turn_key` = sha1(`codebuddy:ide:<request id>`) · `session_id` = conversation id
- `repo` — โฟลเดอร์ใน history คือ md5 ของ path workspace ยืนยันด้วยการ hash path ที่เจอใน
  `workspaceStorage` / ข้อความ ถ้าหาไม่เจอใช้ชื่อจากชื่อไฟล์ log ของ extension ไม่งั้นเป็น `?`
- `elapsed_sec` = ข้อความสุดท้ายของ request − `startedAt`
- request ที่ usage เป็น 0 ทั้งหมด: ไม่มีข้อความ assistant/tool → ข้าม (ไม่มีอะไรเกิดขึ้น) ·
  มีคำตอบแต่ไม่ถูกคิดเงิน หรือ model ขึ้นต้น `custom` → model ที่ตั้งเอง → `agent: "codebuddy-custom"`, `credit: null`
- `repo` ของ `~/CodeBuddy/<14 หลัก>` และ `~/CodeBuddy` เอง → `codebuddy-chat` · `automation-<14 หลัก>` → `codebuddy-automation`
- `ide_sync.py --resync` ลบแถว `source: "ide"` ทั้งหมด + ลบ `.ide_sync.json` แล้วอ่านใหม่ (backup ก่อน ถือ ledger lock)
- จำ mtime ของ `index.json` ไว้ใน `<LEDGER_DIR>/.ide_sync.json` — ไฟล์ไม่เปลี่ยนไม่อ่านซ้ำ
  ลบไฟล์นี้ได้ถ้าอยากให้สแกนใหม่หมด (ไม่นับซ้ำเพราะ dedupe ด้วย `turn_key`)

## กรอก session เอง (ไม่มีข้อมูลบนดิสก์)

ถ้าเจอ agent ที่ไม่มีข้อมูลบนดิสก์ให้อ่าน แต่อยากให้อยู่ในรายงานเดียวกัน ให้ต่อท้าย `ledger.jsonl` เอง — ก็อปเลขจาก UI:

```bash
python3 - <<'PY'
import json, os, datetime
rec = {
  "turn_key": "ide-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
  "agent": "codebuddy-ide", "session_id": "manual",
  "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
  "repo": "my-frontend",
  "prompt": "post pr review ให้หน่อย",
  "credit": 8.7, "total_tokens": 216348, "elapsed_sec": 33.5,
  "input_tokens": 0, "output_tokens": 0, "n_tool_calls": 0, "files_touched": [],
}
import sys; sys.path.insert(0, os.path.expanduser("~/.claude/skills/agent-cost/scripts"))
from cost_paths import ledger_dir
p = os.path.join(ledger_dir(), "ledger.jsonl")
os.makedirs(os.path.dirname(p), exist_ok=True)
open(p, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
PY
```

แยก `agent` เป็นชื่ออื่น (เช่น `codebuddy-ide`) ไว้ จะได้ไม่ปนกับตัวเลขที่วัดเองอัตโนมัติ — คนละวิธีเก็บ
ความน่าเชื่อถือคนละระดับ อย่าเอาไปรวมยอดเดียวกันโดยไม่บอก

## ตอนทำ report เทียบ agent

ตัวเลขดิบไม่พอตัดสิน — **cost ต่อรอบถูกกว่าไม่ได้แปลว่าคุ้มกว่า** ให้ดูคู่กับ:

- งานนั้นจบในรอบเดียวไหม หรือต้องสั่งแก้ซ้ำอีก 5 รอบ (นับ record ที่ prompt คล้ายกันใน repo เดียวกัน)
- ผลลัพธ์ผ่านเทส/รีวิวไหม — ledger ไม่รู้เรื่องคุณภาพ ต้องเอา PR/เทสมาประกอบเอง
- `n_tool_calls` สูงผิดปกติ = วนหาของไม่เจอ มักแปลว่า context ไม่พอตั้งแต่แรก

## Codex

ไม่มี hook — `scripts/codex_capture.py` อ่าน rollout ใน `$CODEX_HOME` (ดีฟอลต์ `~/.codex`) ลง `codex-ledger.jsonl` แยกไฟล์
dashboard ดึงให้ทุก 10 วิ · `report.py` อ่านรวมให้เอง (`--agent codex`) · ไม่มี credit · ลงเฉพาะรอบที่ `task_complete`
ใช้ `codex-ledger.lock` ของตัวเอง ไม่แย่ง `.ledger.lock` กับ Stop hook · `ts` เป็นเวลาท้องถิ่น
รายละเอียด + ข้อจำกัด: README หัวข้อ Codex
