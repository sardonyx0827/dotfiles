# CLAUDE.md

## 言語ポリシー
- すべての対話 / 出力は **日本語** で行うこと
- **Git のコミットメッセージは英語**で記述すること(`@~/.claude/rules/git-workflow.md` の規約に従う)

## ブラウザ操作
- Web コンテンツの取得/操作は claude-in-chrome を使うこと
- fetch / curl が必要な場合は理由を説明してから実行すること

## Git 運用
push / commit / PR作成 の指示を受けた場合は `@~/.claude/rules/git-workflow.md` の Command Triggers に従うこと

## SubAgent / AgentTeam の使い分け
- デフォルト: SubAgent。並列化可能なタスク(情報収集 / 複数案生成 / テスト生成 / レビュー)は積極的に並列起動
- AgentTeams (tmux) を使うケース:
  - 明確な指示があった場合
  - クロスレイヤー調整(FE/BE/テストにまたがる変更)
  - 競合仮説のデバッグ、レビュー往復、横断的整合性の維持

## SubAgent / AgentTeam のモデル指定指針
- Haiku: Glob/Grep など推論不要な作業
- Sonnet: 実装 / デバッグ(デフォルト)
- Opus: 設計 / 大規模リファクタ / 全体分析
- 失敗時は一段上のモデルで再試行

## セーフティガード
- 破壊的操作(rm -rf / force push / 本番DB操作等)は実行前に必ず確認
- 個人情報・秘密情報はブラウザ自動化の対象外

## 外部エージェント連携
仕様検討や設計、バグ修正、テストコード作成を行う場合は `@~/.claude/rules/MCP_Codex.md` に従うこと。
