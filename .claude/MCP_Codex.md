# Codex MCP Server

**Purpose**: Advanced AI collaboration engine for complex code generation, architectural decisions, and strategic analysis

## Triggers

- バグ修正が3回以上失敗した場合のエスカレーション
- 複雑なアーキテクチャ設計や仕様策定が必要な場合
- 作成済みコードのレビューや改善提案が必要な場合
- タスクの複雑性が高く、複数の専門エージェントによる検討が必要な場合
- 重要な機能の追加・編集で多角的な検討が必要な場合

## Choose When

- **Over native Claude**: 3回以上の失敗後、複雑な設計判断、専門的レビュー要求時
- **For strategic decisions**: アーキテクチャ設計、仕様策定、技術選択の判断
- **For quality assurance**: コードレビュー、潜在的バグ特定、改善提案
- **For delegation**: 複雑タスクのサブタスク分解と専門エージェント割り当て
- **Not for simple tasks**: 基本的なコード修正、単純な実装、通常の開発作業

## Works Best With

- **Sequential**: Sequential が問題を構造化 → Codex が解決戦略を策定
- **Serena**: Serena がプロジェクト context を提供 → Codex が architectural analysis
- **Context7**: Context7 が framework patterns → Codex が implementation strategy
- **Business Panel**: Business requirements → Codex が technical implementation roadmap

## Delegation Patterns

### Bug Fix Escalation

```yaml
trigger: "3回以上の失敗"
claude_role: "失敗履歴の整理と問題の詳細化"
codex_role: "高度な debugging と根本原因分析"
handoff: "失敗詳細 + これまでの試行内容 → Codex"
```

### Architecture Design

```yaml
trigger: "複雑なシステム設計要求"
claude_role: "要件整理とタスク分解"
codex_role: "アーキテクチャ設計と技術選択"
handoff: "要件 + 制約条件 → Codex"
```

### Code Review

```yaml
trigger: "重要機能の品質保証"
claude_role: "初期実装とレビュー依頼"
codex_role: "詳細レビューと改善提案"
handoff: "実装コード + レビュー観点 → Codex"
```

### Task Decomposition

```yaml
trigger: "複雑タスクの専門分野別分割"
claude_role: "全体計画とタスク統合"
codex_role: "専門エージェントとしての実装"
handoff: "具体的実装指示 → Codex"
```

## Integration with SuperClaude Framework

### Command Integration

- **Automatic Escalation**: `/codex:codex --escalate --attempts 3` (失敗時の自動エスカレーション)
- **Strategic Analysis**: `/codex:codex --architecture --requirements @doc.md`
- **Code Review**: `/codex:codex --review --code @implementation/`
- **Task Delegation**: `/codex:codex --delegate --subtasks @complex_task.md`

### Workflow Patterns

```yaml
escalation_workflow:
  phase_1: "Claude による初期実装試行"
  phase_2: "失敗カウントが3に到達"
  phase_3: "自動的に Codex MCP エスカレーション"
  phase_4: "Codex による高度な問題解決"
  phase_5: "Claude による結果統合と検証"

design_workflow:
  phase_1: "Claude による要件分析"
  phase_2: "Codex による architecture design"
  phase_3: "Claude による implementation"
  phase_4: "Codex による design review"
  phase_5: "反復的改善サイクル"
```

## Quality Standards

### Handoff Requirements

- **Context Completeness**: 失敗履歴、試行内容、制約条件を完全に提供
- **Clear Scope**: Codex への依頼範囲を明確に定義
- **Success Criteria**: 成功の判定基準を明示
- **Integration Plan**: Codex の成果物をどう統合するかを計画

### Collaboration Principles

- **Complementary Roles**: Claude と Codex の役割を相補的に設計
- **Evidence-Based Handoff**: 具体的な証拠に基づく escalation
- **Validation Required**: Codex の出力を必ず検証・テスト
- **Learning Integration**: Codex からの学習を Claude の今後の作業に活用

## Examples

### Bug Fix Escalation

```
Claude 3回失敗 →
"/sc:codex --escalate" +
"問題: authentication middleware でのtoken validation失敗
試行1: JWT decode logic 修正 → まだ失敗
試行2: middleware order 変更 → まだ失敗
試行3: async/await パターン変更 → まだ失敗
エラー: [詳細なエラーログ]"

Codex response → 根本原因特定 + 修正戦略
Claude → Codex戦略の実装 + 検証
```

### Architecture Review

```
Claude implementation →
"/sc:codex --review --focus architecture" +
"実装したマイクロサービス architecture:
- API Gateway + 3 services
- Event-driven communication
- Database per service pattern"

Codex response → アーキテクチャ分析 + 改善提案
Claude → 提案の評価 + 必要に応じて実装
```

### Strategic Delegation

```
Complex Feature Request →
"/sc:codex --delegate --breakdown" +
"要求: Real-time collaborative editing system
制約: 10万concurrent users, <100ms latency
技術スタック: Node.js, WebSocket, Redis"

Codex response → サブタスク分解 + 専門エージェント配置
Claude → 各サブタスクの調整 + 統合
```
