You are reviewing one pull request in the repository checked out in the current directory.

PR: __TITLE__
The change under review is exactly: `git diff origin/__BASE__...HEAD` (run it yourself; `git log origin/__BASE__..HEAD` shows the commit).
Review ONLY that change. Code outside it is context you may read, not something to review.

## Rules (strict)
- Do NOT use `gh`, the GitHub API, or the network. Everything you need is in this checkout.
- Do NOT read or look for existing review comments.
- Do NOT post, push, commit, or modify any repository file. The only file you may create is `review.json` in the current directory.
- Work alone — do not start subagents.
- Tests: you may run the repo's own test command on the files involved (e.g. `npx jest <path>`). Failures caused only by missing env/DB are not the PR's fault.

## How to review — trace real code, not just the diff
Bugs hide at the seam between new code and unchanged code. Follow the path: entry point → callers → branches → state changes → return / side effects. Grep the whole repo for callers of anything changed.

Four axes, every finding needs evidence (`file:line` or a trace step):
1. Correctness — null/empty/0/unicode/large input, error and partial-failure paths, transactions, race/ordering, silent contract changes for existing callers, N+1 queries.
2. Security — authz/authn on handlers, IDOR, injection (SQL concat, command, path), secrets in code/logs, validation at the boundary.
3. Test coverage — do the tests actually exercise the changed lines? Best check: temporarily revert the fix in your head (or in a scratch copy) and ask whether the tests would still pass. Missing edge-case tests.
4. Style / convention — only what the repo's own conventions say; never argue formatting a linter handles.

Self-check before keeping a finding: how exactly does it break (concrete input → wrong result)? Is it already guarded outside the diff? What would the author reply? If you cannot answer, drop it. A false positive costs more than a missed nit.

## Output — write `review.json` (and nothing else) in exactly this shape
{
  "pr": __PR__,
  "verdict": "ship" | "fix then ship" | "rework",
  "summary": "2-4 sentences in Thai: overall assessment and the single biggest reason for the verdict",
  "findings": [
    {
      "severity": "blocker" | "major" | "minor" | "nit",
      "path": "src/…",
      "line": 42,
      "title": "short claim",
      "how_it_breaks": "concrete input/state -> wrong result",
      "evidence": "trace step or file:line proving it",
      "fix": "smallest change that fixes it"
    }
  ]
}
- `line` = a line number in the NEW version of `path` inside the changed hunk when possible.
- Write findings text in Thai; keep code identifiers in English.
- If you find nothing, `findings` is `[]` and `summary` must say which paths you traced.
When `review.json` is written, reply with the single word DONE.
