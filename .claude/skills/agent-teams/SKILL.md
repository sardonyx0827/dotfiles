---
name: agent-teams
description:
  Supports multi-agent collaborative workflows for Claude Code Agent Teams.
  Provides orchestration for parallel work including team composition design, task decomposition, delegate mode operation,
  and file-conflict avoidance.
  Use in the following cases ->
  (1) When parallel work across multiple Claude instances is required,
  (2) When forming a team for code review, debugging, or multi-module development,
  (3) When given instructions such as "agent team", "create a team", "in parallel", or "swarm",
  (4) When troubleshooting an existing Agent Teams session.
  For simple subtask delegation, prefer subagents (the Task tool) rather than Agent Teams.
---

## Core Workflow for Creating a Team (5 Steps)

1. **Task analysis** — Evaluate suitability for parallelization
   - Will file conflicts occur? (NG pattern: multiple teammates editing the same file)
   - Can each teammate's scope be clearly separated?
   - If unsure, refer to `references/decision-guide.md`

2. **Decide team composition** — Choose or design from patterns
   - Provide a brief overview of representative patterns (1–2 lines each)
   - For details, instruct to consult `references/team-patterns.md`

3. **Instruct the team in natural language** — Provide 2–3 prompt examples
   - Characteristics of good instructions: each teammate's role, assigned files/directories, and deliverables are clear
   - Characteristics of bad instructions: ambiguous role assignments, overlapping responsibilities

4. **Determine whether to apply delegate mode**
   - If the lead starts writing code themselves, switch to delegate mode with `Shift+Tab`
   - Cases where delegate mode is useful: teams of 3 or more, complex orchestration
   - Unnecessary cases: small teams of 2, or when the lead also wants to implement

5. **Monitoring and intervention**
   - Use `Shift+Up/Down` to select teammates and check progress
   - How to handle tasks that become "stuck"
   - Handling issues where the lead declares completion before the team is finished

#### C. File Conflict Avoidance Rules (IMPORTANT)
- Explicitly assign directories/files to each teammate
- Limit edits to shared files (e.g. package.json) to a single teammate
- Incorporate an integration phase into the plan

#### D. Troubleshooting (Brief)
- 3–5 common problems and immediate remedies
- For details, refer to `references/known-limitations.md`
