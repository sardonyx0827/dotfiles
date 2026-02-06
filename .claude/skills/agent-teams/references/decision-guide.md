## references/decision-guide.md (Decision Guide)

### Design Principles
- Help quickly decide whether to use Agent Teams
- Provide a flowchart format + concrete examples

### Contents

#### Decision Flowchart (text format)

```
Assess the nature of the task:
│
├─ Do you need teammates to share findings and rebut each other?
│   ├─ YES → Agent Teams
│   └─ NO ↓
│
├─ Can the task be split into three or more independent subtasks?
│   ├─ YES → Do they edit the same file?
│   │   ├─ YES → Single session (sequential execution)
│   │   └─ NO → Agent Teams or Git worktrees
│   └─ NO ↓
│
├─ Is it sufficient for subtasks to only report their results?
│   ├─ YES → Subagent (Task tool)
│   └─ NO → Agent Teams
│
└─ If none of the above apply → Single session
```

#### Comparison Table

| Characteristic | Single Session | Subagent (Task) | Agent Teams |
|----------------|----------------|-----------------|-------------|
| Context | Shared (single) | Reports only to parent | Independent + mutual communication |
| Parallel execution | Not possible | Possible (up to 7) | Possible (no limit) |
| Inter-team communication | N/A | Not possible | Possible |
| File edit conflicts | None | None (usually read-only) | Requires caution |
| Token cost | Low | Medium | High (3–5x) |
| Setup | Not required | Not required | Environment variables required |
| Suitable tasks | Sequential work | Research / read-only tasks | Parallel implementation / conflict verification |

#### Specific Decision Examples

**Use Agent Teams:**
- Parallel review of a PR from three different perspectives
- Investigate an unknown bug in parallel using multiple hypotheses
- Develop front-end / back-end / tests simultaneously
- Explore new feature designs from multiple viewpoints

**Use Subagent (Task):**
- Reading/searching file contents
- Fetching external documents
- One-off investigative tasks (only report results)

**Use Single Session:**
- Editing a single file
- Work with high order-dependency
- Small bug fixes
- Code changes under 20 lines

