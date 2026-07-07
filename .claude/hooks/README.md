# Hooks System

## Hook Types

- **PreToolUse**: Before tool execution (validation, parameter modification)
- **PostToolUse**: After tool execution (auto-format, checks)
- **Stop**: When session ends (final verification)

## Current Hooks (in ~/.claude/settings.json)

### PreToolUse

- **bash-review** (`hooks/bash-review.py`, matcher: `Bash`):
  Reviews every Bash command before execution.
  Primary pass uses the Gemini API (high throughput); if Gemini returns
  ASK/DENY, a secondary pass re-checks with Codex. Logs to
  `~/.claude/logs/bash-review.log` and `/tmp/claude_hooks/logs/`.
  See **bash-review — design rationale & threat model** below for why the
  cascade is structured this way and what it does (and does not) defend against.
- **git-push-review** (`hooks/git-push-review.sh`, matcher: `Bash`,
  if: `Bash(git push*)`):
  Detects `git push` commands and forces a confirmation prompt
  (permissionDecision: ask) with a summary of the branch, commits,
  and diffstat about to be pushed. Enforces the pre-push review step
  of `rules/git-workflow.md`.

### PostToolUse

Matcher: `Write|Edit|MultiEdit` (runs in order):

1. **auto-format** (`hooks/auto-format.sh`):
   Extracts the edited file path from hook JSON and runs the matching
   formatter (prettier, etc.). Logs to `~/.claude/logs/format.log`.
2. **lint** (`hooks/lint.sh`):
   Static analysis after formatting. Exits with code 2 to feed errors
   back to Claude and trigger self-correction. Logs to
   `~/.claude/logs/lint.log`.

### Stop

- **stop-audit** (`hooks/stop-audit.sh`):
  Final verification gate when Claude finishes a turn. Scans modified
  and untracked files for leftover debug statements (`console.log` /
  `debugger` in JS/TS, `breakpoint()` / `pdb.set_trace()` in Python)
  and blocks once with the findings so Claude removes them.
  `stop_hook_active` guards against infinite loops.

## bash-review — design rationale & threat model

`bash-review` is a **guardrail against an over-eager agent**, not an adversarial
security boundary. Its goal is to stop _the assistant_ from running something
destructive or secret-leaking — not to withstand a human who is actively trying
to defeat it. The hard boundary is `permissions.deny` in `settings.json`
(`rm -rf`, `sudo`, `curl`/`wget`, reads of `*.key` / `.env`, …); this hook is an
advisory layer on top of it.

Given that model, the flow is tuned for **latency first**:

1. **Fast pre-block / safe-skip (no AI call).** Obvious-dangerous prefixes
   (`curl`, `dd`, `rm -rf /`, …) are denied outright and obvious read-only
   commands (`ls`, `cat`, `git status`, …) are allowed — both without any
   API/CLI round-trip. These lists are _convenience fast-paths, not boundaries_:
   an absolute path like `/usr/bin/curl` intentionally falls through to review,
   and anything touching a sensitive path (`.env`, `id_rsa`, `.ssh`, …) is forced
   to review even when its prefix looks safe.
2. **Gemini = tier-1 reviewer (the cascade).** Almost every command is benign,
   so it is gated by a single call to a fast, cheap model. A Gemini `ALLOW`
   short-circuits and Codex is never called. Reviewing _every_ command with two
   models would be too slow to sit in front of every Bash call — this
   short-circuit is the main latency win.
3. **Codex = tier-2 arbiter, only for the flagged minority.** When Gemini
   returns `ASK` / `DENY` / `ERROR`, the command is escalated to the slower but
   more capable Codex, whose verdict is final — **including overriding a Gemini
   `DENY` back to `ALLOW`**. This is deliberate: a throughput-tuned model
   over-flags, and re-checking only the flagged minority with a stronger model
   keeps false positives from becoming constant confirmation prompts. Trusting
   the better model on the hard cases is the whole point of the second tier.
4. **Fail toward the human.** Malformed stdin, an unavailable Codex, or any
   exception raised before a decision is emitted resolves to `ask` / `deny`,
   never a silent allow.

**Accepted tradeoffs (chosen, not overlooked):**

- A command that convinces the _single_ tier-1 model is allowed without a second
  opinion — accepted in exchange for latency.
- LLM reviewers can be swayed by prompt-injection text inside the command; this
  layer is heuristic by nature, which is exactly why the enforced boundary lives
  in `permissions.deny`.
- Safe-skipped `git` / `jq` / … assume a **trusted** repository. Config-driven
  execution (`.git/config` `core.pager`, `[alias]`, `diff.external`) is out of
  scope — verifying it on every call would defeat the point of the skip. See the
  inline comments in `_bash_review_common.py`.

> Do not "harden" this into an AND-gate (block unless _both_ models allow) without
> re-checking the latency budget: the two-tier cascade with a Gemini short-circuit
> is an intentional speed/precision tradeoff, not an oversight.

## Auto-Accept Permissions

Use with caution:

- Enable for trusted, well-defined plans
- Disable for exploratory work
- Never use dangerously-skip-permissions flag
- Configure permissions in `~/.claude/settings.json` instead

## TodoWrite Best Practices

Use TodoWrite tool to:

- Track progress on multi-step tasks
- Verify understanding of instructions
- Enable real-time steering
- Show granular implementation steps

Todo list reveals:

- Out of order steps
- Missing items
- Extra unnecessary items
- Wrong granularity
- Misinterpreted requirements
