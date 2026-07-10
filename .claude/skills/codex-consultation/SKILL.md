---
name: codex-consultation
description: Consult OpenAI Codex MCP for second opinions on specs, architecture, design decisions, and bug-fix strategy. Use when proposing a specification or design, planning a large-scale modification, writing test strategy, or after 2 consecutive failed fix attempts on the same issue (root-cause analysis). Codex is for discussion only — never let it implement code unless explicitly instructed.
---

# Codex MCP Server

**Purpose**: Use OpenAI Codex's advanced problem-solving and code-generation engine
to gather opinions on architecture design, strategic analysis, bug fixes, and similar topics.
Although it can generate code, **never let it implement anything unless explicitly instructed** —
use it solely for discussions about design, strategy, and the like.
After receiving an answer from Codex MCP, validate its soundness with Claude.

## Tools

| Tool                      | Purpose                                                       |
| ------------------------- | ------------------------------------------------------------- |
| `mcp__codex__codex`       | Start a new session (`prompt` required)                       |
| `mcp__codex__codex-reply` | Continue an existing session (`threadId` + `prompt` required) |

### `mcp__codex__codex` parameters

| Parameter         | Description               | Example values                                                            |
| ----------------- | ------------------------- | ------------------------------------------------------------------------- |
| `prompt`          | Initial prompt (required) | Detailed description of the problem                                       |
| `sandbox`         | Sandbox mode              | `read-only`                                                               |
| `approval-policy` | Shell command approval    | `untrusted`, `on-failure`, `on-request`, `never`                          |
| `model`           | Model selection           | `gpt-5.6-terra`                                                           |
| `cwd`             | Working directory         | Project root path                                                         |

### `mcp__codex__codex-reply` parameters

| Parameter  | Description                                                        |
| ---------- | ------------------------------------------------------------------ |
| `threadId` | `structuredContent.threadId` from the previous response (required) |
| `prompt`   | Follow-up instructions / questions (required)                      |

## When to Use

- Double-checking and gathering opinions when proposing a specification, designing, or making large-scale modifications
- Gathering opinions when two consecutive attempts to fix an error on the same topic have failed and root-cause analysis or a fix strategy is needed

## Escalation Flow

```
Implement with Claude → tests/verification fail
  → Summarize failure details + attempts so far into a prompt
  → Delegate to Codex (root-cause analysis + fix strategy)
  → Implement and verify Codex's answer with Claude
```

## Conversation Continuation Pattern

1. Start the initial session with `mcp__codex__codex`
2. Capture `structuredContent.threadId` from the response
3. Continue by passing `threadId` and `prompt` to `mcp__codex__codex-reply`
4. Repeat step 3 as needed

**Note**: threadIds become invalid after the MCP server restarts, so continuation across sessions is not possible.

## What to Include in the Prompt Passed to Codex MCP

- **Goal**: What do you want to change or build?
- **Context**: Which files, folders, documents, examples, or errors are relevant to this task?
- **Constraints**: What standards, architecture, safety requirements, or conventions must Codex follow?
- **Done when**: Conditions that must be met before the task is complete
- **Attempts so far**: What has been tried so far and the results
- **Important**: Explicitly state **"No code generation is needed — only design and strategy proposals."**

## Choosing Between This and the codex-delegator Agent

- **`codex-delegator` agent (via the Task tool)**: Delegate spec discussions, bug-fix strategy consultations, and complex technical decisions. Automatic escalation when Claude has failed multiple times
- **Direct MCP tool calls**: When you need fine-grained control over `sandbox` or `model`, or when continuing an existing conversation with `codex-reply`
