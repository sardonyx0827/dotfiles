---
description: Design system architecture and make technical decisions for new features or large refactors. Invokes the architect agent.
---

# Architecture Design

This command invokes the **architect** agent to design system architecture, weigh trade-offs, and make technical decisions before implementation begins.

## What This Command Does

1. **Clarify Requirements**: Restate the problem, constraints, scale targets, and non-functional requirements
2. **Map the Current System**: Read the relevant modules and boundaries (read-only — no code changes)
3. **Propose Options**: Present 2–3 viable approaches with explicit trade-offs (complexity, scalability, cost, risk)
4. **Recommend**: Give a single recommendation with rationale and the decisive constraints
5. **Sketch the Design**: Component boundaries, data flow, interfaces, and failure modes
6. **Call Out Risks**: Migration/rollout concerns, and what to validate before building

## When to Use

Use `/architect` when:

- Planning a new feature that spans multiple modules or services
- Refactoring or re-architecting a large system
- Choosing between competing designs, libraries, or data models
- Evaluating scalability, consistency, or reliability trade-offs
- You need a design agreed BEFORE writing implementation code

## Output

A concise design document: problem statement, options table, recommendation, component/data-flow sketch, and open risks to validate. No code is written by this command — hand the design to implementation afterward.

## Related

- Agent: `agents/architect.md`
- Related commands: `/plan`, `/orchestrate`
- Skills: `skills/backend-patterns/`, `skills/frontend-patterns/`, `skills/migration-playbook/`
