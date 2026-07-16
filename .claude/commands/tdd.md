---
description: Enforce test-driven development workflow. Scaffold interfaces, generate tests FIRST, then implement minimal code to pass, and verify coverage.
---

# /tdd

Invokes the **tdd-guide** agent to drive test-driven development for `$ARGUMENTS`.

## What runs

The agent loads the canonical workflow from the **tdd-workflow** skill (paired with
**typescript-testing** or **golang-testing** for the target stack), then applies it:
scaffold the interface, write failing tests, verify they fail for the right reason,
implement minimally, refactor, and verify coverage against the project's threshold.

The RED-GREEN-REFACTOR cycle, coverage policy, testing patterns, and anti-patterns are
defined in the skill — this command does not restate them. Read the skill for the
methodology; read `agents/tdd-guide.md` for the enforcement stance and sign-off
checklist.

## When to use

- Implementing a new feature, function, or component
- Fixing a bug — the reproducing test is written first
- Refactoring code that lacks a characterization test
- Building critical business logic

## Related

- `/go-test` — the same workflow for Go (table-driven tests, `-race`, `-cover`)
- `/test-coverage` — analyze existing coverage and fill gaps, without the full cycle
