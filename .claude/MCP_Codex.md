# Codex MCP Server

**Purpose**: Advanced AI collaboration engine for complex code generation, architectural decisions, and strategic analysis

## Triggers

- Escalation when bug fixes fail one or more times
- Architecture design or specification development requirements
- Review or improvement proposals for existing code
- Tasks requiring specialized agent consideration
- Feature addition/editing requiring multi-angle consideration

## Choose When

- **Over native Claude**: After fix failures, complex design decisions, specialized review requests
- **For strategic decisions**: Architecture design, specification development, technology selection
- **For quality assurance**: Code review, potential bug identification, improvement proposals
- **For delegation**: Task decomposition and specialized agent assignment
- **Not for simple tasks**: Basic code fixes, simple implementation, routine development work

## Works Best With

- **Sequential**: Sequential structures problems → Codex develops solution strategies
- **Serena**: Serena provides project context → Codex performs architectural analysis
- **Context7**: Context7 provides framework patterns → Codex develops implementation strategy
- **Business Panel**: Business requirements → Codex creates technical implementation roadmap

## Delegation Patterns

### Bug Fix Escalation

```yaml
trigger: "Bug fix failure"
claude_role: "Organize failure history and problem details"
codex_role: "Advanced debugging and root cause analysis"
handoff: "Failure details + previous attempt content → Codex"
```

### Architecture Design

```yaml
trigger: "System design request"
claude_role: "Requirements organization and task breakdown"
codex_role: "Architecture design and technology selection"
handoff: "Requirements + constraints → Codex"
```

### Code Review

```yaml
trigger: "Quality assurance for critical features"
claude_role: "Initial implementation and review request"
codex_role: "Detailed review and improvement proposals"
handoff: "Implementation code + review perspective → Codex"
```

### Task Decomposition

```yaml
trigger: "Complex task specialization and division"
claude_role: "Overall planning and task integration"
codex_role: "Implementation as specialized agent"
handoff: "Specific implementation instructions → Codex"
```

## Integration with SuperClaude Framework

### Workflow Patterns

```yaml
escalation_workflow:
  phase_1: "Initial implementation attempt by Claude"
  phase_2: "Processing failure"
  phase_3: "Automatic Codex MCP escalation"
  phase_4: "Advanced problem solving and coding by Codex"
  phase_5: "Result integration and validation by Claude"

escalation_workflow:
  phase_1: "Initial fix attempt by Claude"
  phase_2: "Error occurrence during post-fix verification"
  phase_3: "Automatic Codex MCP escalation"
  phase_4: "Advanced problem solving and coding by Codex"
  phase_5: "Result integration and validation by Claude"

design_workflow:
  phase_1: "Requirements analysis by Claude"
  phase_2: "Architecture design by Codex"
  phase_3: "Implementation by Claude"
  phase_4: "Design review by Codex"
  phase_5: "Iterative improvement cycle"
```

## Quality Standards

### Handoff Requirements

- **Context Completeness**: Provide complete failure history, attempt contents, and constraints
- **Clear Scope**: Clearly define the scope of delegation to Codex
- **Success Criteria**: Specify success judgment criteria
- **Integration Plan**: Plan how to integrate Codex deliverables

### Collaboration Principles

- **Complementary Roles**: Design Claude and Codex roles complementarily
- **Evidence-Based Handoff**: Escalate based on concrete evidence
- **Validation Required**: Always verify and test Codex output
- **Learning Integration**: Apply learnings from Codex to Claude's future work

## Examples

### Bug Fix Escalation

```
Claude implementation → Testing
Test results NG → Codex
Codex response → Root cause identification + fix strategy
Claude → Implementation + verification of Codex strategy
```

### Architecture Review

```
Codex response → Architecture analysis + improvement proposals
Claude → Proposal evaluation + implementation as needed
```

### Strategic Delegation

```
Codex response → Subtask decomposition + specialized agent assignment
Claude → Coordination of each subtask + integration
```
