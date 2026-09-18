#!/usr/bin/env bash
# Greenfield A/B: Claude Code vs CodeBuddy on a from-scratch task (no repo, no git).
#   ./bench-greenfield.sh <task-name> [runs-per-arm]
set -uo pipefail

TASK=${1:?task name under tasks/}; RUNS=${2:-1}
ROOT=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-${BENCH_RESULTS:-$HOME/agent-bench/results}/$(date +%Y%m%d-%H%M%S)-$TASK}; mkdir -p "$OUT"
CB=${CB:-$HOME/.claude/skills/codebuddy-agent/scripts/cb.sh}; [ -x "$CB" ] || CB=codebuddy
RESULTS=$OUT/results.jsonl
RUN_TIMEOUT=${RUN_TIMEOUT:-900}

with_timeout() { perl -e 'alarm shift; exec @ARGV or exit 127' "$@"; }

ARMS=(
  "claude|claude|claude-sonnet-5"
  "cb-auto|codebuddy|"
)

# Claude prints ONE result object; CodeBuddy prints an ARRAY whose last result element counts.
NORM='if type=="array" then ([.[]|select(.type=="result")]|last // {}) else . end'
# CodeBuddy bills in credits, reported per assistant message at .providerData.rawUsage.credit
CREDIT='if type=="array" then ([.[]|.providerData.rawUsage.credit // empty]|add // 0) else 0 end'

run_one() {
  local label=$1 cli=$2 model=$3 run=$4
  local wt="/tmp/gbench-$label-$TASK-$run"
  rm -rf "$wt"; mkdir -p "$wt"
  local prompt; prompt=$(cat "$ROOT/tasks/$TASK/prompt.md")
  local log="$OUT/$label-$TASK-$run"
  local t0=$SECONDS

  if [ "$cli" = codebuddy ]; then
    ( cd "$wt" && with_timeout "$RUN_TIMEOUT" "$CB" -p "$prompt" --output-format json \
        --permission-mode bypassPermissions --max-turns 60 --add-dir "$wt" \
        ${model:+--model "$model"} ) >"$log.json" 2>"$log.err"
  else
    ( cd "$wt" && with_timeout "$RUN_TIMEOUT" claude -p "$prompt" --output-format json \
        --permission-mode bypassPermissions --max-turns 60 ${model:+--model "$model"} \
    ) >"$log.json" 2>"$log.err"
  fi
  local rc=$? wall=$((SECONDS - t0))

  local pass=0
  ( cd "$wt" && bash "$ROOT/tasks/$TASK/verify.sh" ) >"$log.verify" 2>&1 && pass=1

  # keep the artifact itself for side-by-side inspection
  cp "$wt/index.html" "$log.index.html" 2>/dev/null
  local bytes=0; [ -f "$wt/index.html" ] && bytes=$(wc -c < "$wt/index.html" | tr -d ' ')
  local extra; extra=$(cd "$wt" && ls -A | grep -v '^index.html$' | tr '\n' ',' )

  jq -n --arg l "$label" --arg cli "$cli" --arg m "${model:-auto}" --arg t "$TASK" \
        --argjson r "$run" --argjson rc "$rc" --argjson pass "$pass" --argjson wall "$wall" \
        --argjson bytes "$bytes" --arg extra "$extra" --slurpfile raw "$log.json" \
    "(\$raw[0] // {}) as \$doc | (\$doc | $NORM) as \$x | (\$doc | $CREDIT) as \$cr |
     {agent:\$l, cli:\$cli, model:\$m, task:\$t, run:\$r, exit:\$rc, pass:\$pass,
      wall_s:\$wall, bytes:\$bytes, extra_files:\$extra,
      turns:(\$x.num_turns // null), cost_usd:(\$x.total_cost_usd // null),
      credits:\$cr, api_ms:(\$x.duration_api_ms // null),
      in_tok:(\$x.usage.input_tokens // 0), out_tok:(\$x.usage.output_tokens // 0),
      cache_read:(\$x.usage.cache_read_input_tokens // 0),
      cache_write:(\$x.usage.cache_creation_input_tokens // 0),
      result_text:(\$x.result // \"\")}" >> "$RESULTS" 2>/dev/null \
    || echo "{\"agent\":\"$label\",\"run\":$run,\"parse\":\"failed\",\"exit\":$rc}" >> "$RESULTS"

  printf '  %-8s run%s -> pass=%s  %ss  %sB  extra=[%s]\n' "$label" "$run" "$pass" "$wall" "$bytes" "$extra"
}

for run in $(seq 1 "$RUNS"); do
  for arm in "${ARMS[@]}"; do
    IFS='|' read -r label cli model <<< "$arm"
    run_one "$label" "$cli" "$model" "$run"
  done
done

echo; echo "== summary"
jq -s -r 'group_by(.agent)[] |
  [ .[0].agent, .[0].model, length, ((map(.pass)|add)/length*100|round|tostring+"%"),
    (map(.wall_s)|add/length|round), ((map(.turns//0)|add)/length|round),
    (map(.out_tok)|add), (map(.in_tok+.cache_read+.cache_write)|add),
    ((map(.cost_usd//0)|add)*10000|round/10000), ((map(.credits//0)|add)*100|round/100) ] | @tsv' "$RESULTS" \
  | cat <(printf 'arm\tmodel\tn\tpass\tavg_s\tturns\tout_tok\ttotal_ctx\tusd\tcredits\n') - | column -t
echo "raw: $RESULTS"
