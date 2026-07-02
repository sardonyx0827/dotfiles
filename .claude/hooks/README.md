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
  Standalone alternative implementations (not imported by `bash-review.py`;
  swap the settings.json command to use one): `claude-bash-review.py`
  (Claude CLI), `codex-bash-review.py` (Codex CLI),
  `gemini-api-bash-review.py` (Gemini API only).
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
