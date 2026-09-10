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
| transcript บนดิสก์ | `~/.codebuddy/projects/**/*.jsonl` | ❌ | `~/.claude/projects/**/*.jsonl` |
| **credit** | ✅ `providerData.rawUsage.credit` | ❌ | ❌ ไม่มี field นี้ |
| token / เวลา / tool | ✅ | ❌ | ✅ |
| `Stop` hook | ✅ | ❌ | ✅ |

⚠️ **IDE เก็บอัตโนมัติไม่ได้** — `codebuddy-sessions.vscdb` มีแค่ metadata (title/status/เวลา) ตัวเลข
Credits/Tokens/Elapsed ที่ UI โชว์มาจาก API ไม่ได้ลงดิสก์ → ต้องกรอกมือ (ดูหัวข้อล่างสุด)

⚠️ **Claude ไม่มี credit** — เทียบเป็นเงินได้ต่อเมื่อเติม `pricing.json` เอง ถ้าไม่เติม report จะโชว์ `—`
ไม่เดาราคาให้ ตัวเลขที่มั่วมาแพงกว่าช่องว่าง

## โครงสร้าง

```
<LEDGER_DIR>/                    ← เจ้าของเครื่องเลือกเองตอนติดตั้ง (ดีฟอลต์ ~/.agent-cost)
├── ledger.jsonl     ← ข้อมูลดิบ append-only (hook เขียน) = source of truth
├── pricing.json     ← ราคา USD ต่อ 1M token ต่อ model (ออปชัน กรอกเอง)
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
ledger เป็นของเครื่องใครเครื่องมัน **อย่าเอา ledger ไป commit รวมกัน** — ในนั้นมี prompt กับ path ไฟล์ของเจ้าตัว

เทสโดยไม่ต้องรอรอบจริง:

```bash
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "{\"session_id\":\"test\",\"transcript_path\":\"$T\",\"cwd\":\"$PWD\"}" \
  | AGENT_COST_DEBUG=1 python3 ~/.claude/skills/agent-cost/scripts/capture.py
```

## กติกาการนับ (สำคัญตอนตีความตัวเลข)

- **1 record = 1 รอบ** (ตั้งแต่ prompt ของคนจนจบ) ไม่ใช่ 1 API call — เรียก tool 40 ครั้งก็ยังนับเป็นรอบเดียว
  tool result ไม่นับเป็น prompt ของคน
- **dedupe usage ตาม message id** — Claude เขียน assistant event ซ้ำ 3 รอบต่อ 1 ข้อความ
  (event ละ content block: thinking / text / tool_use) ถ้าบวกดื้อ ๆ token จะเกินจริง ~2-3 เท่า
  เคยเจอ 151 events → 67 ข้อความจริง
- **`turn_key`** = sha1(agent+session+turn index) กัน hook ยิงซ้ำแล้วนับซ้ำ
- **elapsed** = timestamp สุดท้าย − แรกของรอบนั้น รวมเวลาที่รอ tool ด้วย → ไม่เท่ากับเวลา inference ล้วน
- **`prompt`** ถ้าเป็น slash command จะย่อเหลือชื่อคำสั่ง (`/pr-review <args>`) เพราะ transcript เก็บ
  SKILL.md มาทั้งก้อน เอามาเป็น label ไม่ได้
- รอบที่ไม่มี output token และไม่มี credit → ข้าม ไม่เขียนลง ledger

## กรอก session จาก IDE เอง

IDE ดึงอัตโนมัติไม่ได้ ถ้าอยากให้อยู่ในรายงานเดียวกัน ให้ต่อท้าย `ledger.jsonl` เอง — ก็อปเลขจากใต้คำตอบใน UI:

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

แยก `agent` เป็น `codebuddy-ide` ไว้ จะได้ไม่ปนกับตัวเลข CLI ที่วัดเองอัตโนมัติ — คนละวิธีเก็บ
ความน่าเชื่อถือคนละระดับ อย่าเอาไปรวมยอดเดียวกันโดยไม่บอก

## ตอนทำ report เทียบ agent

ตัวเลขดิบไม่พอตัดสิน — **cost ต่อรอบถูกกว่าไม่ได้แปลว่าคุ้มกว่า** ให้ดูคู่กับ:

- งานนั้นจบในรอบเดียวไหม หรือต้องสั่งแก้ซ้ำอีก 5 รอบ (นับ record ที่ prompt คล้ายกันใน repo เดียวกัน)
- ผลลัพธ์ผ่านเทส/รีวิวไหม — ledger ไม่รู้เรื่องคุณภาพ ต้องเอา PR/เทสมาประกอบเอง
- `n_tool_calls` สูงผิดปกติ = วนหาของไม่เจอ มักแปลว่า context ไม่พอตั้งแต่แรก
