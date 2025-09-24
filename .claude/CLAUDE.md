# CLAUDE.md

## 0. 目的
このプロジェクトで Claude Code が作業する際の「言語・ブラウザ操作・Git 運用」を明示する。

## 1. 言語ポリシー（必須）
- すべての対話・出力は **日本語** で行うこと
- **Git のコミットメッセージは英語**で記述すること（後述の規約に従う）

## 2. ブラウザ操作（必須）
- Web ブラウズ／UI 自動化が必要な作業は **MCP の Playwright** のみを使用すること
- `fetch` / `curl` など **他の手段によるネットワークアクセスは原則禁止**
  必要な場合は理由を説明し、Playwright MCP で代替できないかを検討する

## 3. Git 運用（必須）
  ### 基本的なpush運用
  - 「push して」という指示を受けた場合は、以下の順序で実施すること :
    1. 変更点の確認（`git status` / `git diff`）
    2. ステージング（`git add`）・ すでにステージングされているものがある場合は新たにステージングしない
    3. コミット (コミットメッセージ規約に従う)
    4. リモートへのプッシュ（デフォルトブランチに直 push が不適切な場合は PR 作成を提案）
  - 「commit して」という指示を受けた場合は、以下の順序で実施すること :
    1. 変更点の確認（`git status` / `git diff`）
    2. ステージング（`git add`）・ すでにステージングされているものがある場合は新たにステージングしない
    3. コミット

  ### Pull Request作成の自動化
  - 「pr作成して」という指示を受けた場合は、以下の手順を自動実行すること :
    1. 現在の変更状況とブランチ構成を確認
    2. 現在のブランチから新しい作業用ブランチを作成（命名規則：`fix/`, `feat/`, `style/`等の接頭辞を使用）
    3. 変更をコミット (コミットメッセージ規約に従う)
    4. 新しいブランチをリモートにプッシュ
    5. 元のブランチに対してPull Requestを作成（日本語の詳細な説明付き）
    6. 元のブランチに戻る
    7. 作業用ブランチをローカル・リモートともに削除してクリーンアップ

  ### コミットメッセージ規約
  - コミットメッセージは **要約（50 文字程度） + 必要ならボディ**。自動生成時も英文の簡潔さを優先
  - コミットメッセージは英語でConventional Commitsに準拠 (例：`feat: ...`, `fix: ...`, `docs: ...`）
    言語の指定があった場合を除き、常に英語で記述すること。
  - Pull Requestの説明は日本語で詳細に記述すること

## 4. セーフティガード
- ファイル編集・依存追加・外部通信は、プロジェクトの既定ルールに従うこと
- 危険操作は事前に差し戻すこと
- ブラウザ自動化の対象サイトは最小限に限定し、個人情報や秘密情報を扱わない

## 5. 実行時チェック
- 起動後 `/status` でワークスペース/権限を確認し、必要に応じて設定を提案する

# ═══════════════════════════════════════════════════
# SuperClaude Framework Components
# ═══════════════════════════════════════════════════

# Core Framework
@BUSINESS_PANEL_EXAMPLES.md
@BUSINESS_SYMBOLS.md
@FLAGS.md
@PRINCIPLES.md
@RULES.md

# Behavioral Modes
@MODE_Brainstorming.md
@MODE_Business_Panel.md
@MODE_Introspection.md
@MODE_Orchestration.md
@MODE_Task_Management.md
@MODE_Token_Efficiency.md

# MCP Documentation
@MCP_Context7.md
@MCP_Playwright.md
@MCP_Serena.md
@MCP_Sequential.md
@MCP_Codex.md
