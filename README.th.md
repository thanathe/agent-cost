# agent-cost

*[English](README.md) · ไทย*

**รู้ว่า agent ที่ใช้อยู่ กินเงินไปเท่าไหร่จริง ๆ**

ทุกครั้งที่ Claude Code, CodeBuddy หรือ Codex ทำงานจบ 1 รอบ agent-cost จะจดลง ledger ในเครื่อง 1 บรรทัด —
credit, token, กี่วินาที, เรียก tool อะไร, แตะไฟล์ไหน, สั่งว่าอะไร แล้วเอามาคิดเป็นเงินให้ดูผ่าน dashboard สด ๆ
หรือ report รายเดือน คำถามแบบ "งานแบบนี้ใช้ CodeBuddy ถูกกว่า Claude ไหม" เลยตอบด้วยตัวเลข ไม่ใช่ความรู้สึก

เก็บด้วยสคริปต์ตัวเดียว นิยาม "1 รอบ" กับ "1 token" เดียวกันทุก agent — ตัวเลขถึงเอามาวางเทียบกันได้จริง

ข้อมูลอยู่ในเครื่องล้วน ๆ ไม่มี server ไม่ต้องสมัคร ไม่มี telemetry

---

## สารบัญ

- [ได้อะไรบ้าง](#ได้อะไรบ้าง) · [agent ที่รองรับ](#agent-ที่รองรับ) · [ติดตั้ง](#ติดตั้ง)
- [ตั้งราคา](#ตั้งราคา) · [dashboard](#dashboard) · [report](#report)
- [CodeBuddy IDE กับ Codex](#codebuddy-ide-กับ-codex) · [ย้อนเก็บของเก่า](#ย้อนเก็บของเก่า)
- [รวมยอดทั้งทีม](#รวมยอดทั้งทีม) · [กติกาการนับ](#กติกาการนับ) · [ความเป็นส่วนตัว](#ความเป็นส่วนตัว)
- [ไม่ขึ้น / ตัวเลขแปลก](#ไม่ขึ้น--ตัวเลขแปลก) · [ถอดออก](#ถอดออก) · [ส่ง PR](#ส่ง-pr)

---

## ได้อะไรบ้าง

- **dashboard สดบน localhost** — ช่วงที่เลือกจ่ายไปเท่าไหร่ต่อ agent, ต่อรอบ, ต่อ 1M token, กราฟรายวัน,
  รอบที่แพงสุด, แยกตาม repo · รอบที่เพิ่งจบขึ้นเองภายในไม่กี่วินาที ไม่ต้อง refresh
- **report รายเดือนเป็น markdown** — `2026-09.md` วางข้าง ๆ ledger สร้างใหม่จากข้อมูลดิบได้ตลอด
- **คิดเป็นเงินจริง ไม่ใช่แค่ token** — แพ็กเหมาเฉลี่ยตามวันที่ใช้จริง, credit คูณราคาที่เราตั้ง,
  และมีราคา API วางข้าง ๆ ให้เห็นว่าแพ็กคุ้มกว่ากี่เท่า
- **เทียบกันแบบแฟร์** — ขอบเขต "1 รอบ" เดียวกัน นิยาม token เดียวกัน นับ cache read/write เหมือนกันทุกตัว
- **ledger เป็นของเรา** — JSONL ธรรมดา บรรทัดละรอบ ยิง `jq` ถามเองได้

## agent ที่รองรับ

| | Claude Code | CodeBuddy CLI | CodeBuddy IDE | Codex |
|---|---|---|---|---|
| เก็บยังไง | `Stop` hook | `Stop` hook | อ่าน history ของแอป | อ่าน rollout ในเครื่อง |
| credit / ราคาจากเจ้าของ | ❌ ไม่มีใน transcript | ✅ credit จริง | ✅ credit จริง | ❌ ไม่มี |
| token · เวลา · tool · prompt | ✅ | ✅ | ✅ | ✅ |
| เงินมาจาก | แพ็กของเรา หรือราคา API | credit × ราคาที่ตั้ง | credit × ราคาที่ตั้ง | แพ็กของเรา หรือราคา API |
| ของก่อนติดตั้ง | `backfill.py` | `backfill.py` | `backfill.py` | `codex_capture.py --month` |

รองรับด้วย: subagent ของ Claude (token รวมเข้ารอบที่เรียกมัน), รอบที่ Claude สั่ง CLI ให้ทำเอง (ติด `via: claude`),
และ Claude Code ที่ชี้ไป model อื่นผ่าน gateway (แยกเป็น `claude-code:<model>` ไม่ปนยอด Claude)

---

## ติดตั้ง

ต้องมี `python3` (macOS กับ Linux ส่วนใหญ่มีมาให้อยู่แล้ว)

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/thanathe/agent-cost.git ~/.claude/skills/agent-cost
python3 ~/.claude/skills/agent-cost/scripts/install.py
```

ตัวติดตั้งถาม 2 ข้อ

1. **เก็บ ledger ไว้ที่ไหน** — Enter = `~/.agent-cost` · เลือกโฟลเดอร์ที่ **ไม่ได้อยู่ใน git repo**
2. **จะจด prompt ที่พิมพ์ไหม** — ตอบ `n` ถ้าอยากได้แค่ตัวเลข

แล้วมันจะต่อ `Stop` hook เข้า `~/.claude/settings.json` กับ `~/.codebuddy/settings.json`
(hook เดิมอยู่ครบ + backup ให้ก่อนทุกครั้ง) และ symlink skill เข้าทั้งสอง agent ให้เรียก `/agent-cost` ได้
ลงซ้ำกี่รอบก็ไม่พัง

```bash
python3 ~/.claude/skills/agent-cost/scripts/install.py --dry-run          # ดูก่อนว่าจะแตะอะไร
python3 ~/.claude/skills/agent-cost/scripts/install.py --only claude      # ลงตัวเดียว
python3 ~/.claude/skills/agent-cost/scripts/install.py -y --no-prompts    # ไม่ถาม + ไม่จด prompt
```

**เปิด session ใหม่ด้วย** — session ที่เปิดค้างอยู่ยังใช้ hook ชุดเก่า

### อัปเดต

```bash
git -C ~/.claude/skills/agent-cost pull
python3 ~/.claude/skills/agent-cost/scripts/capture.py --rebuild   # คำนวณแถวเก่าใหม่ด้วยกติกาล่าสุด
```

`--rebuild` backup ให้ก่อนเสมอ (`ledger.jsonl.bak-*`) ใช้เฉพาะตอนที่กติกาการนับเปลี่ยน

---

## ตั้งราคา

ไม่ตั้งก็ใช้ได้ แต่จะเห็นแค่ token กับ credit ไม่มีเงิน · สร้าง `pricing.json` วางข้าง ๆ ledger:

```json
{
  "_plans": {
    "claude":    { "name": "Max 5x", "usd_per_month": 100, "days_per_month": 20 },
    "codex":     { "usd_per_month": 20 },
    "codebuddy": { "usd_per_credit": 0.005 }
  },
  "_fx": { "thb_per_usd": 32.5 },

  "claude-opus-5":    { "input": 5,  "output": 25, "cache_read": 0.5,  "cache_write": 6.25 },
  "claude-fable-5-1": { "input": 10, "output": 50, "cache_read": 0.25, "cache_write": 12.5 }
}
```

| key | คืออะไร |
|---|---|
| `_plans.<agent>.usd_per_month` | ค่าแพ็กเหมาที่จ่ายจริงต่อเดือน |
| `_plans.<agent>.days_per_month` | ไม่ใส่ = หารทุกวันในปฏิทิน · ใส่ 20 = คิด 1 เดือน = 20 วันทำงาน แล้วคิดเฉพาะวันที่ใช้ |
| `_plans.codebuddy.usd_per_credit` | ราคา 1 credit |
| `_fx.thb_per_usd` | เรทแปลงเป็นบาท (โชว์ประกอบข้าง USD — เฉพาะ dashboard) |
| `<model>` | ราคา API ต่อ 1M token ไว้ดูคอลัมน์ "ถ้าจ่ายตาม token" · `cache_write` ไม่ใส่ = `input × 1.25` |

ราคาเปลี่ยนบ่อย — เช็คหน้าราคาจริงก่อน อย่าเชื่อตัวอย่าง · ทุกค่าที่นี่ใช้แค่แสดงผล เครื่องมือนี้ไม่ได้ไปตัดเงินใคร

---

## dashboard

```bash
python3 ~/.claude/skills/agent-cost/scripts/dashboard.py --open
```

เปิด <http://127.0.0.1:8791> (เปลี่ยน port ด้วย `--port`) เปิดทิ้งไว้ได้เลย มันเช็ค ledger ใหม่ทุกไม่กี่วินาที

- **หัวตาราง** — ช่วงที่เลือกแต่ละ agent จ่ายไปเท่าไหร่ วางข้างกัน
- **ช่วงวัน** — ทั้งหมด / วันนี้ / 7 / 30 วัน หรือกำหนดวันเริ่ม–วันจบเอง
- **แยก CLI กับ IDE** ของ CodeBuddy และบอกว่ารอบไหน Claude เป็นคนสั่งให้ทำ
- **แถวเทียบกันแบบแฟร์** — เงินรวม, ต่อรอบ, ต่อ 1M output token, ต่อ 1M token และจุดคุ้มทุนราคา credit
  · ดูพร้อมกันทั้งแถว เพราะเปลี่ยนหน่วยวัดแล้วผู้ชนะเปลี่ยน
- **กราฟรายวัน, รอบที่แพงสุด, แยกตาม repo** และข้อสังเกตจากข้อมูล (เช่น เปลี่ยน model กลาง session ทำ cache หลุด)
- **ช่องตั้งค่า** — ราคา credit, ค่าแพ็ก, เรทเงินบาท · ค่าที่พิมพ์จำไว้ใน browser ถ้าไม่พิมพ์จะใช้จาก `pricing.json`

bind แค่ 127.0.0.1 และตอบเฉพาะ Host ที่เป็น localhost เพราะใน ledger มี prompt กับ path ไฟล์ของเรา

## report

```bash
R=~/.claude/skills/agent-cost/scripts/report.py
python3 $R                             # เดือนนี้
python3 $R --month 2026-09 --write     # เขียน 2026-09.md ข้าง ๆ ledger ด้วย
python3 $R --compare                   # แยกตาม repo
python3 $R --since 2026-09-01 --until 2026-09-15
python3 $R --agent codebuddy --source ide
python3 $R --repo my-service --limit 50
```

ในแต่ละ report มีตารางสรุป, ตาราง "จ่ายจริงเท่าไหร่" (แพ็กเฉลี่ย, credit แปลงเป็นเงิน, เทียบราคา API),
ตารางแยก repo / แยกคน และรายการรอบล่าสุด

หรือถาม agent ตรง ๆ ว่า *"เดือนนี้ใช้ credit ไปเท่าไหร่"* — skill ที่แถมมาจะรันคำสั่งให้เอง

---

## CodeBuddy IDE กับ Codex

สองตัวนี้ไม่มี hook เลยใช้วิธีอ่านไฟล์ที่มันเขียนไว้ในเครื่องอยู่แล้ว · เปิด dashboard ไว้มันดึงให้เอง
ถ้าไม่ได้เปิด ให้รันเองก่อนดู report

```bash
S=~/.claude/skills/agent-cost/scripts
python3 $S/ide_sync.py                 # CodeBuddy IDE  (--dry-run, --watch, --resync)
python3 $S/codex_capture.py            # Codex เดือนนี้  (--month YYYY-MM, --watch)
```

- **CodeBuddy IDE** ลงเป็น `agent: codebuddy` + `source: ide` → รวมยอดกับ CLI ได้ แต่แยกดูได้ทุกที่
  อ่าน history ของ extension ที่ `~/Library/Application Support` (macOS), `%APPDATA%` (Windows),
  `~/.config` (Linux) — ชี้เองได้ด้วย `CODEBUDDY_APPDATA` · ครั้งแรกดึงทุกอย่างที่เจอ อาจย้อนหลังหลายเดือน
- **Codex** เขียนแยกเป็น `codex-ledger.jsonl` อ่านจาก `$CODEX_HOME` (ดีฟอลต์ `~/.codex`)
  นับเฉพาะรอบที่มี usage จริงต่อ response และจบสมบูรณ์ · รอบที่ถูกยกเลิกข้าม
  เลือกเดือนตามโฟลเดอร์ rollout เดือนเก่าต้องสั่ง `--month` เอง

## ย้อนเก็บของเก่า

hook เห็นเฉพาะรอบที่จบหลังติดตั้ง ของก่อนหน้านั้นยังอยู่ในดิสก์:

```bash
S=~/.claude/skills/agent-cost/scripts
python3 $S/backfill.py --dry-run --since 2026-08-01   # นับก่อน
python3 $S/backfill.py --since 2026-08-01             # เขียนจริง
python3 $S/capture.py --rebuild                       # ออปชัน: ติดป้ายว่ารอบไหน Claude สั่ง CLI
```

- **ใส่ `--since` เสมอ** ถ้าไม่ได้ตั้งใจเอาทั้งหมดจริง ๆ — Claude Code ไม่กี่เดือนก็หลักพันรอบแล้ว
- รันซ้ำได้ ไม่นับซ้ำ เพราะใช้ turn key ชุดเดียวกับ hook
- ข้ามรอบสุดท้ายของ transcript ที่เพิ่งถูกเขียนใน 10 นาที (อาจยังทำงานอยู่ เดี๋ยว hook จดเอง)
- ใช้แพ็กเหมา: อย่าย้อนไปก่อนวันที่เริ่มแพ็กปัจจุบัน ไม่งั้นเดือนเก่าคิดเงินผิด
- `--only claude|codebuddy|ide` เลือกเฉพาะบางแหล่ง

---

## รวมยอดทั้งทีม

ไม่มี server กลาง — แต่ละคนส่ง ledger ให้คนที่รวมยอด

**ฝั่งคนส่ง** ตัด prompt กับ path ออกก่อน:

```bash
L="$(python3 -c 'import sys,os;sys.path.insert(0,os.path.expanduser("~/.claude/skills/agent-cost/scripts"));import cost_paths;print(os.path.join(cost_paths.ledger_dir(),"ledger.jsonl"))')"
jq -c '.prompt="" | .files_touched=[] | del(.cwd, .transcript, .ide_history)' "$L" > ~/Desktop/ledger-<ชื่อเรา>.jsonl
# ใช้ Codex ด้วย: ต่อท้ายไฟล์เดียวกัน
jq -c '.prompt="" | .files_touched=[] | del(.cwd, .transcript)' "$(dirname "$L")/codex-ledger.jsonl" >> ~/Desktop/ledger-<ชื่อเรา>.jsonl 2>/dev/null
```

ตั้งชื่อไฟล์ `ledger-<ชื่อ>.jsonl` — ชื่อตรงนั้นคือชื่อที่ขึ้นใน report

**ฝั่งคนรวม** เอาไฟล์มากองโฟลเดอร์เดียว (นอก git repo):

```bash
R=~/.claude/skills/agent-cost/scripts/report.py
python3 $R --ledger 'team/ledger-*.jsonl' --month 2026-09        # ยอดรวม + ตารางแยกคน
python3 $R --ledger 'team/ledger-*.jsonl' --owner somchai        # เจาะคนเดียว
```

ส่งไฟล์เดิมซ้ำได้ ไม่นับซ้ำ (dedupe ด้วย turn key) · ค่าแพ็กคิดแยกรายคนแล้วค่อยรวม โดยใช้ `pricing.json`
ของคนรวม ถ้าในทีมใช้แพ็กคนละแบบ ให้ถือว่ายอดรวมเป็นค่าประมาณ แล้วดูตารางรายคนประกอบ

dashboard อ่านได้แค่ ledger ของเครื่องตัวเอง

---

## กติกาการนับ

กติกาพวกนี้คือเหตุผลที่ตัวเลขเอามาเทียบกันได้ — อ่านสองนาทีก่อนเอาไปอ้างกับใคร

- **1 แถว = 1 รอบที่คนสั่ง** ไม่ใช่ 1 API call · เรียก tool 40 ครั้งก็ยังนับเป็นรอบเดียว
  สิ่งที่ไม่นับเป็นการเริ่มรอบใหม่: tool result, skill body ที่ถูกแทรก, echo ของคำสั่ง local
  (`/model` `/clear` `!bash`) และข้อความตอนกดหยุด
- **token นิยามเดียวกันหมด** (`usage_v: 2`): `input_tokens` = input ที่ไม่ได้มาจาก cache,
  แยก `cache_read_tokens` / `cache_write_tokens` และ
  `total_tokens = input + cache read + cache write + output` · เจ้าไหนรวม cache hit มาใน input ให้แล้ว จะหักออกตอนอ่าน
- **subagent ของ Claude** รวมเข้ารอบที่เรียกมัน (`n_subagents`, `subagent_tokens`)
- **เวลา** = ผลรวมช่วงห่างระหว่าง event ที่เป็นงาน โดยตัดช่วงเงียบเกิน 30 นาทีทิ้ง
  session ที่เปิดค้างข้ามคืนเลยไม่กลายเป็นรอบยาว 14 ชม. · รวมเวลารอ tool ด้วย ไม่ใช่เวลา inference ล้วน
- **แพ็กเหมาเฉลี่ยตามวัน ไม่ใช่ตาม token** — ทุกวันในปฏิทิน หรือเฉพาะวันที่ใช้ถ้าตั้ง `days_per_month`
  แพ็กไม่ได้ถูกลงเพราะเราพิมพ์น้อย
- **รอบที่ error ก็เสียเงิน** — 502/504 คือ gateway ล่ม *หลัง* โมเดลทำงานไปแล้ว token ถูกคิดไปแล้ว
  แถวจะมี `n_api_errors` + `error`, `n_tool_errors` และ `resumed` (รอบ IDE ที่เริ่มด้วยการสั่ง resume ต่อ = retry หลังรอบล่ม)
  · dashboard สรุปให้ว่า credit กี่ % หมดไปกับรอบพวกนี้ · IDE ไม่เก็บ body ของ 5xx ลงดิสก์ เลยดูได้แค่ทางอ้อมจากรอบ resumed
- **รอบที่ไม่มี output และไม่มี credit → ข้าม** เพราะไม่มีอะไรเกิดขึ้นจริง
- **ไม่เดาให้เงียบ ๆ** ไม่เดาราคา ไม่เดา credit · ไม่มีข้อมูลก็ขึ้น `—`

รายละเอียดราย field อยู่ใน [SKILL.md](SKILL.md)

---

## ความเป็นส่วนตัว

ledger ไม่ถูกส่งไปไหนเอง แต่ในนั้นมี **prompt ที่พิมพ์, path ไฟล์ที่ agent แตะ และชื่อ repo ของเรา**

- **อย่า commit `ledger.jsonl`, `*.bak-*`, report `*.md`, `pricing.json`, `.ide_sync.json`
  หรือ `codex-ledger.jsonl`** · ถ้าโฟลเดอร์ ledger อยู่ใน repo (เช่น wiki) ใส่ `.gitignore` ก่อน:

  ```gitignore
  agent-cost/*.jsonl
  agent-cost/*.jsonl.*
  agent-cost/*.md
  agent-cost/pricing.json
  agent-cost/.ide_sync.json*
  agent-cost/.ledger.lock
  ```

  ⚠️ `git rm --cached ledger.jsonl && git commit -- ledger.jsonl` จะ **เอาไฟล์กลับเข้าไปใหม่**
  เพราะ commit ที่ระบุ path จะหยิบไฟล์จากในเครื่องมาใส่ · ให้ `git commit` เฉย ๆ แล้วเช็ค `git ls-files`
- **ไม่อยากให้จด prompt** → `install.py --no-prompts` (มีผลกับรอบถัดไป) ส่วนรอบเก่าใช้ `jq` ข้างบนตัดก่อนส่ง
- **ย้ายที่เก็บทีหลัง** → แก้ `~/.config/agent-cost/config.json`
- repo นี้ public มีแต่โค้ด ไม่มีข้อมูลของใคร

---

## ไม่ขึ้น / ตัวเลขแปลก

```bash
# ยิง hook มือ ๆ ด้วย transcript ล่าสุด แล้วดูว่ามันบ่นอะไร
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "{\"session_id\":\"test\",\"transcript_path\":\"$T\",\"cwd\":\"$PWD\"}" \
  | AGENT_COST_DEBUG=1 python3 ~/.claude/skills/agent-cost/scripts/capture.py
```

| อาการ | สาเหตุที่น่าจะใช่ |
|---|---|
| `turn had no usage — skipped` | ปกติ — รอบนั้นไม่ได้ใช้อะไรจริง |
| ไม่มี output เลย | hook ยังไม่เข้า → รัน `install.py` ใหม่ ดูบรรทัด `settings:` แล้วเปิด session ใหม่ |
| ไม่มีรอบจาก IDE | `ide_sync.py --dry-run` ได้ 0 = หา history ไม่เจอ ลองตั้ง `CODEBUDDY_APPDATA` |
| ไม่มีรอบจาก Codex | รอบที่ยกเลิกกลางทางและ rollout รุ่นเก่า ตั้งใจข้าม |
| ตัวเลขเพี้ยนหลังอัปเดต | `capture.py --rebuild` หรือ `ide_sync.py --resync` สำหรับแถว IDE |
| ยอด Claude พุ่งหลัง backfill | ย้อนไปก่อนเริ่มแพ็กปัจจุบัน — เอา backup คืนแล้วรันใหม่ด้วย `--since` |
| ป้ายบน dashboard เป็นสีแดง | server ปิดไปแล้ว รัน `dashboard.py` ใหม่ |

hook พังยังไงก็ไม่ทำให้ agent สะดุด — `capture.py` exit 0 เสมอ

## ถอดออก

```bash
python3 ~/.claude/skills/agent-cost/scripts/install.py --uninstall
```

เอา hook กับ symlink ออก · ledger เดิมยังอยู่ อยากลบค่อยลบเอง

## Benchmark

`bench/` มีสคริปต์เทียบ agent แบบตัวต่อตัวบนงานเดียวกัน (รีวิว PR, สร้างหน้าเว็บ) พร้อมวัดคุณภาพ · เวลา · ค่าใช้จ่าย — ดู [bench/README.md](bench/README.md)

## ส่ง PR

ยินดีรับ issue และ PR ทุกขนาด — ตัวเลขผิด, อยากให้รองรับ agent อื่น, ย่อหน้าไหนอ่านแล้วงง
อ่าน [CONTRIBUTING.md](CONTRIBUTING.md) ก่อน สรุปสั้น ๆ คือ ห้ามมีข้อมูลส่วนตัวใน repo,
hook ต้องไม่ทำให้ agent ค้าง, ใช้แค่ standard library · ทุก PR รีวิวก่อน merge

เปิด issue เรื่องตัวเลข แนบ output ของ `AGENT_COST_DEBUG=1` ข้างบนมาด้วย — **ลบ path, ชื่อ repo และ prompt ออกก่อน**

[MIT](LICENSE)
