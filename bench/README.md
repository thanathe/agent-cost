# bench — head-to-head agent benchmarks

Small harnesses for comparing Claude Code and CodeBuddy on the same task, with the same prompt,
limits and working copy, and recording **quality, time and cost** per run. Results go to
`~/agent-bench/results/` (override with `BENCH_RESULTS` or `OUT`) — never into this repo, because
they contain code, diffs and prompts.

| file | what it does |
|---|---|
| `bench-review.sh` | several arms review the same pinned PR commits → one `review.json` each |
| `score-review.py` | mechanical checks, pools findings across arms, scores precision / recall / cost |
| `review-to-draft.py` | turns a `review.json` into the pr-review skill's `draft.json` for posting |
| `bench-greenfield.sh` | from-scratch task in an empty folder (`tasks/landing-page`) |
| `prices.json` | fallback USD per 1M tokens when a run reports no cost |

Arms are listed at the top of each script (`claude-opus-5`, CodeBuddy `default`, `kimi-k3`,
`primary-model`); pick some with `ARMS="claude-opus cb-auto"`. CodeBuddy runs through the
`codebuddy-agent` skill's `cb.sh` if present (it fixes the Node version), else plain `codebuddy`
(override with `CB=`).

## PR review benchmark

```bash
# 1. a clone that has the base branch and the PR heads fetched
git clone https://github.com/<org>/<repo>.git /tmp/src
git -C /tmp/src fetch origin "pull/123/head:pr-123" main:refs/remotes/origin/main

# 2. run (each arm gets its own copy at the pinned SHA; nothing is posted anywhere)
BASE=main ./bench-review.sh /tmp/src "123:<head-sha>:<title without colons>"

# 3. score
./score-review.py ~/agent-bench/results/<run> --src /tmp/src --pool   # checks + pool.json
#    verify each pooled finding, write verdicts.json, then:
./score-review.py ~/agent-bench/results/<run> --verdicts verdicts.json
```

Guard rails on every run: `gh`, `curl`, web, `git push/commit` and subagents are denied, MCP
servers are off (`--strict-mcp-config`), `GH_TOKEN` is set to a dummy, 20-minute timeout, and the
row records whether the agent touched anything besides `review.json`.

`verdicts.json` maps `"<pr>:<arm>#<finding index>"` to `{"group": "...", "real": true|false,
"severity": "..."}` — a human confirms each one; the pooling only groups findings that cite the
same file within a few lines.

## Lessons baked in

- Pin model ids — aliases drift (`sonnet` changed meaning between runs).
- Use per-run credits (`.providerData.rawUsage.credit`), never balance deltas — those bill hangs and
  cancelled runs too.
- n=1 hides a lot: the same model can miss a bug on one run and catch it on the next.
- Check the PR's real diff against the base branch before trusting the host's file list — a stale
  base makes a small PR look huge.
