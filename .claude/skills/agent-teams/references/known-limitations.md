## references/known-limitations.md (Known Limitations and Workarounds)

### Design Principles
- Collected from official documentation and practitioner reports
- Three-column structure: "Problem → Cause → Workaround"
- Requires periodic updates to match the latest version

### Contents

#### Session Management Limitations
| Problem | Cause | Workaround |
|------|------|--------|
| `/resume` does not restore teammates | Session resumption is not supported in in-process mode | Instruct the lead to spawn new teammates |
| tmux sessions remain | Cleanup at team shutdown is incomplete | Manually remove with `tmux ls` → `tmux kill-session -t [name]` |
| Cannot create nested teams | Not supported at this time | Use a flat team structure as an alternative |

#### Task Management Limitations
| Problem | Cause | Workaround |
|------|------|--------|
| Tasks get stuck in "in progress" | Teammates forget to mark tasks complete | Instruct the lead to "check the status of task N", or update the status manually |
| Dependent tasks remain blocked | Completion notifications for prerequisite tasks are missed | Instruct the lead to nudge (remind) the responsible party |
| Lead declares completion prematurely | Lead determines completion before all tasks are done | Explicitly instruct: "Wait for all teammates to finish their work before ending" |

#### Implementation Limitations
| Problem | Cause | Workaround |
|------|------|--------|
| Overwriting the same file | Multiple teammates edit the same file | Rigorously separate ownership of files/directories |
| Lead starts writing code themselves | Delegate mode is not enabled | Switch to delegate mode with `Shift+Tab` |
| Context bloat | Long periods of unattended operation | Perform regular check-ins and shut down unnecessary teammates early |

#### Token Cost Considerations
- Agent Teams consume 3–5x the tokens of a single session
- Because each teammate has an independent context window
- Be mindful of cost-effectiveness and use only for tasks that truly require parallelization

