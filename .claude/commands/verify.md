---
description: Run the full local quality gate in order - build, type check, lint, tests, secret grep, and diff review. Reports pass/fail per step and stops on build failure.
---

# Verification Command

Run the local quality gate over the current codebase state.

## Instructions

Follow the **verification-loop** skill — it is the single source of truth for the phase
order, the toolchain detection table, the grep patterns, and the report format. Do not
restate or re-derive those here.

Two rules decide whether the resulting report is trustworthy:

- **Stop on build failure.** Every later phase reports noise against a broken tree.
- **A phase that could not run is SKIPPED, with the reason — never PASS.**

## Arguments

`$ARGUMENTS` selects how far to go:

| Value        | Phases                                                       |
| ------------ | ------------------------------------------------------------ |
| `quick`      | Build + type check                                           |
| `pre-commit` | Build, types, lint, tests                                    |
| `full`       | All phases (default)                                         |
| `pre-pr`     | All phases, plus the **security-review** skill over the diff |

`pre-pr` escalates to the security-review skill because the verification-loop Phase 5 is
a secret grep — a smoke test, not an audit. For changes touching auth, user input,
secrets, or payments, run the **security-reviewer** agent regardless of the argument.
