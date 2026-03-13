# Codex MCP Server

**Purpose**: OpenAI Codex による高度な問題解決・コード生成エンジン。
アーキテクチャ設計、戦略的分析、バグ修正などの意見収集に使用する。
コードの生成も可能だが**ユーザーからの指示がない限り絶対に実装はさせず**、設計や戦略などの議論のみに利用すること。
Codex MCPから回答を得た後は、Claudeで意見の妥当性を検証すること。

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
| `model` | モデル指定 | 推論タスク `gpt-5.4`, コードの読み取りを含むタスク `gpt-5.3-codex` |
| `cwd` | 作業ディレクトリ | プロジェクトルートパス |

### `mcp__codex__codex-reply` パラメータ

| パラメータ | 説明 |
|------------|------|
| `threadId` | 前回レスポンスの `structuredContent.threadId`（必須） |
| `prompt` | 追加の指示・質問（必須） |

## いつ使うか

- 仕様を提案、設計、大規模修正を行う場合のダブルチェックおよび意見収集
- エラーの修正に2回連続で失敗し、根本原因分析や修正戦略の立案が必要な場合の意見収集

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

## 推奨されるPrompt

Codex MCPの`prompt`パラメータには可能な限り以下の4点を含めることが推奨される：
- **Goal**: 何を変えたい、または構築したいのか
- **Context**: このタスクに関連するファイル、フォルダ、ドキュメント、例、エラーは何か
- **Constraints**: Codexが従うべき標準、アーキテクチャ、安全要件、または規約は何か
- **Done when**: タスク完了前に満たすべき条件

## codex-delegator エージェントとの使い分け

- **`codex-delegator` エージェント（Task tool 経由）**: 仕様検討、バグ修正方針の相談、複雑な技術判断の委譲。Claude が複数回失敗した場合の自動エスカレーション
- **直接 MCP ツール呼び出し**: `sandbox` や `model` を細かく制御したい場合、または `codex-reply` で既存会話を継続する場合

## Examples
```
"仕様を提案したり、設計、大規模修正を行う" → Codex(意見収集)
"エラーの修正に2回連続で失敗し、根本原因分析や修正戦略の立案が必要な場合" → Codex(意見収集)
```
