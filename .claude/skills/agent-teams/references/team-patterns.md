## references/team-patterns.md (Team Composition Pattern Collection)

### Design Principles
- Limit to patterns that have been proven effective in practice
- Each pattern includes four elements: "When to use", "Composition", "Prompt example", and "Notes"
- Include copy-pasteable prompt examples

### Contents

#### Pattern 1: Code Review Team (Read-only · Low Risk)
```
Recommendation: ★★★★★ (Ideal for Agent Teams beginners)
Team composition:
  Lead: Integration reviewer (aggregate findings and prioritize)
  Teammate1: Security reviewer
  Teammate2: Performance reviewer
  Teammate3: Test coverage reviewer

When to use:
  - Reviewing PRs/MRs
  - Quality audits of large codebases
  - Current-state analysis before refactoring

Prompt example:
  "Create an agent team to review this PR.
   Conduct parallel reviews from the perspectives of security, performance, and test coverage,
   then consolidate the results.
   Target: [branch name or file path]"

Notes:
  - No risk of file conflicts because code is not modified
  - Recommended for Agent Teams beginners
```

#### Pattern 2: Hypothesis-Conflict Debugging Team (Investigation · Low Risk)
```
Recommendation: ★★★★★ (Demonstrates the true value of Agent Teams)
Team composition:
  Lead: Hypothesis integrator (delegate mode recommended)
  Teammate1-N: Investigators for each hypothesis (3-5 members)

When to use:
  - Investigating unknown-cause bugs
  - Identifying causes of performance regressions
  - Isolating issues with multiple possible causes

Prompt example:
  "[Bug symptom] is occurring.
   Create an agent team to investigate the following hypotheses in parallel:
   1. [Hypothesis A]
   2. [Hypothesis B]
   3. [Hypothesis C]
   Team members should validate and refute each other's hypotheses,
   and identify the most likely root cause."

Notes:
  - Messaging between teammates is important (share and challenge findings)
  - Lead should focus on coordination in delegate mode
  - A single agent tends to stop at the first hypothesis → multiple agents are more accurate
```

#### Pattern 3: Multi-module Feature Development Team (Implementation · High Risk)
```
Recommendation: ★★★☆☆ (For experienced users)
Team composition:
  Lead: Architect (delegate mode required)
  Teammate1: Backend API + DB schema
  Teammate2: Frontend UI + state management
  Teammate3: Testing (E2E + integration tests)

When to use:
  - Feature additions where frontend/backend/testing responsibilities are clearly separable
  - Implementing new CRUD features
  - Adding new endpoints to a microservice

Prompt example:
  "Create an agent team to implement [feature name].
   Teammate1: responsible for src/api/ and src/db/ (backend)
   Teammate2: responsible for src/components/ and src/stores/ (frontend)
   Teammate3: responsible for tests/ (test creation)
   The lead should operate in delegate mode to coordinate,
   and ensure teammate3 writes tests after teammate1 finishes."

Notes:
  - Explicitly specify files/directories each member is responsible for
  - Limit shared files (types, config, etc.) to a single owner
  - Recommend using plan approval (lead approves before risky changes)
```

#### Pattern 4: Design Exploration Team (Planning · No Risk)
```
Recommendation: ★★★★☆
Team composition:
  Lead: Facilitator (integration & decision-making)
  Teammate1: UX/DX perspective
  Teammate2: Technical architecture perspective
  Teammate3: Devil's Advocate (critical review)

When to use:
  - Design phase for a new feature
  - Library/framework selection
  - Architectural decisions

Prompt example:
  "Create an agent team to explore [design challenge].
   Explore in parallel from UX, technical architecture, and critical review perspectives,
   and summarize a final recommended approach."

Notes:
  - Low risk because no code is written
  - Follows official documentation examples/patterns
```

#### Pattern 5: Refactoring Team (Implementation · Medium Risk)
```
Recommendation: ★★★☆☆
Team composition:
  Lead: Integration manager (delegate mode recommended)
  Teammate1: Refactor Module A
  Teammate2: Refactor Module B
  Teammate3: Detect breaking changes & update tests

When to use:
  - Large-scale refactoring
  - Reorganizing directory structure
  - API changes spanning multiple modules

Prompt example:
  "Create an agent team to refactor the authentication module.
   Enable plan approval and require the lead to approve the plan before implementation."

Notes:
  - Plan approval is particularly important
  - Clearly separate each teammate's module responsibilities
```

