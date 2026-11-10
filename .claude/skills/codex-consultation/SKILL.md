---
name: codex-consultation
description: Consult OpenAI Codex (via the `codex exec` CLI) for second opinions on specs, architecture, design decisions, and bug-fix strategy. Use when proposing a specification or design, planning a large-scale modification, writing test strategy, or after 2 consecutive failed fix attempts on the same issue (root-cause analysis). Codex is for discussion only — never let it implement code unless explicitly instructed.
---

# Codex consultation (`codex exec`)

**Purpose**: Use OpenAI Codex's problem-solving engine to gather opinions on architecture design,
strategic analysis, bug fixes, and similar topics. Although it can generate code, **never let it
implement anything unless explicitly instructed** — use it solely for discussions about design,
strategy, and the like. After receiving an answer from Codex, validate its soundness with Claude.

Codex is reached through the **`codex exec` CLI on the Bash tool**. Codex CLI 0.154 removed its
MCP-server mode, so there are no Codex MCP tools to call. This skill is the single source of truth
for the command shape; other skills, rules and agents point here.

## When to Use

- Double-checking and gathering opinions when proposing a specification, designing, or making large-scale modifications
- Gathering opinions when two consecutive attempts to fix an error on the same topic have failed and root-cause analysis or a fix strategy is needed
- The advisor fallback chain in CLAUDE.md, when the advisor tool is absent or errors

## Escalation Flow

```
Implement with Claude → tests/verification fail
  → Summarize failure details + attempts so far into a prompt
  → Delegate to Codex (root-cause analysis + fix strategy)
  → Implement and verify Codex's answer with Claude
```

## Invocation contract

Shell variables do not survive between Bash calls, and the Write tool expands neither `$` nor `~`
in its path. Use **absolute paths, written out literally**, for every `<placeholder>` below.

1. **Write the prompt to a file** with the Write tool: `<scratchpad>/codex-<slug>-<n>.prompt.txt`.
   Pick a name not used before in this session, so a failed run can never leave you reading an
   earlier run's answer.
2. **Run Codex** with the Bash tool, `run_in_background: true` (Codex runs at `xhigh` reasoning by
   default and can outlast the Bash tool's 10-minute foreground limit), as one command:

   ```bash
   python3 ~/.claude/hooks/../../scripts/secret_scan.py < <prompt-file> && EDITOR_AI_ONESHOT=1 codex exec -s read-only -C <project-root> --skip-git-repo-check --json -o <answer-file> - < <prompt-file> > <events-file> 2> <log-file>
   ```

3. **When the task notification arrives**, check the exit status. On success, Read `<answer-file>`:
   it holds Codex's final message only. On failure, read the tail of `<log-file>`, not the events.

What each part is for:

| Part                                | Why                                                                                                                                                                                                       |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `secret_scan.py < <prompt-file> &&` | A prompt written with the Write tool never passes through the bash-review hook, so this is the only secret scan it gets. Exit 1 (a credential) and exit 2 (the scanner could not run) both stop the send. |
| `EDITOR_AI_ONESHOT=1`               | The sandbox does not bind Codex's own Stop hooks. Without it, `.codex/hooks/auto-format.sh` reformats every uncommitted file in your tree at the end of a run that was only meant to answer.              |
| `-s read-only`                      | A consultation reads; it never writes. Do not widen it.                                                                                                                                                   |
| `-C <project-root>`                 | The Bash tool's cwd is not guaranteed; Codex reads the project from here.                                                                                                                                 |
| `--skip-git-repo-check`             | Works outside a git repository too (same as the bash-review hook's own `codex exec`).                                                                                                                     |
| `--json … > <events-file>`          | The events carry the thread id needed for a follow-up. Do not print them into the conversation; they are long.                                                                                            |
| `-o <answer-file>`                  | The final answer, without the event noise.                                                                                                                                                                |
| `- < <prompt-file>`                 | The prompt arrives on stdin: no `ps` exposure, no argv length limit, and no heredoc for bash-review to misread.                                                                                           |

Omit `-m`: the Codex default model tracks the CLI. If no follow-up is planned, `--ephemeral` keeps
the session out of `~/.codex/sessions`; a follow-up needs the session, so do not combine the two.

**If the command fails**, tell the causes apart before retrying — never drop the gate to get a
prompt through:

- The scanner printed a category label (`AWS access key`, `GitHub token`, …): the prompt contains a
  credential. Remove it and run again.
- `secret-scan unavailable: …` on stderr: `python3` could not run the scanner. Fix that.
- `No such file or directory` from the shell: the `<prompt-file>` path is wrong (the redirect
  failed before the scanner ran).
- None of the above, and `<log-file>` exists: Codex itself failed — read its tail.

### Follow-up turns

Take the thread id from the first run's events, then resume that thread:

```bash
jq -r 'select(.type=="thread.started").thread_id' <events-file> | head -n1
```

(`head -n1`: with Codex's `multi_agent` feature on, sub-agents can start threads of their own; the
first `thread.started` is the session you ran.)

```bash
python3 ~/.claude/hooks/../../scripts/secret_scan.py < <prompt-file-2> && EDITOR_AI_ONESHOT=1 codex exec resume <thread-id> -c sandbox_mode=read-only --skip-git-repo-check --json -o <answer-file-2> - < <prompt-file-2> > <events-file-2> 2> <log-file-2>
```

`codex exec resume` takes neither `-s` nor `-C`, so the sandbox is pinned with
`-c sandbox_mode=read-only` (that resume continues the thread was verified with Codex CLI 0.167;
that it honours this pin was not). Always resume by id: **never `--last`**, because the most recent Codex
session is usually one of the bash-review hook's own reviews.

### What the bash-review hook lets through

The hook judges every `codex` invocation against an **allowlist** of exactly the shapes above
(`codex_flag_ask_reason` in `.claude/hooks/_bash_review_common.py`); anything else goes to the user
for confirmation, whatever a reviewing model thinks:

- `-s read-only`; `-s workspace-write` only with a `-C` inside the session scratchpad
  (codex-image-gen skill)
- `-c sandbox_mode=read-only` and `-c model_reasoning_effort=…` — no other config key
- `-C`, `-o`, `-m`, `--json`, `--ephemeral`, `--skip-git-repo-check`; subcommand `exec` /
  `exec resume`
- a prompt file on stdin only if the same command secret-scans that same file first
- `EDITOR_AI_ONESHOT=1` as the only environment assignment in front of `codex`

So never add `danger-full-access`, `--dangerously-*`, `--add-dir`, `--profile`, `--enable` /
`--disable`, `CODEX_HOME=` or other config keys to get a run through.

## What to Include in the Prompt

- **Goal**: What do you want to change or build?
- **Context**: Which files, folders, documents, examples, or errors are relevant to this task?
- **Constraints**: What standards, architecture, safety requirements, or conventions must Codex follow?
- **Done when**: Conditions that must be met before the task is complete
- **Attempts so far**: What has been tried so far and the results
- **Important**: Explicitly state **"No code generation is needed — only design and strategy proposals."**

## Implementation, when explicitly instructed

Do not hand Codex write access from this skill. When the user explicitly asks for Codex to implement
or rescue something, use the Codex plugin for Claude Code instead: the `codex:codex-rescue` agent or
the `/codex:rescue` command. Image generation is the one write path this repo drives through
`codex exec` itself (see the codex-image-gen skill).

## Choosing Between This and the codex-delegator Agent

- **`codex-delegator` agent (via the Agent tool)**: Delegate spec discussions, bug-fix strategy consultations, and complex technical decisions so the Codex exchange stays out of your context. Escalate here after 2 consecutive failed fix attempts on the same issue (see When to Use)
- **Running the contract yourself**: When you need the answer in your own context, or are continuing a thread you started
