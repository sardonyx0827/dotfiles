# Codex MCP Server

**Purpose**: OpenAI Codex による高度な問題解決・コード生成エンジン。バグ修正の失敗時のエスカレーション、アーキテクチャ設計、戦略的分析に使用する

## Tools

- `mcp__codex__codex` - 新規セッション開始（`prompt` 必須）
- `mcp__codex__codex-reply` - 既存セッションの会話継続（`threadId` + `prompt` 必須）

### 主要パラメータ（`mcp__codex__codex`）

| パラメータ        | 説明                   | 値の例                                               |
| ----------------- | ---------------------- | ---------------------------------------------------- |
| `prompt`          | 初期プロンプト（必須） | 問題の詳細な記述                                     |
| `sandbox`         | サンドボックスモード   | `read-only`, `workspace-write`, `danger-full-access` |
| `approval-policy` | シェルコマンド承認     | `untrusted`, `on-failure`, `on-request`, `never`     |
| `model`           | モデル指定             | `gpt-5.2`, `gpt-5.2-codex` 等                        |
| `cwd`             | 作業ディレクトリ       | プロジェクトルートパス                               |

## Triggers

- バグ修正が1回以上失敗した場合のエスカレーション
- アーキテクチャ設計・仕様策定が必要な場合
- 既存コードのレビュー・改善提案が必要な場合
- Claude 単体では解決困難な複雑タスク

## Choose When

- **Over native Claude**: 修正の失敗後、複雑な設計判断、専門的レビューが必要な場合
- **For strategic decisions**: アーキテクチャ設計、技術選定、仕様策定
- **For quality assurance**: コードレビュー、潜在バグの特定、改善提案
- **Not for simple tasks**: 基本的なコード修正、単純な実装、ルーティン作業

## Works Best With

- **Serena**: Serena でプロジェクトコンテキスト取得 → Codex でアーキテクチャ分析
- **Context7**: Context7 でフレームワークパターン取得 → Codex で実装戦略策定

## 会話継続パターン

Codex は会話型セッションをサポートする。複数ターンのやり取りが必要な場合：

1. `mcp__codex__codex` で初回セッションを開始し、レスポンスから `threadId` を取得
2. `mcp__codex__codex-reply` に `threadId` と追加の `prompt` を渡して会話を継続
3. 必要に応じて複数回 `codex-reply` を繰り返す

## codex-delegator エージェントとの使い分け

- **`codex-delegator` エージェント（Task tool 経由）**: 仕様検討、バグ修正方針の相談、複雑な技術判断の委譲に使用。Claude が複数回失敗した場合の自動エスカレーションにも対応
- **直接 MCP ツール呼び出し**: Codex のパラメータ（sandbox、model 等）を細かく制御したい場合、または既存の会話を `codex-reply` で継続したい場合

## エスカレーションフロー

```
Claude で実装 → テスト/検証で失敗
  → 失敗の詳細 + 試行内容を prompt にまとめる
  → Codex に委譲（根本原因分析 + 修正戦略）
  → Codex の回答を Claude で実装・検証
```

## Examples

```
"バグ修正が2回失敗した"         → Codex（エスカレーション）
"システム全体の設計を検討したい"   → Codex（アーキテクチャ設計）
"このコードの品質をレビューして"   → Codex（詳細レビュー + 改善提案）
"単純なtypoを直して"            → Native Claude（Codex 不要）
```
