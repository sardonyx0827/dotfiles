---
name: verification-loop
description: Build, type-check, lint, test, secret-grep, and diff-review quality gate for code changes. Use when you have finished a feature or significant change, are about to open a PR, or want to confirm quality gates pass after refactoring — running the full verify sequence and reporting pass/fail per phase. Detects the project's own toolchain rather than assuming one. For the one-shot slash command, see /verify.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob
---

# Verification Loop Skill

A comprehensive verification system for Claude Code sessions.

## When to Use

Invoke this skill:

- After completing a feature or significant code change
- Before creating a PR
- When you want to ensure quality gates pass
- After refactoring

## Verification Phases

### Phase 0: Detect the Toolchain

Run the project's own commands, not a hardcoded stack. Detect before running:

| Marker                        | Build            | Types              | Lint            | Test            | Coverage                        |
| ----------------------------- | ---------------- | ------------------ | --------------- | --------------- | ------------------------------- |
| `package.json`                | `<pm> run build` | `npx tsc --noEmit` | `<pm> run lint` | `<pm> test`     | `<pm> test -- --coverage`       |
| `go.mod`                      | `go build ./...` | (compiler)         | `go vet ./...`  | `go test ./...` | `go test -cover ./...`          |
| `pyproject.toml` / `setup.py` | (n/a)            | `pyright .`        | `ruff check .`  | `pytest`        | `pytest --cov`                  |
| `Cargo.toml`                  | `cargo build`    | (compiler)         | `cargo clippy`  | `cargo test`    | `cargo llvm-cov` (if installed) |

`<pm>` is the package manager implied by the lockfile (`pnpm-lock.yaml` → pnpm,
`yarn.lock` → yarn, `package-lock.json` → npm). A `scripts` entry in `package.json`
always wins over the table — read it first. If the repo defines its own gate (a
`Makefile` target, a `verify` script, a CI workflow), run that instead of the table.

### Phase 1: Build

Run the detected build command. If the build fails, STOP and fix before continuing —
every later phase reports noise against a broken tree.

### Phase 2: Type Check

Run the detected type check. Report all type errors; fix critical ones before continuing.

### Phase 3: Lint

Run the detected lint command.

### Phase 4: Test Suite

Run the detected test command with coverage **where the toolchain produces it natively**.
Coverage is not universally available: Rust needs `cargo-llvm-cov` or `cargo-tarpaulin`
installed, and a bare `go test` reports none without `-cover`. If the tool is not present,
report coverage as N/A — never infer a number you did not read from a report.

Check any coverage figure against the project's threshold. Policy and defaults: the
tdd-workflow skill. The project's own CI gate always wins.

Report:

- Total tests: X
- Passed: X
- Failed: X
- Coverage: X%

### Phase 5: Secret & Debug-Statement Grep

```bash
# Secret-ish identifier assigned a string literal.
# -i is load-bearing (apiKey); the optional `: type` group catches `api_key: string = "..."`;
# `:=` catches Go. Without those three, this silently misses most real hits.
grep -rniE '(sk-[a-z0-9]{16,}|api[_-]?key|secret|passwd|password|token|credential)[a-z0-9_]*(\s*:\s*[a-z_<>\[\]| ]+)?\s*(:=|=|:)\s*["'"'"'][^"'"'"']{4,}' \
  --exclude-dir={.git,node_modules,dist,build,vendor} . 2>/dev/null | head -20

# Debug statements left behind (adjust to the project's language)
grep -rnE 'console\.log|debugger;|fmt\.Print|pdb\.set_trace|binding\.pry' \
  --exclude-dir={.git,node_modules,dist,build,vendor} . 2>/dev/null | head -20
```

**This is a smoke test, not a security audit.** It catches literals assigned in tracked
source; it does not catch secrets in history, entropy-based keys, base64 blobs, `.env`
files, or anything in a language whose pattern is not listed above. A clean result means
"these greps found nothing" — never report it as "no secrets present". For real coverage
run the repo's own scanner (`gitleaks detect`, `trufflehog`, `git secrets --scan`) if it
has one, and use the **security-review** skill for changes touching auth, input handling,
or secrets.

### Phase 6: Diff Review

```bash
# Show what changed
git diff --stat
git diff HEAD~1 --name-only
```

Review each changed file for:

- Unintended changes
- Missing error handling
- Potential edge cases

## Output Format

After running all phases, produce a verification report:

```
VERIFICATION REPORT
==================

Build:      [PASS/FAIL]
Types:      [PASS/FAIL] (X errors)
Lint:       [PASS/FAIL] (X warnings)
Tests:      [PASS/FAIL] (X/Y passed, Z% coverage)
Secret grep: [CLEAN/HITS] (X hits — smoke test only, not an audit)
Diff:       [X files changed]

Overall:    [READY/NOT READY] for PR

Issues to Fix:
1. ...
2. ...
```

Report a phase you did not run as SKIPPED with the reason (e.g. "no build script"),
never as PASS. A phase that cannot run has not passed.

## When to Re-run

Verification is event-driven — run it at these points, not on a timer:

- After completing a function or component
- Before moving to the next task
- Before creating a PR

Run: `/verify`

## Integration with Hooks

This skill complements PostToolUse hooks but provides deeper verification.
Hooks catch issues immediately; this skill provides comprehensive review.
