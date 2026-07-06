---
description: One unattended pass over docs/requests/ - quiet when idle, auto-executes safe requests only. Designed to be driven by /loop (e.g. /loop 30m /requests-watch).
---

# Requests Watch Command

Run **one unattended pass** over the `docs/requests/` drop zone. Follow the **request-harness** skill — its "Watch Mode" section is authoritative and overrides the normal workflow rules.

## Usage

- `/loop 30m /requests-watch` — recommended: check every 30 minutes (any interval works). Stop by interrupting or telling Claude to stop the loop.
- `/requests-watch` — a single manual pass.

## Behavior (summary — see the skill for the full rules)

1. Scaffold `docs/requests/` silently if it does not exist.
2. Nothing to do → exactly one summary line, end the turn.
3. Intake all new drops. Guards: ignore hidden/temp files and symlinks; leave entries modified within the last 2 minutes for the next pass.
4. Auto-execute `research` / `writing` / `visual` tickets only, max 3 per pass. `code` / `ops` / `mixed` are ticketed (interpretation + plan) then set to `needs-input` for explicit `/requests <ticket-id>` execution — no unattended repo mutation.
5. Never ask the user questions; never auto-retry `needs-input` / `blocked` tickets; back off to `blocked` on repeated failure.
6. Finish with a short Japanese board covering only the tickets touched this pass.
