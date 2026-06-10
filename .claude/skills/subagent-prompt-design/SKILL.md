---
name: subagent-prompt-design
description: >
  Design and write well-formed prompts for SubAgents launched via the Agent/Task tool.
  Use this skill whenever you are about to delegate work to a SubAgent, write prompts for
  parallel fan-out exploration or multi-perspective review, decide which model tier a
  SubAgent should use, or structure the return format so the main context stays clean.
  If you are about to fire off a Task call without reading this first, stop and read it —
  a poorly written subagent prompt is worse than doing the work inline.
---

# SubAgent Prompt Design

Companion to `iterative-retrieval`. That skill solves _what context to gather_;
this skill solves _how to commission the agent that does the work_.

---

## 1. The Core Problem: Subagents Are Born Amnesiac

A SubAgent starts with **zero conversation history**. The only thing it knows is
what you wrote in its prompt. The only thing that returns to you is its **final
message** — everything intermediate is discarded.

Consequences:

- Anything you discussed with the user but did not include in the prompt **does not
  exist** for the SubAgent.
- Any file content the SubAgent read but did not include in its return **is lost**.
- Any reasoning the SubAgent did but expressed only mid-conversation **is silent**.

Design every prompt as if you are handing a task to a contractor you have never
met, via written brief, with no follow-up possible.

---

## 2. Anatomy of a Good SubAgent Prompt

A complete prompt has five parts. Include all of them.

### 2.1 Task (one sentence)

State the single concrete outcome. Use a verb + object + constraint.

```
❌  "Look into the auth code."
✅  "Identify all call sites of `verifyToken()` in /Users/me/proj/src and return
    the file path and line number of each one."
```

The one-sentence limit forces you to split bloated tasks before you write the
prompt, not after the agent wastes cycles.

### 2.2 Context (absolute paths + decided constraints)

Include every piece of information the agent needs that is not on disk:

- **Absolute file paths** for every relevant file — never relative paths; the
  SubAgent's cwd may differ.
- Relevant constraints already decided in the main conversation (e.g., "we are
  targeting Node 20, not Node 18").
- The current state of things (e.g., "the tests in /…/auth.test.ts currently pass;
  do not break them").

```
❌  "Check the config files."
✅  "The relevant config files are:
    - /Users/me/proj/config/auth.json  (JWT settings)
    - /Users/me/proj/config/db.json    (connection pool)
    The team has decided to keep JWT expiry at 24h; do not change that."
```

### 2.3 Scope Boundaries (what NOT to touch)

Explicitly forbid out-of-scope actions. SubAgents are eager; they will "helpfully"
refactor adjacent code, fix unrelated lint errors, or update docs unless told not to.

```
✅  "Do NOT edit any files. Do NOT run tests. Do NOT install packages.
    Read-only analysis only."
```

For writer agents: name the exact files they may modify.

### 2.4 Expected Deliverable (exact return format)

Specify the **structure** of the final message. Match verbosity to what the main
context actually needs.

```
❌  "Tell me what you found."
✅  "Return exactly:
    FILE: <absolute path>
    ISSUES: <bullet list, max 5 items>
    VERDICT: PASS | FAIL"
```

See section 3 for the full return-format contract.

### 2.5 Why Each Part Matters

| Part        | Why it matters                                                      |
| ----------- | ------------------------------------------------------------------- |
| Task        | Without it the agent optimizes for the wrong goal                   |
| Context     | Agent cannot infer what you discussed before it was spawned         |
| Scope       | Eager agents cause merge conflicts and unintended side-effects      |
| Deliverable | Unstructured returns flood the main context and obscure conclusions |

---

## 3. Return-Format Contract

The return message is the **entire output** of the SubAgent call. Design it so
the main context stays clean.

### Rules

1. **Summaries and conclusions only** — never raw logs, stack traces, or full file
   dumps unless explicitly requested.
2. **Specify structure** — bullet lists, key-value pairs, or a defined schema.
3. **Bound the length** — "max 5 bullets", "3 sentences", "one line per file".
4. **Include actionable verdicts** — PASS/FAIL, NEEDS_FIX/OK, HIGH/MED/LOW.

### Examples

```
❌  "Explain your findings in detail."

✅  "Return:
    - summary: one sentence describing what changed
    - files_modified: list of absolute paths
    - risk: LOW | MEDIUM | HIGH with one-sentence rationale"
```

```
❌  "Print the full diff."

✅  "Return the diff as a unified diff block, max 50 lines. If longer,
    summarize the excess as '… N more lines in <file>'."
```

---

## 4. Parallelization Rules

Per `CLAUDE.md`: run 2–4 SubAgents concurrently for independent tasks. Before
launching in parallel, verify these gates:

### Gate 1: Independence

Two tasks are independent only if neither uses the output of the other **and**
neither modifies a resource the other reads.

```
❌  Parallel (wrong): Agent A writes auth.ts → Agent B reviews auth.ts
    (B might read the pre-write version)

✅  Sequential: A writes auth.ts, then B reviews it.

✅  Parallel (correct): Agent A reviews auth.ts / Agent B reviews cache.ts
```

### Gate 2: One Writer Per File

If two writers target the same file, the second write will silently overwrite the
first. Serialize writers; only readers can be parallelized freely.

```
❌  Agent 1: "Add rate limiting to /src/api/routes.ts"
    Agent 2: "Add auth middleware to /src/api/routes.ts"   ← same file, conflict

✅  Run Agent 1, then Agent 2 on the result.
```

### Gate 3: Writer / Reviewer Separation

Always separate the writing pass from the reviewing pass. Never combine "fix
this and review your own fix" in one SubAgent — reviewers have confirmation bias
when reviewing their own work.

```
✅  Agent A (Sonnet): implement feature in feature.ts
    Agent B (Opus): review feature.ts after A completes
```

### Concurrency guideline

Target 2–4 simultaneous SubAgents. Beyond 4, coordination overhead and context
merging costs outweigh the parallelism gains.

---

## 5. Model Tier Selection

Per `performance.md` and `CLAUDE.md`. Assign the **cheapest tier that can do
the job**, and retry one tier up on failure.

| Task type                           | Model                | Examples                                                                               |
| ----------------------------------- | -------------------- | -------------------------------------------------------------------------------------- |
| Mechanical search / extraction      | Haiku 4.5            | Glob/grep sweeps, counting occurrences, checking for console.log, extracting imports   |
| Implementation / debugging / review | Sonnet 4.6 (default) | Writing a function, reviewing a PR, fixing a failing test                              |
| Design / architecture / lead role   | Opus 4.8             | Architectural decision, complex refactor plan, multi-file analysis, team-lead SubAgent |

### Decision shortcut

Ask: "Could I write a regex or a short bash script to do this?" → Haiku.
Ask: "Does this require understanding intent, tradeoffs, or multi-file reasoning?" → Sonnet.
Ask: "Is this a design decision or coordination role?" → Opus.

### Retry protocol

If a Haiku SubAgent returns incorrect or incomplete results, re-run at Sonnet.
If a Sonnet SubAgent fails twice, escalate to Opus or consult Codex MCP per
`rules/MCP_Codex.md`.

---

## 6. Search and Exploration Agents

When the goal is "understand the codebase" rather than "change something":

### Breadth instruction

Tell the agent how deep to go:

```
❌  "Search for all usages."   ← ambiguous thoroughness

✅  "Do a medium-breadth search: cover src/ and lib/; skip node_modules,
    dist/, and *.test.ts. Stop when you have 5+ examples or have scanned
    all files — whichever comes first."
```

### Iterative refinement

For complex discovery tasks, apply the `iterative-retrieval` pattern:
do not send one giant prompt that guesses all the relevant files. Instead,
instruct the agent to start broad, score relevance (0–1), identify gaps, and
refine — capped at 3 cycles. See
`/Users/sardonyx0827/work/github/dotfiles/.claude/skills/iterative-retrieval/SKILL.md`
for the full loop specification.

---

## 7. Common Failure Modes

### 7.1 Vague Task

```
❌  "Look into the auth code and see if there's anything wrong."
✅  "Scan /Users/me/proj/src/auth/ for any call to eval() or Function(),
    and return a list of file:line occurrences. Return NONE if clean."
```

### 7.2 Missing Output Contract

Without a defined return format, the agent writes an essay. The main context
fills with prose, and you have to re-parse it to extract the conclusion.

```
❌  (no return format specified)
✅  "Return: VERDICT: SAFE|UNSAFE, REASON: <one sentence>"
```

### 7.3 Assumed Context ("fix the bug we discussed")

The SubAgent was not in that conversation. Every bug, design decision, and
constraint must be stated from scratch.

```
❌  "Fix the token expiry bug we talked about."
✅  "Bug: `verifyToken()` in /…/auth.ts does not check the `exp` claim.
    Fix: add an `exp < Date.now()/1000` check after line 42 and return
    a 401 if expired. Do not change the function signature."
```

### 7.4 Oversized Scope

One agent doing five unrelated things produces low-quality results on all five
and an unmanageable return blob.

```
❌  "Review security, check performance, update the README, fix lint errors,
    and write tests."
✅  Launch 5 separate agents in parallel, each with one task.
```

---

## 8. When NOT to Delegate

Per the Single-layer policy in `CLAUDE.md`, keep work inline when:

- The task depends on the current conversation context (earlier turns, user
  confirmations, open questions).
- Edits are sequential: step 2 depends on what step 1 produced.
- The change touches only 1–2 files.
- You want to pause for user review mid-way.

Spawning a SubAgent for a 10-line edit adds latency, coordination overhead, and
a context-handoff risk. Do it yourself.

---

## Prompt Template

Copy and fill in before every SubAgent call:

```
TASK: <one sentence — verb + object + constraint>

CONTEXT:
- File(s): <absolute paths>
- Decided constraints: <list>
- Current state: <what is true now that the agent must not break>

SCOPE:
- May read: <paths or "all files under X">
- May write: <specific files, or "none">
- Must NOT: <forbidden actions>

DELIVERABLE:
Return exactly:
  <field>: <description>
  <field>: <description>
  ...
Max length: <N lines / N bullets / "one paragraph">
```

---

## Checklist

Before firing any SubAgent call, verify:

- [ ] Task is one sentence with a concrete verb and measurable outcome
- [ ] All file references use absolute paths
- [ ] Context includes every constraint decided in the main conversation
- [ ] Scope section forbids unintended writes
- [ ] Return format is explicit: structure + length bound
- [ ] Return is a summary/verdict — not raw logs or full file dumps
- [ ] Parallelism gates passed: tasks are independent, no two writers on same file
- [ ] Model tier matches task complexity (Haiku / Sonnet / Opus)
- [ ] If exploration: breadth instruction given or iterative-retrieval pattern invoked
- [ ] Task does NOT belong in Single layer (not conversation-dependent, not 1-2 file sequential edit)
