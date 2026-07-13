# Hooks System

## Hook Types

- **PreToolUse**: Before tool execution (validation, parameter modification)
- **PostToolUse**: After tool execution (auto-format, checks)
- **Stop**: When session ends (final verification)

## Current Hooks (in ~/.claude/settings.json)

### PreToolUse

- **bash-review** (`hooks/bash-review.py`, matcher: `Bash`):
  Reviews every Bash command before execution in three tiers — static
  allow/deny fast-paths (no AI call), a parallel Gemini+Codex AND-gate for
  high-risk commands, and a Gemini→Codex cascade for the low-risk majority.
  Logs to `~/.claude/logs/bash-review.log` and `/tmp/claude_hooks/logs/`.
  See **bash-review — design rationale & threat model** below for why the
  tiers are structured this way and what they do (and do not) defend against.
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

Given that model, the flow is tuned by **how dangerous the command is**, keeping
latency low for the common case:

1. **Fast pre-block / safe-skip (no AI call).** Obvious-dangerous prefixes
   (`curl`, `dd`, `rm -rf /`, …) are denied outright and obvious read-only
   commands (`ls`, `cat`, `git status`, …) are allowed — both without any
   API/CLI round-trip. These lists are _convenience fast-paths, not boundaries_:
   an absolute path like `/usr/bin/curl` intentionally falls through to review,
   and anything touching a sensitive path (`.env`, `id_rsa`, `.ssh`, …) is forced
   to review even when its prefix looks safe.
2. **High-risk commands → parallel two-model AND-gate.** Commands that are
   dangerous _depending on context_ (`rm -r`, package installs like
   `pip install` / `npm i`, …; see `high_risk_label`) are reviewed by Gemini
   **and** Codex run _in parallel_, and their verdicts are combined as an
   AND-gate (`combine_high_risk_verdicts`): `allow` only when **both** return
   `ALLOW`, `deny` only when **both** return `DENY`, and everything else —
   disagreement, `ASK`, or `ERROR` — resolves to `ask` with both verdicts
   attached. A single model's approval never auto-runs a high-risk command;
   convincing just one is not enough. Reserving both models for this
   dangerous-but-ambiguous minority is what keeps the AND-gate's latency cost off
   the common path.
3. **Low-risk commands → Gemini tier-1 cascade.** Everything else — the vast
   majority — is gated by a single call to a fast, cheap model. A Gemini `ALLOW`
   short-circuits and Codex is never called; that short-circuit is the main
   latency win. When Gemini returns `ASK` / `DENY` / `ERROR`, the command is
   escalated to the slower but more capable Codex, and how Codex's verdict is
   applied depends on how hard Gemini's flag was — a lone `ALLOW` clears a soft
   flag but **not** an explicit refusal:
   - Gemini **`ASK` / `ERROR`** (soft — uncertainty or unavailability, not a
     refusal): a Codex `ALLOW` resolves it to `allow`.
   - Gemini **`DENY`** (an explicit refusal): a lone Codex `ALLOW` does **not**
     override it back to `allow`. The disagreement resolves to `ask`, put to the
     human with both verdicts attached. Letting one model's `ALLOW` override the
     other's `DENY` would turn the cascade into an OR-gate — convincing _either_
     model would be enough to run the command, the opposite of what a second
     opinion is for.
   - Codex `DENY` → `deny`; Codex `ASK` → `ask`; Codex `ERROR` → fall back to
     Gemini's verdict (`deny` unless Gemini's flag was soft).
4. **Fail toward the human.** Malformed stdin, an unavailable Codex, or any
   exception raised before a decision is emitted resolves to `ask` / `deny`,
   never a silent allow.

**Accepted tradeoffs (chosen, not overlooked):**

- On the **low-risk path**, a command that convinces the _single_ tier-1 model is
  allowed without a second opinion — accepted in exchange for latency. High-risk
  commands never get this treatment (step 2).
- LLM reviewers can be swayed by prompt-injection text inside the command; this
  layer is heuristic by nature, which is exactly why the enforced boundary lives
  in `permissions.deny`.
- Safe-skipped `git` / `jq` / … assume a **trusted** repository. Config-driven
  execution (`.git/config` `core.pager`, `[alias]`, `diff.external`) is out of
  scope — verifying it on every call would defeat the point of the skip. See the
  inline comments in `_bash_review_common.py`.

> The **low-risk cascade** (step 3) is single-model-with-short-circuit on purpose:
> don't "harden" _it_ into an AND-gate without re-checking the latency budget — a
> two-model review in front of _every_ Bash call would be too slow. The
> **high-risk tier** (step 2) already _is_ a parallel AND-gate; that is the whole
> point of spending both models only on the dangerous-but-context-dependent
> minority.

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
