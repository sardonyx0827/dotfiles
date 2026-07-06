---
description: Process requests dropped in docs/requests/ (intake -> plan -> execute -> deliver). Usage: /requests [status|init|<ticket-id>]
---

# Requests Command

Process the `docs/requests/` drop zone. Follow the **request-harness** skill for every rule below (directory contract, ticket format, routing, delivery).

## Usage

- `/requests` — intake new drops, resume every unfinished ticket, deliver
- `/requests status` — show the board only (new / in-progress / needs-input / recently done); no execution
- `/requests <ticket-id>` — process only the given ticket
- `/requests init` — scaffold the docs/requests/ structure only
- Periodic monitoring: `/loop 30m /requests-watch` (see the skill's Watch Mode section)

## Behavior

1. If `docs/requests/` does not exist, scaffold it per the skill. If the argument was `init`, stop here.
2. Scan and show the board (Japanese) before executing.
3. Process tickets per the skill workflow: intake -> clarify -> execute -> deliver.
   - Independent tickets may run in parallel via **request-worker** subagents (2–4), per the skill's parallelism rules.
   - Tickets that require user answers are set to `needs-input` and skipped, not stalled on.
4. Finish with a Japanese summary: completed tickets with deliverable paths, and tickets awaiting user input with their questions.

## Arguments

$ARGUMENTS
