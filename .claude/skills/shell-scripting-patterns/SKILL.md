---
name: shell-scripting-patterns
description: Bash/zsh scripting best practices for dotfiles management and Claude Code hooks. Use this skill whenever writing or modifying shell scripts (.sh files), Claude Code hooks (PreToolUse/PostToolUse/Stop), zsh configs (.zshrc, .zprofile), or anything under .claude/hooks/ — even for small edits, since shell quoting and error-handling mistakes are easy to introduce silently.
---

# Shell Scripting Patterns

Best practices for bash/zsh scripts, with specific patterns for Claude Code hooks and dotfiles maintenance on macOS.

## Script Header & Strict Mode

Every bash script starts with:

```bash
#!/bin/bash
set -euo pipefail
```

- `-e` exits on error, `-u` errors on undefined variables, `-o pipefail` makes a pipeline fail if any stage fails
- Why: without these, a failed `cd` or a typo'd variable silently continues and corrupts state downstream

When a non-zero exit is expected (grep finding nothing, optional commands), handle it explicitly instead of dropping strict mode:

```bash
# grep returns 1 on no-match — don't let -e kill the script
matches=$(grep -c "pattern" file || true)

if ! command -v terminal-notifier >/dev/null 2>&1; then
  # fallback path
fi
```

Note: hooks in this repo intentionally use `set -e` without `-u`/`pipefail` when they must be fail-open (see Hook Patterns below). Choose deliberately, not by omission.

## Quoting

Quote every expansion unless you explicitly want word splitting:

```bash
# ❌ WRONG: breaks on spaces, runs glob expansion
rm $FILE_PATH
[ -f $FILE_PATH ] && cat $FILE_PATH

# ✅ CORRECT
rm "$FILE_PATH"
[[ -f "$FILE_PATH" ]] && cat "$FILE_PATH"
```

- Prefer `[[ ]]` over `[ ]` in bash: no word splitting inside, supports `=~` regex and `&&`/`||`
- Use `"$@"` (never `$@` or `$*`) to forward arguments

## Variables & Functions

```bash
# Defaults for optional values (plays well with set -u)
timeout="${3:-5}"
LOG_DIR="${CLAUDE_LOG_DIR:-$HOME/.claude/logs}"

# Always declare function-local variables
log() {
  local lines
  lines=$(wc -l <"$LOG_FILE")
}
```

- Declare and assign separately when the value comes from a command: `local x; x=$(cmd)` — otherwise `local` masks the command's exit code
- UPPER_CASE for constants/environment, lower_case for locals

## Loops & File Iteration

```bash
# ❌ WRONG: parsing ls, breaks on spaces
for f in $(ls *.log); do ...

# ✅ CORRECT: globs with nullglob guard
shopt -s nullglob
for f in *.log; do
  process "$f"
done

# ✅ CORRECT: null-delimited find for recursive walks
while IFS= read -r -d '' f; do
  process "$f"
done < <(find . -name '*.sh' -print0)
```

## Cleanup & Temp Files

```bash
tmpfile=$(mktemp)
trap 'rm -f "$tmpfile"' EXIT
```

- `trap ... EXIT` runs on normal exit, errors, and Ctrl-C — cleanup never gets skipped
- Atomic file replacement: write to a temp file, then `mv` (see the log-rotation pattern below); `mv` on the same filesystem is atomic, partial writes never become visible

## macOS Portability

This environment is macOS (BSD userland), which differs from GNU/Linux:

```bash
# BSD sed requires an argument to -i (GNU does not)
sed -i '' 's/foo/bar/' file        # macOS
sed -i 's/foo/bar/' file           # GNU — breaks on macOS

# Prefer env shebang when bash version matters
#!/usr/bin/env bash               # picks up Homebrew bash (5.x)
#!/bin/bash                        # system bash is 3.2 (no associative arrays, no mapfile)
```

- `date`, `stat`, `grep -P` also differ between BSD/GNU — when a script must run on both, test on both or use portable flags only
- Check tool availability before use: `command -v jq >/dev/null 2>&1 || exit 0`

## Claude Code Hook Patterns

Hooks receive JSON on stdin and communicate via exit codes and stdout JSON. Patterns proven in `~/.claude/hooks/`:

### Reading hook input

```bash
input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
```

- Always use `// ""` or `// empty` fallbacks — the field may be absent for other tools matched by the same matcher

### Fail-open principle

A hook that crashes blocks Claude's entire tool call. Anything irrelevant or unexpected exits 0 early:

```bash
# Not the command we care about → let it through immediately
echo "$cmd" | grep -qE 'git[[:space:]]+push' || exit 0

# Missing input → not our business
[ -z "$FILE_PATH" ] && exit 0
[ ! -f "$FILE_PATH" ] && exit 0
```

### Exit code semantics

| Exit code | Meaning                                                                     |
| --------- | --------------------------------------------------------------------------- |
| 0         | Success / allow (stdout shown in transcript mode only)                      |
| 2         | Block; stderr is fed back to Claude for self-correction (used by `lint.sh`) |
| other     | Non-blocking error; stderr shown to user                                    |

### Structured decisions (PreToolUse)

To force a confirmation prompt instead of hard-blocking, emit JSON on stdout with exit 0:

```bash
jq -cn --arg reason "$reason" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $reason}}'
exit 0
```

`permissionDecision` is `"allow"`, `"deny"`, or `"ask"`. Build JSON with `jq -n --arg` — never by string interpolation, which breaks on quotes/newlines in the data.

### Logging with rotation

Hooks run on every matched tool call, so logs grow fast. Cap them:

```bash
LOG_FILE="$HOME/.claude/logs/myhook.log"
MAX_LOG_LINES=500
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG_FILE"
  local lines
  lines=$(wc -l <"$LOG_FILE")
  if [ "$lines" -gt "$MAX_LOG_LINES" ]; then
    tail -n "$MAX_LOG_LINES" "$LOG_FILE" >"${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
  fi
}
```

### Stop-hook loop guard

A Stop hook that blocks causes Claude to run again, which fires the Stop hook again. Always check `stop_hook_active`:

```bash
stop_active=$(echo "$input" | jq -r '.stop_hook_active // false')
[ "$stop_active" = "true" ] && exit 0
```

## zsh-Specific Notes (dotfiles configs)

- `.zshrc` / `.zprofile` are zsh, not bash — `setopt` instead of `shopt`, arrays are 1-indexed
- zsh does NOT word-split unquoted variables by default (unlike bash) — but quote anyway; the file may be sourced into other contexts
- Guard interactive-only config: `[[ -o interactive ]] || return`
- Keep `.zshrc` fast: lazy-load heavy completions, profile with `zmodload zsh/zprof`

## Linting & Testing

```bash
# Lint every script before committing
shellcheck script.sh

# Consistent formatting (2-space indent used in this repo)
shfmt -i 2 -w script.sh

# Test hooks by piping synthetic hook JSON
echo '{"tool_input":{"command":"git push origin main"}}' | ./git-push-review.sh

# bats for regression tests
bats tests/
```

ShellCheck findings are almost always real bugs (SC2086 unquoted expansion, SC2155 local masking exit codes). Fix them rather than disabling, and leave a comment when a disable is genuinely needed.

## Checklist

Before committing a shell script:

- [ ] `set -euo pipefail` (or a deliberate, commented choice not to)
- [ ] All expansions quoted
- [ ] `shellcheck` clean
- [ ] Works with BSD (macOS) userland
- [ ] Temp files cleaned up via `trap EXIT`
- [ ] Hooks: fail-open on irrelevant input, JSON built with `jq -n`, logs rotated
- [ ] Tested with realistic stdin (hooks) or arguments
