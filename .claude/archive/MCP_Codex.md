# Codex MCP Server

**Purpose**: OpenAI Codex による高度な問題解決・コード生成エンジン。
バグ修正の失敗時のエスカレーション、アーキテクチャ設計、戦略的分析に使用する。

## Tools

| ツール | 用途 |
|--------|------|
| `mcp__codex__codex` | 新規セッション開始（`prompt` 必須） |
| `mcp__codex__codex-reply` | 既存セッションの継続（`threadId` + `prompt` 必須） |

### `mcp__codex__codex` パラメータ

| パラメータ | 説明 | 値の例 |
|------------|------|--------|
| `prompt` | 初期プロンプト（必須） | 問題の詳細な記述 |
| `sandbox` | サンドボックスモード | `read-only`, `workspace-write`, `danger-full-access` |
| `approval-policy` | シェルコマンド承認 | `untrusted`, `on-failure`, `on-request`, `never` |
| `model` | モデル指定 | `gpt-5.2`, `gpt-5.2-codex` 等 |
| `cwd` | 作業ディレクトリ | プロジェクトルートパス |

### `mcp__codex__codex-reply` パラメータ

| パラメータ | 説明 |
|------------|------|
| `threadId` | 前回レスポンスの `structuredContent.threadId`（必須） |
| `prompt` | 追加の指示・質問（必須） |

## いつ使うか

- バグ修正が2回以上失敗した場合のエスカレーション
- アーキテクチャ設計・仕様策定・技術選定
- 既存コードのレビュー・改善提案
- **不要な場面**: 基本的なコード修正、単純な実装、ルーティン作業

## エスカレーションフロー
```
Claude で実装 → テスト/検証で失敗
  → 失敗の詳細 + 試行内容を prompt にまとめる
  → Codex に委譲（根本原因分析 + 修正戦略）
  → Codex の回答を Claude で実装・検証
```

## 会話継続パターン

1. `mcp__codex__codex` で初回セッションを開始
2. レスポンスの `structuredContent.threadId` を取得
3. `mcp__codex__codex-reply` に `threadId` と `prompt` を渡して継続
4. 必要に応じて手順3を繰り返す

**注意**: MCP サーバー再起動後は threadId が無効になるため、セッションをまたいだ継続は不可。

## Works Best With

- **Serena**: Serena でプロジェクトコンテキスト取得 → Codex でアーキテクチャ分析
- **Context7**: Context7 でフレームワークパターン取得 → Codex で実装戦略策定

## codex-delegator エージェントとの使い分け

- **`codex-delegator` エージェント（Task tool 経由）**: 仕様検討、バグ修正方針の相談、複雑な技術判断の委譲。Claude が複数回失敗した場合の自動エスカレーション
- **直接 MCP ツール呼び出し**: `sandbox` や `model` を細かく制御したい場合、または `codex-reply` で既存会話を継続する場合

## Examples
```
"バグ修正が2回失敗した"          → Codex（エスカレーション）
"システム全体の設計を検討したい"  → Codex（アーキテクチャ設計）
"このコードの品質をレビューして"  → Codex（詳細レビュー）
"単純なtypoを直して"             → Native Claude（Codex 不要）
```
