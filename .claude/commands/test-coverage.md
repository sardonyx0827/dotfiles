---
description: Analyze test coverage, identify files below the project's threshold, and generate missing tests for uncovered code paths.
---

# /test-coverage

Analyze test coverage and fill the gaps for `$ARGUMENTS` (default: the whole project).

The coverage policy — targets per code type, and the rule that the project's own CI gate
overrides the defaults — is defined in the **tdd-workflow** skill. Read it first and use
the project's actual threshold, not a remembered number.

1. Determine the threshold: check the project's CI config and test tooling
   (`.github/workflows/`, `.coveragerc`, `pytest.ini`, `jest.config`, `vitest.config`)
2. Run tests with coverage (e.g. `npm test -- --coverage`, `go test -cover ./...`)
3. Analyze the coverage report
4. Identify files below the threshold
5. For each under-covered file:
   - Analyze untested code paths
   - Generate unit tests for functions
   - Generate integration tests for APIs
   - Generate E2E tests for critical flows
6. Verify new tests pass
7. Report before/after coverage against the threshold, quoting the measured numbers

Focus on:

- Error handling and failure paths — not just the happy path
- Edge cases (null, undefined, empty)
- Boundary conditions

Note: this command fills coverage gaps in existing code. For the full RED-GREEN-REFACTOR
cycle on new work, use `/tdd` (or `/go-test` for Go).
