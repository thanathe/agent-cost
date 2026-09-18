#!/usr/bin/env bash
# PR-review A/B: Claude Code vs CodeBuddy models reviewing the same pinned PR commits.
#   BASE=main ./bench-review.sh <src-repo> "<pr>:<sha>:<title>" ... [ARMS="claude-opus cb-auto"]
# Each run gets its own copy of the repo at the pinned SHA, reviews `origin/$BASE...HEAD`,
# and must write review.json. No network/gh for the agents; nothing is posted anywhere.
# <src-repo> must already have origin/$BASE and the pinned SHAs fetched.
# Results go OUTSIDE the repo (default ~/agent-bench/results) — they hold code and prompts.
set -uo pipefail

SRC=${1:?source git repo}; shift
ROOT=$(cd "$(dirname "$0")" && pwd)
BASE=${BASE:-main}
OUT=${OUT:-${BENCH_RESULTS:-$HOME/agent-bench/results}/$(date +%Y%m%d-%H%M%S)-pr-review}; mkdir -p "$OUT"
# CodeBuddy CLI: the codebuddy-agent wrapper fixes its Node version; plain `codebuddy` otherwise.
CB=${CB:-$HOME/.claude/skills/codebuddy-agent/scripts/cb.sh}; [ -x "$CB" ] || CB=codebuddy
RESULTS=$OUT/results.jsonl
RUN_TIMEOUT=${RUN_TIMEOUT:-1200}
NODE_MODULES=${NODE_MODULES:-}
ONLY_ARMS=${ARMS:-}

with_timeout() { perl -e 'alarm shift; exec @ARGV or exit 127' "$@"; }

ALL_ARMS=(
  "claude-opus|claude|claude-opus-5"
  "cb-auto|codebuddy|"
  "cb-kimi|codebuddy|kimi-k3"
  "cb-primary|codebuddy|primary-model"
)
# Nothing may leave the machine or touch the repo's history.
DENY="Bash(gh:*) Bash(git push:*) Bash(git commit:*) Bash(curl:*) Bash(wget:*) WebFetch WebSearch Task Agent"

NORM='if type=="array" then ([.[]|select(.type=="result")]|last // {}) else . end'
CREDIT='if type=="array" then ([.[]|.providerData.rawUsage.credit // empty]|add // 0) else 0 end'

run_one() {
  local label=$1 cli=$2 model=$3 pr=$4 sha=$5 title=$6
  local wt="/tmp/rbench-$label-$pr"
  rm -rf "$wt"
  git clone -q --shared "$SRC" "$wt" && git -C "$wt" checkout -q "$sha" \
    && git -C "$wt" update-ref refs/remotes/origin/$BASE "$(git -C "$SRC" rev-parse "origin/$BASE")" || {
      echo "{\"agent\":\"$label\",\"pr\":$pr,\"invalid\":\"setup failed\"}" >>"$RESULTS"; return; }
  [ -n "$NODE_MODULES" ] && ln -s "$NODE_MODULES" "$wt/node_modules"
  local prompt; prompt=$(sed -e "s|__PR__|$pr|g" -e "s|__TITLE__|$title|g" -e "s|__BASE__|$BASE|g" "$ROOT/tasks/${TASK_DIR:-pr-review}/prompt.md")
  local log="$OUT/$label-$pr"
  local t0=$SECONDS
  echo "$(date +%H:%M:%S) start $label #$pr" >&2

  if [ "$cli" = codebuddy ]; then
    ( cd "$wt" && GH_TOKEN=disabled-for-benchmark GITHUB_TOKEN=disabled-for-benchmark with_timeout "$RUN_TIMEOUT" "$CB" -p "$prompt" --output-format json \
        --permission-mode bypassPermissions --max-turns 60 --add-dir "$wt" --strict-mcp-config \
        --disallowedTools $DENY ${model:+--model "$model"} ) >"$log.json" 2>"$log.err"
  else
    ( cd "$wt" && GH_TOKEN=disabled-for-benchmark GITHUB_TOKEN=disabled-for-benchmark with_timeout "$RUN_TIMEOUT" claude -p "$prompt" --output-format json \
        --permission-mode bypassPermissions --max-turns 60 --strict-mcp-config \
        --disallowedTools $DENY ${model:+--model "$model"} ) >"$log.json" 2>"$log.err"
  fi
  local rc=$? wall=$((SECONDS - t0))

  local valid=0
  if [ -f "$wt/review.json" ] && jq -e '.findings|type=="array"' "$wt/review.json" >/dev/null 2>&1; then
    valid=1; cp "$wt/review.json" "$log.review.json"
  fi
  # anything else the agent changed in the repo is a rule break worth recording
  local touched; touched=$(git -C "$wt" status --porcelain | grep -v -e ' review.json$' -e ' node_modules$' -e ' .serena/$' | wc -l | tr -d ' ')

  jq -cn --arg l "$label" --arg cli "$cli" --arg m "${model:-auto}" --argjson pr "$pr" --arg sha "$sha" \
        --argjson rc "$rc" --argjson valid "$valid" --argjson wall "$wall" --argjson touched "$touched" \
        --slurpfile raw "$log.json" \
    "(\$raw[0] // {}) as \$doc | (\$doc | $NORM) as \$x | (\$doc | $CREDIT) as \$cr |
     {agent:\$l, cli:\$cli, model:\$m, pr:\$pr, sha:\$sha, exit:\$rc, timeout:(\$rc==142 or \$rc==143),
      valid_review:\$valid, repo_files_touched:\$touched, wall_s:\$wall,
      turns:(\$x.num_turns // null), cost_usd:(\$x.total_cost_usd // null), credits:\$cr,
      in_tok:(\$x.usage.input_tokens // 0), out_tok:(\$x.usage.output_tokens // 0),
      cache_read:(\$x.usage.cache_read_input_tokens // 0), cache_write:(\$x.usage.cache_creation_input_tokens // 0),
      result_text:((\$x.result // \"\")|tostring|.[0:200])}" >>"$RESULTS" 2>/dev/null \
  || echo "{\"agent\":\"$label\",\"pr\":$pr,\"exit\":$rc,\"wall_s\":$wall,\"valid_review\":$valid,\"invalid\":\"unparseable output\"}" >>"$RESULTS"
  echo "$(date +%H:%M:%S) done  $label #$pr rc=$rc wall=${wall}s valid=$valid" >&2
}

# Interleave arms per PR so an outage hits every arm, not just one.
for spec in "$@"; do
  IFS=: read -r pr sha title <<<"$spec"
  for arm in "${ALL_ARMS[@]}"; do
    IFS='|' read -r label cli model <<<"$arm"
    if [ -n "$ONLY_ARMS" ] && [[ " $ONLY_ARMS " != *" $label "* ]]; then continue; fi
    run_one "$label" "$cli" "$model" "$pr" "$sha" "$title"
  done
done
echo "results: $RESULTS" >&2
