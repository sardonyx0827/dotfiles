---
description: Enforce TDD workflow for Go. Write table-driven tests first, then implement, and verify coverage with go test -cover.
---

# /go-test

Invokes the **tdd-guide** agent to drive test-driven development for Go code
(`$ARGUMENTS`), using idiomatic Go testing patterns.

## What runs

The agent loads the canonical workflow from the **tdd-workflow** skill and the Go
mechanics from the **golang-testing** skill, then applies them: scaffold the signature,
write a failing table-driven test, verify it fails for the right reason, implement
minimally, refactor, and verify coverage against the project's threshold.

The RED-GREEN-REFACTOR cycle and coverage policy live in **tdd-workflow**. Table-driven
tests, subtests, benchmarks, fuzzing, and the `go test` coverage commands live in
**golang-testing**. This command restates neither.

## When to use

- Implementing a new Go function or package
- Adding tests to existing Go code that lacks them
- Fixing a bug — the reproducing test is written first
- Building critical business logic

## Go-specific reminders

- Run `go test -race ./...` for anything touching goroutines or shared state
- Do not use `time.Sleep` to synchronize tests — synchronize explicitly
- Test exported behavior, not unexported functions

## Related

- `/go-build` — fix build errors
- `/go-review` — review code after implementation
- `/verify` — run the full verification loop
