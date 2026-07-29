# CLAUDE.md

## Language Policy

- All interactions / outputs must be in **Japanese**
- **Git commit messages must be written in English** (follow the conventions in `@~/.claude/rules/git-workflow.md`)

## Browser Operations

- **claude-in-chrome**: pages that need interaction, authentication, or JS rendering. It drives a logged-in browser session, so treat it as the heavier path, not the default one
- **`WebFetch` / `WebSearch`**: plain read-only fetches and searches. Both are allow-listed, so no justification is needed
- `curl` / `wget` / `nc` / `ssh` are hard-denied by `permissions.deny` and by the bash-review hook, so they are not an available fetch path — do not plan a step around them

## Git Operations

When instructed to push / commit / create a PR, follow the Command Triggers in `@~/.claude/rules/git-workflow.md`

## Execution Layer Selection (SubAgents / Single / AgentTeams)

**This section is the single source of truth for delegation.** The `description` fields in
`.claude/agents/*.md` ("Use PROACTIVELY", "MUST BE USED for all code changes", "Automatically
activated") exist so the _right_ agent is picked once delegation is already warranted — they are
capability advertisements, **not invocation mandates**, and they never override this section.
Delegation triggers live here and nowhere else.

**These triggers are a request, not just a policy.** I wrote them down in advance so that I would not
have to ask for the same delegation again in every session. When one of them matches, I am asking for
that SubAgent.

**Default posture: delegate.** A SubAgent costs tokens and a round trip. Not delegating costs a
polluted main context, serialized work, and a review that never happens — that second cost used to
go unpriced here. Check the triggers below first; fall through to Single only when none match.

### SubAgents (the Agent tool) — the default whenever a trigger matches

These fire on the situation, and not on a fresh cost/benefit judgment made in the moment.
"I could probably do this inline" is not a reason to skip one.

| Situation                                                                              | Agent                                                                      |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| A feature, fix, or refactor is complete, or a commit is about to be made               | `code-reviewer` (Go: `go-reviewer`; SQL / migrations: `database-reviewer`) |
| A question that needs sweeping many files, directories, or naming conventions          | `Explore` (read-only fan-out; returns the conclusion, not the file dumps)  |
| A new feature, or a refactor spanning more than a couple of files, before writing code | `planner` (system-level: `architect`)                                      |
| Build, type, or vet errors that are mechanical to clear                                | `build-error-resolver` (Go: `go-build-resolver`)                           |
| Independent tickets, competing proposals, or distinct review angles                    | one SubAgent per track                                                     |

Security review keeps its own trigger table in `@~/.claude/rules/security.md` — follow it there, and
treat the agent it names as a trigger of the same standing as the rows above.

**How many.** Match the count to the number of genuinely independent tracks: one track, one agent;
five independent tickets, five agents. Past ~6 concurrent, coordination and report-reading outweigh
the parallelism — run the rest as a second wave. Do not slice one modest job across several agents.

Conventions when calling — this is what keeps delegation cheap enough to be the default:

- Specify the "file path" and "format of the artifact to return" for each SubAgent
- Return only a summary (diff / conclusion). Do not return raw logs to the main agent
- Do not launch SubAgents that write to the same file simultaneously (to avoid conflicting overwrites)
- Launch independent SubAgents in a single message so they run concurrently
- Follow the **subagent-prompt-design** skill when writing the prompt itself

### Single (executed by the main agent itself) — when no trigger matches

- A single-line or mechanical edit whose location is already known, or a follow-up question about
  work already in context
- Interactive debugging where each step depends on the previous result and on user judgment
- State transitions that are sequential and need user review between steps
- Anything the user asked the main agent to do itself

Once you delegate, commit to it: do not redo a SubAgent's work or re-derive its findings after it reports back.

### AgentTeams (tmux)

Use only for intermediate cases between Single and SubAgents where parallel processing is possible but context maintenance is difficult with SubAgents alone.
Launch conditions (when any of the following are met):

- The user has explicitly instructed AgentTeam / team / tmux launch
- Changes span multiple layers such as FE / BE / tests, and teammates need to consult with each other
- Debugging competing hypotheses, where independent teammates refute each other's hypotheses
- Large-scale refactoring or cross-cutting analysis that takes more than 10 minutes

## Model Selection Guidelines (Common to Single / SubAgent / AgentTeam)

Two independent axes: **model tier** (capability ceiling) and **effort** (how much thinking and tool work is spent reaching it). Tier alone is a pre-effort mental model — pick both.

### Model tier

- Main session: Opus (CLI default; not pinned via `model` in `settings.json`)
- Haiku: Tasks requiring no reasoning such as Glob/Grep, template extraction, document consistency checks
- Sonnet: Implementation / debugging / review (default for SubAgents)
- Opus: Design / large-scale refactoring / overall analysis / team lead
- Fable: reserve for the hardest long-horizon work, and only when explicitly chosen — it is priced above the Opus tier and requires 30-day data retention (unavailable under ZDR)

### Effort

Set via `effortLevel` in `settings.json` (session-wide, currently `xhigh`), `/effort` for one session, or an `effort:` key in `.claude/agents/*.md` frontmatter (`low` | `medium` | `high` | `xhigh` | `max`, or an integer).

- On the current Opus, `low` and `medium` are far stronger than their names suggest — they often beat a previous generation's `high` at a fraction of the tokens and latency
- `xhigh` is the right default for coding and agentic work (and the Claude Code default); `high` for other intelligence-sensitive work
- Effort levels carried over from an older model are usually the wrong setting — sweep before trusting them
- Lower effort for mechanical SubAgents rather than dropping a tier: the capability ceiling stays available if the task turns out to need it

### On failure

Raise **effort first, model tier second.** Escalating the tier when an effort bump would have done wastes budget and latency. Only after effort is exhausted at the current tier should you move up a tier, or consult Codex MCP per the `codex-consultation` skill.

## Development Workflow

- For new features, bug fixes, and refactoring, follow the **tdd-workflow** skill (test-first; it defines the coverage policy)
- Review timing for code changes is a delegation trigger — see § Execution Layer Selection
- Do not always load code pattern/style details; instead follow the relevant skill (backend-patterns / frontend-patterns / golang-patterns, etc.)

## Safety Guards

- Always confirm before executing destructive operations (rm -rf / force push / production DB operations, etc.)
- Personal information and secrets are excluded from browser automation

## External Agent Integration

When working on specification review, design, bug fixes, or test code creation, follow the **codex-consultation** skill.

Three second-opinion channels coexist — treat them as tiers along two axes (vendor and weight), not interchangeable:

- **advisor (Opus — same-vendor, routine)** — fast primary self-check over the whole trajectory. Sees the full transcript automatically, zero friction (no prompt to author). Call before substantive work, before declaring done, and when first stuck — the routine checkpoint.
  - **Fallback chain when advisor is unavailable.** Trigger the fallback whenever the advisor checkpoint can't run as intended — whether because the advisor tool is **absent from the session entirely** (not in the available tools, so there is no call to make) or because an advisor call **itself errors** (tool failure — not merely advice you disagree with). In either case, don't skip the checkpoint: treat **Codex** (`mcp__codex__codex`, per the **codex-consultation** skill) as the primary channel, and if that call _also_ errors, fall back to **gemini-consultant** (`consult_gemini`, Pro). Neither Codex nor Gemini sees the transcript the way advisor does, so summarize the current situation and the question you would have put to advisor into the prompt — this is a re-consultation with context rebuilt, not a mechanical swap of the call.
- **gemini-consultant (Google — cross-vendor, lightweight)** — a cheap, fast cross-vendor gut-check via the MCP server. Use `review_gemini` (Flash) for high-frequency, local "did I miss anything?" checks, and `consult_gemini` (Pro) for a quick cross-vendor design gut-check _before_ an idea is heavy enough to warrant Codex. Reach for it when you want another vendor's eyes but the case is lighter than a Codex escalation.
- **Codex (OpenAI — cross-vendor, heavyweight)** — selective escalation for an independent opinion on the heavier cases the **codex-consultation** / **debugging-protocol** skills define (spec/design proposals, large-scale changes, test strategy, 2+ consecutive failed fixes → root-cause). Unlike the other two it can also act (implement / rescue) when explicitly instructed.
- **On conflicting advice** — do not silently pick a side; surface the opinions to the user.

## Visual Asset Generation

When a task needs a generated bitmap image written to disk (site `public/` assets, hero/OG images, illustrations, mockups), delegate to OpenAI Codex's built-in `image_gen` via the `mcp__codex__codex` tool. Follow the Triggers in `@~/.claude/rules/image-generation.md` (full workflow in the **codex-image-gen** skill). The built-in path needs no `OPENAI_API_KEY`; only true native transparency does — confirm with the user first.
