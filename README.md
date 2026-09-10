# agent-cost — จดว่า agent แต่ละรอบกินไปเท่าไหร่

ทุกครั้งที่ CodeBuddy CLI หรือ Claude Code ทำงานจบ 1 รอบ มันจะจดลงไฟล์เดียว (`ledger.jsonl`) ว่า
รอบนั้น **ใช้ credit / token เท่าไหร่ กี่วินาที เรียก tool อะไร แตะไฟล์ไหน สั่งว่าอะไร**
แล้วสั่ง report ออกมาเทียบกันได้ว่างานแบบไหนใช้ตัวไหนคุ้มกว่า

เก็บด้วยสคริปต์ตัวเดียว นิยามเดียวกันทั้งสอง agent — ตัวเลขถึงเอามาเทียบกันได้จริง

## ติดตั้ง (2 นาที)

ต้องมี `python3` (macOS มีมาให้อยู่แล้ว)

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/thanathe/agent-cost.git ~/.claude/skills/agent-cost
python3 ~/.claude/skills/agent-cost/scripts/install.py
```

(ได้มาเป็นไฟล์ zip แทน: `tar -xzf agent-cost.tar.gz -C ~/.claude/skills` แล้วรัน `install.py` เหมือนกัน)
อัปเดตทีหลัง: `git -C ~/.claude/skills/agent-cost pull` — ledger ไม่โดนแตะ (อยู่คนละที่ + gitignore)

ตัวติดตั้งจะถาม 2 ข้อ — **เก็บไฟล์ ledger ไว้ที่ไหน** (Enter = `~/.agent-cost`) และ
**จะจด prompt ที่สั่งไปด้วยไหม** จากนั้นมันจะ:

- ต่อ `Stop` hook เข้า `~/.claude/settings.json` และ `~/.codebuddy/settings.json` (hook เดิมอยู่ครบ + backup ให้)
- symlink skill นี้เข้า skills/ ของทั้งสองตัว เรียก `/agent-cost` ได้
- ลงซ้ำกี่รอบก็ไม่พัง เจอของตัวเองแล้วข้าม

อยากดูก่อนว่าจะแตะอะไร: `python3 ~/.claude/skills/agent-cost/scripts/install.py --dry-run`

**เปิด session ใหม่** ของ agent แล้วมันจะเริ่มเก็บเอง (session ที่เปิดค้างอยู่ยังใช้ hook ชุดเก่า)

## ดูรายงาน

```bash
R=~/.claude/skills/agent-cost/scripts/report.py
python3 $R                        # เดือนนี้
python3 $R --compare              # เทียบ codebuddy vs claude + แยกตาม repo
python3 $R --repo my-service    # เฉพาะ repo
python3 $R --month 2026-09 --write   # เขียน <เดือน>.md ลงข้าง ๆ ledger
```

หรือถาม agent ตรง ๆ ว่า "เดือนนี้ใช้ credit ไปเท่าไหร่" — skill จะพาไปเอง

## รวม log ของทั้งทีม

ledger เป็นไฟล์เดียว ส่งให้กันได้ตรง ๆ — ฝั่งคนส่ง:

```bash
cp "$(python3 -c 'import sys,os;sys.path.insert(0,os.path.expanduser("~/.claude/skills/agent-cost/scripts"));import cost_paths;print(os.path.join(cost_paths.ledger_dir(),"ledger.jsonl"))')" ~/Desktop/ledger-<ชื่อเรา>.jsonl
```

ฝั่งคนรวม เอามากองไว้โฟลเดอร์เดียวแล้ว:

```bash
python3 ~/.claude/skills/agent-cost/scripts/report.py --ledger 'team/ledger-*.jsonl' --by-owner
python3 ~/.claude/skills/agent-cost/scripts/report.py --ledger 'team/ledger-*.jsonl' --owner preaw   # เจาะคนเดียว
```

ชื่อคนมาจากชื่อไฟล์ (`ledger-preaw.jsonl` → preaw) ไม่ต้องไปแก้ข้อมูลใคร
ไฟล์เดียวกันส่งมาซ้ำสองชื่อก็ไม่นับซ้ำ — dedupe ด้วย `turn_key`

> บอกน้อง ๆ ก่อนว่าไฟล์นี้มี prompt ที่เขาพิมพ์กับ path ไฟล์ที่ agent แตะอยู่ด้วย
> ใครไม่อยากส่ง prompt ให้รัน `install.py --no-prompts` แล้วส่งเฉพาะรอบหลังจากนั้น หรือกรองออกก่อนส่ง:
> `jq -c '.prompt = ""' ledger.jsonl > ledger-<ชื่อ>.jsonl`

## เก็บอะไรได้ / ไม่ได้

| | CodeBuddy CLI (`cbc`) | CodeBuddy IDE | Claude Code |
|---|---|---|---|
| credit | ✅ ของจริงจาก transcript | ❌ ต้องกรอกมือ | ❌ ไม่มี field นี้ |
| token / เวลา / tool | ✅ | ❌ | ✅ |

- **IDE เก็บอัตโนมัติไม่ได้** เลข Credits/Tokens ที่ UI โชว์มาจาก API ไม่ได้ลงดิสก์ → กรอกเองได้ (ดู SKILL.md หัวข้อสุดท้าย)
- **Claude ไม่มี credit** จะคิดเป็นเงินต้องเติม `pricing.json` เอง ไม่งั้น report โชว์ `—` (ไม่เดาราคาให้)

## ข้อมูลอยู่ที่เครื่องเราเท่านั้น

ledger ไม่ถูกส่งไปไหน อยู่ในเครื่องคนใช้ล้วน ๆ แต่ในนั้นมี **prompt ที่พิมพ์ + path ไฟล์ที่ agent แตะ**

- ไม่อยากให้จด prompt → ตอบ `n` ตอนติดตั้ง (หรือ `--no-prompts`) เหลือแค่ตัวเลข
- **อย่าเอา ledger ไป commit เข้า repo ที่แชร์กัน** ถ้าจะรวมยอดทีม ส่งแค่ตัวเลขสรุปจาก `report.py` พอ
- เปลี่ยนที่เก็บทีหลัง: แก้ `~/.config/agent-cost/config.json`

## ถอดออก

```bash
python3 ~/.claude/skills/agent-cost/scripts/install.py --uninstall
```

เอา hook กับ symlink ออก — ledger เดิมยังอยู่ ลบเองได้ถ้าไม่เอา

## ไม่ขึ้นเลย ทำไง

```bash
# ยิง hook มือ ๆ ด้วย transcript ล่าสุด แล้วดูว่ามันบ่นอะไร
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "{\"session_id\":\"test\",\"transcript_path\":\"$T\",\"cwd\":\"$PWD\"}" \
  | AGENT_COST_DEBUG=1 python3 ~/.claude/skills/agent-cost/scripts/capture.py
```

- `turn had no usage — skipped` = รอบนั้นไม่ได้ใช้อะไรจริง ปกติ
- ไม่มี output เลย = hook ยังไม่เข้า → รัน `install.py` ใหม่แล้วดูบรรทัด `settings:`
- hook พังยังไงก็ **ไม่ทำให้ agent สะดุด** สคริปต์ exit 0 เสมอ
