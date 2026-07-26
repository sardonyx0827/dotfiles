# CLAUDE.md

## Language Policy

- All interactions / outputs must be in **Japanese**
- **Git commit messages must be written in English** (follow the conventions in `@~/.claude/rules/git-workflow.md`)

## Browser Operations

- Use claude-in-chrome for fetching/operating web content
- If fetch / curl is needed, explain the reason before executing

## Git Operations

When instructed to push / commit / create a PR, follow the Command Triggers in `@~/.claude/rules/git-workflow.md`

## Execution Layer Selection (Single / SubAgents / AgentTeams)

When receiving a task, evaluate in the following order and execute at the first matching layer.

### 1. Single (executed by the main agent itself) - Default

If any of the following apply, execute sequentially without delegating to SubAgent / AgentTeam:

- Work that strongly depends on the immediately preceding conversation context or unconfirmed premises
- Continuously editing the same file, or where edit locations depend on the result of the previous step
- State transitions are sequential and intermediate results need review / user confirmation
- Small-scale changes of 1–2 files, interactive debugging, minor fixes

### 2. SubAgents (launched in parallel with the Agent tool)

Delegating is not free: each SubAgent re-establishes context, re-explores, and reports back, and the main agent then re-reads that report. Delegate when the payoff clearly exceeds that overhead — if any of the following apply (guideline: 2–4 simultaneously, never more than 4 unless the user asks):

- Large-scale exploration where you don't want to pollute the context (Glob/Grep, log scanning, understanding the entire codebase)
- Parallel tasks that can run independently of each other (generating multiple proposals, multi-perspective reviews, test generation)
- Work where quality improves through role separation, such as Writer / Reviewer

Do NOT delegate when:

- You could finish the work yourself in a handful of tool calls (a few reads, a simple search, a couple of edits)
- One modest job would be split across several SubAgents. Parallel fan-out is for genuinely independent tracks, not for slicing one small task
- One SubAgent would do. Prefer one over several; keep spawn counts low

Once you delegate, commit to it: do not redo a SubAgent's work or re-derive its findings after it reports back.

Conventions when calling:

- Specify the "file path" and "format of the artifact to return" for each SubAgent
- Return only a summary (diff / conclusion). Do not return raw logs to the main agent
- Do not launch SubAgents that write to the same file simultaneously (to avoid conflicting overwrites)

### 3. AgentTeams (tmux)

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
- After writing or modifying code, review with the **code-reviewer** agent (for Go, use **go-reviewer**)
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
