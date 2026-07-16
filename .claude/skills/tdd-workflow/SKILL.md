---
name: tdd-workflow
description: Use this skill when writing new features, fixing bugs, or refactoring code. Enforces test-driven development across unit, integration, and E2E tests, and defines the coverage policy.
---

# Test-Driven Development Workflow

The methodology of TDD: RED-GREEN-REFACTOR, which test to write when, and the coverage
policy. Language-neutral by design — it does not carry framework mechanics.

## Relationship to language-specific testing skills

This skill is the **methodology**. For framework-specific mechanics, pair it with the
matching skill rather than duplicating their guidance here:

| Need                                                    | Skill                  |
| ------------------------------------------------------- | ---------------------- |
| Vitest / Jest, mocking (`vi.mock`, MSW), async patterns | **typescript-testing** |
| Table-driven tests, subtests, benchmarks, fuzzing       | **golang-testing**     |
| E2E journeys, artifacts, flake quarantine               | **e2e-runner** agent   |

If you are reaching for a mock, a matcher, or a test-runner flag, you want one of those —
not this skill. The `/tdd`, `/go-test`, and `/test-coverage` commands drive this workflow.

## When to Activate

- Writing new features or functionality
- Fixing bugs or issues
- Refactoring existing code

## Core Principles

### 1. Tests BEFORE Code

ALWAYS write tests first, then implement code to make tests pass.

A test written after the implementation tends to assert what the code _does_, not what it
_should do_ — it locks in bugs instead of catching them.

### 2. Coverage Requirements

**This section is the single source of truth for the coverage policy.** Other skills,
agents, and commands reference it rather than restating a number — if the policy changes,
it changes here only.

**The project's own gate always wins.** Before quoting any number below, check the
project's CI config and test tooling (`.github/workflows/`, `.coveragerc`, `pytest.ini`,
`jest.config`, `vitest.config`). If the project enforces a threshold, that is the
threshold — the defaults here apply only where the project sets none. Never report
"coverage requirement met" against a default that is looser than the project's gate.

Defaults, absent a project gate:

| Code type               | Target  |
| ----------------------- | ------- |
| Critical business logic | 100%    |
| Public APIs             | 90%+    |
| General code            | 80%+    |
| Generated code          | Exclude |

Beyond the number:

- All edge cases covered
- Error scenarios tested
- Boundary conditions verified

Coverage is a floor, not a goal. A green percentage with no error-path assertions is a
worse signal than a lower number with meaningful ones.

### 3. Test Types — which to write when

Pick the cheapest test that can actually fail for the reason you care about:

| Test type       | Covers                                       | Use when                                                  |
| --------------- | -------------------------------------------- | --------------------------------------------------------- |
| **Unit**        | Pure functions, business rules, edge cases   | The logic has branches worth enumerating. Default choice. |
| **Integration** | API endpoints, DB access, service boundaries | The bug would live _between_ units, not inside one.       |
| **E2E**         | Critical user flows end to end               | The flow's value is the wiring itself. Keep these few.    |

Push coverage down the pyramid: an assertion that can be made in a unit test should not be
made in an E2E test, which is slower and fails for unrelated reasons.

## The Cycle

### Step 1: Define the behavior

State it as an observable claim before writing any code:

```
As a [role], I want to [action], so that [benefit]
```

Then enumerate the cases worth asserting: the happy path, the boundaries (empty, null,
max), and the error paths.

### Step 2: RED — write a failing test

Write the test and **run it**. It must fail, and it must fail for the _expected reason_.

A test that passes before the implementation exists is testing nothing — it is the single
most common way a TDD cycle silently becomes worthless. If it passes, fix the test.

### Step 3: GREEN — minimal implementation

Write the least code that makes the test pass. Not the general solution — the minimal one.
Generality comes in the refactor step, driven by the next failing test.

### Step 4: Verify it passes

Re-run. The new test passes, and every previously passing test still passes.

### Step 5: REFACTOR — with the net in place

Improve naming, remove duplication, simplify structure — while the tests stay green.

Refactoring means behavior does not change: if a test needs editing to keep passing, that
is not a refactor, that is a behavior change, and it needs its own RED step first.

### Step 6: Verify coverage

Run the project's coverage command and check it against the threshold (see Coverage
Requirements above — the project's own gate wins).

## Wiring a Coverage Gate

An illustration of _how_ to wire a gate, not a statement of the policy — the numbers come
from Coverage Requirements above, or from the project's existing config if it has one. A
project may also stack gates (e.g. an aggregate floor plus a stricter per-file floor,
since an aggregate hides individual files below it):

```json
{
  "coverageThresholds": {
    "global": { "branches": 80, "functions": 80, "lines": 80, "statements": 80 }
  }
}
```

## Common Mistakes

### Testing implementation details

Assert what the caller can observe, not internal state. A test coupled to internals fails
on every refactor while catching no real bugs — the exact inverse of what it is for.

```
❌ assert component.internalState.count == 5     # reaches inside the unit
✅ assert rendered.text contains "Count: 5"      # what the caller actually sees
```

(Pseudocode — the real matchers live in the language-specific testing skill.)

### No test isolation

Each test sets up its own data and cleans up after itself. Tests that depend on execution
order fail mysteriously when one is skipped, when they run in parallel, or when the suite
is re-ordered.

### Asserting only the happy path

The branch that is never asserted is the branch that ships broken. Error paths and
boundaries are where bugs live; a coverage percentage without them is theatre.

### Skipping the RED step

"I know this test will fail" is how tests that never fail get written. Run it and watch it
fail — that is the only proof the test is wired to the code at all.
