---
name: codex
description: "codex MCP integration for advanced problem-solving and strategic analysis"
category: utility
complexity: basic
mcp-servers: [codex]
personas: []
---
# /codex:codex - Codex MCP Integration Command

Advanced problem-solving through Codex MCP delegation with intelligent escalation and strategic coordination.

## Syntax

```bash
/codex:codex <prompt>                    # Basic Codex delegation
/codex:codex @file.py --strategy         # Strategic code analysis
/codex:codex --spec "requirements"       # Specification discussion
/codex:codex --debug --attempts 3        # Post-failure escalation
/codex:codex --review @code.js           # Code review request
/codex:codex --architecture "system"     # Architecture design
```

## Use Cases

### 🔴 Critical Escalation (Auto-triggered)

- Bug fixes failed 3+ times → Automatic escalation
- Complex system failures requiring advanced analysis
- Performance issues beyond standard optimization

### 🟡 Strategic Analysis (Manual)

- Specification discussions for complex features
- Architecture decisions impacting multiple systems
- Complex refactoring spanning multiple modules

### 🟢 Code Quality (Optional)

- Advanced code review and optimization suggestions
- Performance optimization requiring advanced algorithms
- Multi-domain expertise requirements

## Parameters

**Analysis Types**:

- `--strategy`: Strategic planning and approach
- `--spec`: Specification and requirements analysis
- `--debug`: Problem diagnosis and resolution
- `--review`: Code quality and improvement analysis
- `--architecture`: System design and structure

**Context Control**:

- `--attempts <n>`: Number of previous failure attempts
- `--context <scope>`: Analysis scope (file|module|system)
- `--priority <level>`: Urgency level (low|medium|high|critical)

**Output Format**:

- `--structured`: Formatted analysis with clear sections
- `--actionable`: Focus on implementable recommendations
- `--brief`: Condensed analysis for quick decisions

## Integration Patterns

### With Task Agents

```bash
/codex:codex --review @auth.js --agent python-expert
# Codex provides strategic review, python-expert handles implementation
```

### With Analysis Tools

```bash
/codex:analyze @codebase --depth system --codex
# Analysis followed by Codex strategic assessment
```

### With Documentation

```bash
/codex:codex --spec "authentication system" --document
# Specification discussion with automatic documentation
```

## Escalation Rules

### Automatic Triggers

- **3+ Failed Attempts**: Auto-escalate to Codex MCP
- **System-Wide Impact**: Architecture decisions affecting multiple components
- **Performance Critical**: Optimization requiring advanced algorithms
- **Multi-Domain**: Problems spanning multiple technical domains

### Manual Triggers

- **Strategic Planning**: Complex feature specifications
- **Code Review**: Quality assessment for critical code
- **Architecture Design**: System design and structure decisions
- **Problem Solving**: Complex debugging requiring advanced analysis

## Examples

### Bug Fix Escalation

```bash
# After 3 failed attempts at fixing authentication bug
/codex:codex --debug --attempts 3 "Authentication middleware failing with token validation"

Output:
【タスク種別】: バグ修正
【背景】: 認証ミドルウェアでトークン検証が3回の修正試行後も失敗
【これまでの試行】: 1) JWT署名検証修正 2) トークン有効期限チェック 3) ヘッダー解析ロジック
【要求事項】: 根本原因の特定と確実な修正方針
【制約条件】: 既存セッション保持、ダウンタイム最小化
```

### Specification Discussion

```bash
/sc:codex --spec "real-time notification system with offline support"

Output:
【タスク種別】: 仕様検討
【背景】: リアルタイム通知システムのオフライン対応設計
【要求事項】: アーキテクチャ設計、技術選定、実装方針
【制約条件】: 既存システム統合、スケーラビリティ確保
```

### Code Review Request

```bash
/codex:codex --review @payment-processor.js --priority high

Output:
【タスク種別】: コード生成
【背景】: 決済処理システムの品質評価と改善提案
【要求事項】: セキュリティ、パフォーマンス、保守性の総合評価
【制約条件】: PCI DSS準拠、既存API互換性
```

## Quality Gates

### Pre-Delegation Validation

- Clear problem statement and context
- Previous attempts documented (for escalation)
- Expected outcome defined
- Technical constraints identified

### Post-Response Integration

- Validate Codex recommendations against project standards
- Test proposed solutions before implementation
- Document architectural decisions and rationale
- Update project knowledge base with insights

## Integration with SuperClaude

### Framework Alignment

- Follows SuperClaude task management patterns
- Integrates with existing agent delegation system
- Maintains consistent command syntax and behavior
- Supports parallel execution with other tools

### Cross-Agent Coordination

- Coordinates with specialized agents for implementation
- Preserves Codex strategic insights for future reference
- Enables seamless handoff between analysis and execution
- Maintains quality standards across all operations

## Best Practices

### When to Use Codex

✅ **Use for**: Strategic decisions, complex problem solving, multi-domain analysis
❌ **Don't use for**: Simple code changes, basic debugging, routine operations

### Effective Prompting

✅ **Include**: Specific context, constraints, previous attempts, expected outcomes
❌ **Avoid**: Vague requests, missing context, unclear requirements

### Response Handling

✅ **Validate**: Test recommendations, verify against standards, document decisions
❌ **Blindly follow**: Implement without validation, ignore project constraints

This command bridges strategic thinking with tactical implementation, ensuring complex problems receive appropriate expertise while maintaining SuperClaude framework quality and efficiency.
