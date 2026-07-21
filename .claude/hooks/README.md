# Hooks System

## Hook Types

- **PreToolUse**: Before tool execution (validation, parameter modification)
- **PostToolUse**: After tool execution (auto-format, checks)
- **Stop**: When session ends (final verification)

## Current Hooks (in ~/.claude/settings.json)

### PreToolUse

- **bash-review** (`hooks/bash-review.py` via `hooks/bash-review-launcher.sh`,
  matcher: `Bash`):
  Reviews every Bash command before execution — static allow/deny fast-paths
  (no AI call), a static secret pre-send scan that refuses to forward commands
  carrying raw credentials to any LLM, a parallel Gemini+Codex AND-gate for
  high-risk commands, and a Gemini→Codex cascade for the low-risk majority.
  Logs to `~/.claude/logs/bash-review.log` and `/tmp/claude_hooks/logs/`.
  `settings.json` launches it through the launcher, not bare `python3`: a hook
  command that cannot start (no `python3`, missing file) or crashes with an
  unexpected exit code is treated by Claude Code as a _non-blocking_ error —
  the command would run unreviewed. The launcher converts every such
  "review never happened" case into an explicit `ask`, and passes the normal
  vocabulary (exit 0 + decision JSON, exit 2 + stderr) through untouched.
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

## Shared libraries (`_`-prefixed, not hooks themselves)

These four files hold the per-file logic shared between the two sides — the
lint/format matrices and bash-review's verdict logic. They live here as the
real files; `.codex/hooks/` holds relative symlinks to them, so this shared
logic cannot drift apart. The hooks that consume it (`lint.sh`,
`auto-format.sh`, `bash-review.py`, …) are _not_ symlinked: each side keeps its
own copy, because Claude and Codex differ in how they receive targets, report
verdicts, and place hooks on events. See `.codex/hooks/README.md` for the full
table and those differences.

- **`_hook_common.sh`**: `hook_log` (timestamped, size-capped) and
  `hook_notify` (terminal-notifier / osascript / notify-send).
- **`_lint_common.sh`**: `hook_lint_file` — the per-language lint matrix.
- **`_format_common.sh`**: `hook_format_file` — the per-language formatter matrix.
- **`_bash_review_common.py`**: bash-review's verdict logic and review calls.

Contract for the shell ones: they define functions and nothing else. Sourcing
must not print, `mkdir`, or touch `set`/`IFS`/`trap`/cwd — the Codex `lint.sh`
sources them before `exec 1>/dev/null`, so any output at source time would land
in the structured-output channel Codex parses and fail the hook. Names are
namespaced `hook_`/`HOOK_` because bash is dynamically scoped and a collision
with a caller's local fails silently.

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
2. **Secret pre-send scan (no external call).** Before any command is handed to
   an LLM, a static scan (`scan_secrets`) checks the command _and_ the full
   `tool_input` for raw credential **values** — known-format tokens (GitHub /
   AWS / Google / OpenAI / Slack / Stripe), PEM private keys, JWTs,
   `Authorization: Bearer`/`Basic` headers, `user:pass@` URLs, and
   credential-shaped assignments / long flags (`PGPASSWORD=…`, `--password …`).
   A hit fails closed to `ask` (Claude) / block (Codex) **without calling Gemini
   or Codex**, so a secret never leaves the machine for review; the
   reason / notification / logs carry only a generic category label, and the raw
   command is redacted out of the local audit logs (that redaction also applies
   when the same secret-bearing command is caught by the deny / safe-skip
   fast-paths above). This is **value-only** by design: sensitive _paths_
   (`cat ~/.aws/credentials`) reveal intent, not the secret itself, so they still
   go to normal review (step 1's sensitive-path rule). The threat model is
   _accidental_ secret inclusion by the agent, not deliberate obfuscated
   exfiltration — so detection is high-precision prefix / structural matching,
   not entropy or de-obfuscation.
3. **High-risk commands → parallel two-model AND-gate.** Commands that are
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
4. **Low-risk commands → Gemini tier-1 cascade.** Everything else — the vast
   majority — is gated by a single call to a fast, cheap model. A Gemini `ALLOW`
   short-circuits and Codex is never called; that short-circuit is the main
   latency win. When Gemini returns `ASK` / `DENY` / `ERROR`, the command is
   escalated to the slower but more capable Codex, and how Codex's verdict is
   applied depends on whether Gemini's flag carried an _opinion_ — a lone
   `ALLOW` clears a no-opinion flag but **not** a flag that voiced caution:
   - Gemini **`DENY`** (an explicit refusal) or **`ASK`** (the model's explicit
     "a human should confirm this" — the review prompt defines `ASK` that way):
     a lone Codex `ALLOW` does **not** override it. The disagreement resolves to
     `ask`, put to the human with both verdicts attached. Letting one model's
     `ALLOW` clear the other's caution would turn the cascade into an OR-gate —
     convincing _either_ model would be enough to run the command, the opposite
     of what a second opinion is for — and would apply "fail toward the human"
     inconsistently across `ASK` vs `DENY`.
   - Gemini **`ERROR`** (unavailability — no opinion at all, not a verdict): a
     Codex `ALLOW` resolves it to `allow`. Codex is simply the sole reviewer
     left, so its approval carries (graceful degradation, no added friction).
   - Codex `DENY` → `deny`; Codex `ASK` → `ask`; Codex `ERROR` → fall back to
     Gemini's verdict (`deny` if Gemini said `DENY`, otherwise `ask`).
5. **Fail toward the human.** Malformed stdin, an unavailable Codex, or any
   exception raised before a decision is emitted resolves to `ask` / `deny`,
   never a silent allow. This includes the review **never starting**:
   `bash-review-launcher.sh` turns a missing `python3`, a missing
   `bash-review.py`, or a crash (any exit other than 0/2) into an explicit
   `ask`, where the bare `python3 …/bash-review.py` wiring would have been
   downgraded by Claude Code to a non-blocking error — i.e. fail-open. What
   the launcher cannot cover is its own failure to start; that residual case
   is bounded by `permissions.deny`, as below.

**Sandbox is off on purpose.** Claude Code's `sandbox` feature is disabled in
`settings.json` (`"sandbox": {"enabled": false}`): the enforcement boundary is
`permissions.deny` plus these hooks. While disabled, sibling keys under
`sandbox` (`network.allowedDomains`, `excludedCommands`) are never consulted —
they are inert, not an active allowlist, so don't read them as protection.

**Accepted tradeoffs (chosen, not overlooked):**

- On the **low-risk path**, a command that convinces the _single_ tier-1 model is
  allowed without a second opinion — accepted in exchange for latency. High-risk
  commands never get this treatment (step 3).
- LLM reviewers can be swayed by prompt-injection text inside the command; this
  layer is heuristic by nature, which is exactly why the enforced boundary lives
  in `permissions.deny`.
- The **secret pre-send scan** (step 2) is deliberately narrow: it matches
  credential _values_ by known format and a few structural shapes, tuned for
  _accidental_ inclusion. It does not defeat deliberate obfuscation (base64,
  quote-splitting, `$(…)`), does not catch very short opaque values (`< 8`
  chars) or short-flag secrets (`-p<pw>`, which collide with `mkdir -p` etc.),
  and leaves sensitive _paths_ to normal review. A miss falls through to the
  usual AI review, not to a silent send of an unreviewed secret; a false
  positive only costs an `ask`.
- Safe-skipped `git` / `jq` / … assume a **trusted** repository. Config-driven
  execution (`.git/config` `core.pager`, `[alias]`, `diff.external`) is out of
  scope — verifying it on every call would defeat the point of the skip. See the
  inline comments in `_bash_review_common.py`.

> The **low-risk cascade** (step 4) is single-model-with-short-circuit on purpose:
> don't "harden" _it_ into an AND-gate without re-checking the latency budget — a
> two-model review in front of _every_ Bash call would be too slow. The
> **high-risk tier** (step 3) already _is_ a parallel AND-gate; that is the whole
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
