---
name: debugging-protocol
description: Systematic debugging workflow with Codex escalation. Use this skill whenever investigating a bug, test failure, unexpected behavior, error message, or regression — even if the cause seems obvious. Especially important when a first fix attempt has already failed, since it defines when the "first stuck" second-opinion checkpoint fires and when to escalate to a deeper Codex root-cause analysis.
---

# Debugging Protocol

A systematic workflow for investigating and fixing bugs. The core discipline: **evidence before hypotheses, hypotheses before fixes, one variable at a time.** Most debugging time is wasted by skipping straight to fixes based on pattern-matching — this protocol exists to prevent that.

## Phase 0: Reproduce

Never debug what you cannot reproduce.

1. Run the failing case and capture the exact output (error message, stack trace, wrong value)
2. Determine reproducibility: always / intermittent / environment-dependent
3. Reduce to the smallest reliable reproduction (smallest input, fewest steps)
4. Record the reproduction command — every later hypothesis test reruns exactly this

If the bug is intermittent, do NOT proceed as if it were deterministic. Run the repro N times to estimate frequency first; otherwise "the fix worked" is indistinguishable from "it didn't fire this time."

## Phase 1: Gather Evidence

Collect facts before forming opinions:

```bash
# What changed recently? Most bugs are regressions.
git log --oneline -15
git diff HEAD~5 --stat

# Find when it broke (when a good commit is known)
git bisect start <bad> <good>

# Read the actual error — the full stack trace, not the last line
# Check logs around the failure timestamp
```

- Read the failing code path top to bottom — do not skim
- Check assumptions at the boundary: actual input values, env vars, config, dependency versions
- For Claude Code hook/tooling issues, check `~/.claude/logs/`

## Phase 2: Isolate

Narrow the search space by bisection:

- **In time**: `git bisect` between known-good and known-bad commits
- **In space**: disable/stub half of the involved components, see if the bug persists, recurse
- **In data**: shrink the failing input until removing anything makes the bug disappear

The goal is a statement like "the bug is in function X when input has property Y" — not "somewhere in the auth flow."

## Phase 3: Hypothesis Loop

For each iteration:

1. **State the hypothesis explicitly** — "X is null here because the cache returns stale data"
2. **Define the test before testing** — what observation would confirm vs. refute it
3. **Test the cheapest hypothesis first** (log statement / assertion / debugger, not a rewrite)
4. **Change ONE variable at a time** — if you change two things and it works, you don't know which one mattered, and one of them may be a new bug
5. **Record the attempt** in the attempt log (below) — this becomes the escalation prompt if needed

A hypothesis that is refuted is progress. A fix applied without a confirmed hypothesis is a guess.

### Attempt Log

Keep a running log during any non-trivial debugging session:

```markdown
## Debug log: <one-line bug description>

- Repro: <command> → <observed failure>
- Attempt 1: <hypothesis> → <change made> → <result: refuted/confirmed/inconclusive>
- Attempt 2: ...
```

Why: it prevents retrying refuted ideas, and it is exactly the "Attempts so far" section Codex needs on escalation.

## Phase 4: Fix & Verify

1. Write a failing test that captures the bug BEFORE fixing (per the tdd-workflow skill) — if you can't write a failing test, you haven't isolated the bug
2. Apply the minimal fix for the root cause, not the symptom
3. Confirm: the new test passes, the original repro passes, the full test suite passes
4. Ask: "could this same root cause exist elsewhere?" — grep for the pattern

## Escalation

Two triggers, of different weight. This skill says _when_ each fires. The order of calls and who
stands in when a consultant is unavailable live in `~/.claude/rules/second-opinion.md`; the
`codex exec` command shape lives in the `codex-consultation` skill.

### First stuck → second-opinion checkpoint

When the first fix attempt fails, or the hypothesis loop stops narrowing the search, you are at
the "when first stuck" checkpoint of `~/.claude/rules/second-opinion.md`. Follow that rule as
written — its pair (advisor + Codex), its order of calls and its fallback table — rather than
consulting Codex alone. Give the cross-vendor half the attempt log as its evidence. A failed attempt
at a trivial or mechanical edit (a typo, a stale import, an obvious one-line fix) does not trigger
the checkpoint: `second-opinion.md` skips it for such edits, and so does this section.

### 2 consecutive failed fixes → deeper Codex root-cause run

After **2 consecutive failed fix attempts on the same issue**, stop iterating: this is the
heavier escalation the `codex-consultation` skill and `second-opinion.md` assign to Codex — a
dedicated root-cause analysis, not another checkpoint opinion. Continuing to guess past this
point burns context and compounds confusion. If Codex cannot run, the fallback table in
`second-opinion.md` decides who takes its place.

Run the `codex-consultation` skill's invocation contract yourself, with `run_in_background: true`
(`codex exec -s read-only` behind the secret-scan gate; leave `-m` unset and add no effort
override, so the Codex defaults — `xhigh` reasoning — apply), and wait for the task
notification. The `codex-delegator` agent keeps the exchange out of your context instead, but it
runs Codex in the foreground at `model_reasoning_effort=medium` to fit its 10-minute Bash limit —
a shallower pass than this escalation usually wants. The background run is the main session's
path: a subagent cannot wait on a background task, so a subagent that reaches this point stops and
returns its attempt log to the caller instead. Build the prompt from the attempt log and write it
to the prompt file:

```text
Goal: <what correct behavior looks like>
Context: <files involved, error output, stack trace, repro command>
Constraints: <architecture/conventions that limit the fix>
Done when: <observable success condition, e.g. "test X passes">
Attempts so far:
  1. <hypothesis> → <change> → <result>
  2. <hypothesis> → <change> → <result>
Important: コード生成は不要。根本原因の分析と修正戦略の提案のみ。
```

Then:

1. Validate Codex's analysis against the evidence yourself — does it explain ALL observed symptoms, not just some?
2. Implement the strategy with Claude, re-running the Phase 0 repro to verify
3. Continue the same thread with `codex exec resume <thread-id>` (the codex-consultation skill's follow-up turn) if the strategy fails, rather than starting over

## Anti-Patterns

- **Shotgun debugging** — changing several things at once hoping something sticks
- **Symptom patching** — adding a null check where the value should never be null; ask why it is null
- **"It works now"** — a fix you cannot explain is a bug that will return; identify the mechanism
- **Retrying refuted hypotheses** — the attempt log exists to prevent this
- **Debugging without a repro** — reading code hoping to spot the bug is a last resort, not step one
- **Silent fixes for intermittent bugs** — without a frequency baseline, you cannot claim the fix worked

## Quick Reference

| Situation                | Action                                                                |
| ------------------------ | --------------------------------------------------------------------- |
| Bug report received      | Phase 0: reproduce before anything else                               |
| Regression suspected     | `git log` / `git bisect` first                                        |
| Hypothesis confirmed     | Failing test → minimal fix → full verification                        |
| First fix attempt failed | "First stuck" checkpoint → follow `~/.claude/rules/second-opinion.md` |
| 2 fix attempts failed    | STOP → deeper Codex root-cause run with the attempt log               |
| Codex strategy received  | Validate against evidence → implement → verify with original repro    |
