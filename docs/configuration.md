# 設定リファレンス

このドキュメントは [README.md](../README.md) から分離した、各ツールの設定・ユーティリティ・カスタマイズの詳細です。

## 目次

- [主要設定の説明](#主要設定の説明)
- [ユーティリティスクリプト](#ユーティリティスクリプト)
- [カスタマイズ](#カスタマイズ)

## 主要設定の説明

### Zsh (.zshrc)

- **テーマ**: `px-rose-pine`（`.oh-my-zsh/custom/themes/` に vendored。[pixeljae](https://github.com/pixeljae) 製の agnoster ベーステーマを Rosé Pine 配色に合わせて調整したもので、完全な自作ではありません）
- **プラグイン**: `git` / `zsh-autosuggestions` / `zsh-syntax-highlighting` / `z`
- **PATH設定**: Go、npm global、PHP 8.4、pyenv、Rust (cargo)
- **言語環境**: `pyenv`（インストール時のみ初期化）、Rust の cargo bin
- **履歴**: 10万件・`share_history` / 重複除去などの最適化
- **fzf 連携関数**: `cf`（ディレクトリ移動）/ `vf`（プレビュー付きで開く）/ `sshs`（SSH ホスト選択）
- **AI ツールのエイリアス / 関数**:
  - `c` / `cl`: Claude Code、`cx`: Codex、`ge` / `g`: Gemini CLI、`cop`: GitHub Copilot CLI
  - `mc`（補完付き）: `mc explain` / `mc translate` / `mc commit` / `mc push` など Claude を用途別モデルで起動
  - `commit` / `push` / `pull_request` / `translate`: Gemini CLI ベースの Git・翻訳ショートカット
  - `update_ai_tools`: `scripts/update_ai_tools.sh` を実行

```bash
# 主要なPATH設定
export PATH=~/go/bin:$PATH                          # Go
export PATH=~/.npm-global/bin:$PATH                 # npm global
export PATH="/opt/homebrew/opt/php@8.4/bin:$PATH"   # PHP 8.4
export PATH="$PYENV_ROOT/bin:$PATH"                 # pyenv
export PATH="$HOME/.cargo/bin:$PATH"                # Rust
```

### Vim (.vimrc → .vim/rc/)

- **構成**: `.vimrc` は薄いローダーで、実体は `.vim/rc/*.vim` を番号順（`00-plugins` → `10-basic` → … → `80-custom`）に読み込み。`resolve()` でシンボリックリンクを辿るため `.vim/rc` の追加リンクは不要
- **プラグインマネージャー**: vim-plug
- **カラースキーム**: Rosé Pine（`rose-pine/vim`）
- **主要プラグイン**:
  - NERDTree: ファイルエクスプローラー（フローティングプレビュー付き）
  - vim-fugitive / vim-rhubarb / vim-gitgutter: Git 統合
  - vim-airline: ステータスライン
  - fzf / fzf.vim: ファジーファインダー（Neovim 側 telescope と同一キーマップ）
  - vim-lsp + vim-lsp-settings + asyncomplete 系: LSP・補完（対象ファイルを開いて `:LspInstallServer` でサーバー導入。キーマップは Neovim 側 `nvim-lspconfig.lua` と同一: `gd` / `K` / `<leader>ra` / `<leader>ca` / `gr` / `[d` / `]d` など）
  - ALE: 非同期 Lint（LSP 機能は vim-lsp に委譲）
  - tagbar: タグ一覧表示（`F4`）
  - vim-gutentags: 保存時に `tags` を自動生成・更新（`universal-ctags` が必要）
  - vim-easymotion: 高速カーソル移動、copilot.vim: GitHub Copilot 補完

#### タグジャンプ（定義ジャンプ）

`universal-ctags` と vim-gutentags により、ファイルをまたいだ定義ジャンプが可能です。

| キー                | 動作                                   |
| ------------------- | -------------------------------------- |
| `Ctrl-t` / `Ctrl-]` | カーソル下の定義へジャンプ             |
| `Ctrl-o`            | ジャンプ前の位置へ戻る                 |
| `g Ctrl-]`          | 候補が複数あるとき一覧表示してジャンプ |

> `tags` は vim-gutentags が保存時に自動更新します。手動生成する場合は対象ディレクトリで `ctags -R .` を実行してください。
> LSP が有効なバッファでは `Ctrl-t` / `gd` は LSP の定義ジャンプ（`<plug>(lsp-definition)`）に置き換わります。

### Neovim (.config/nvim/)

- **プラグインマネージャー**: lazy.nvim（Lua ベースのモダン構成）
- **LSP / 補完 / Treesitter**: 言語サーバープロトコル統合、シンタックスハイライト
- **主要プラグイン**: telescope（ファジーファインダー）、nvim-tree、gitsigns / fugitive / neogit / diffview、trouble、toggleterm、nvim-dap（デバッガ）、zen-mode、auto-session
- **AI 連携**: copilot.lua
- **カラースキーム**: Rosé Pine（gruvbox / kanagawa / tokyonight / onedark なども同梱）

### tmux (.tmux.conf)

- **プレフィックスキー**: `Ctrl+a` (デフォルトの `Ctrl+b` から変更)
- **256色ターミナル**: RGBカラー対応
- **viモード**: コピーモードでviキーバインド使用
- **マウス操作**: ペイン端のダブルクリックで分割、それ以外はコピーモード
- **テーマ / プラグイン**（tpm で管理）: `rose-pine/tmux`（moon）、`tmux-mode-indicator`、`tmux-sensible`、`tmux-logging`、`tmux-easy-motion`（`f` プレフィックスで高速カーソル移動）
- **クリップボード統合**:
  - macOS: pbcopy/pbpaste
  - Linux: xsel
- **ロギング**: `Ctrl+a C-p` 開始 / `Ctrl+a C-o` 停止（`~/.tmux/log` に保存）

#### 主要なキーバインド

```
# ペイン操作
Ctrl+a h/j/k/l  # ペイン間移動 (Vim風)
Ctrl+a H/J/K/L  # ペイン入れ替え
Ctrl+a C-h/j/k/l# ペインのリサイズ
Ctrl+a C-s      # 横分割 (current path)
Ctrl+a C-v      # 縦分割 (current path)
Ctrl+a e        # 全ペイン同期 (synchronize-panes)
Ctrl+a o        # カレント以外のペインを閉じる
Ctrl+a C-q      # カレントペインを閉じる

# ウィンドウ操作
Ctrl+a c        # 新規ウィンドウ (current path)
Ctrl+a N        # 'dev' ウィンドウを作成し claude を起動
Ctrl+a C-c      # 右に幅30%のペインを開き claude を起動

# コピーモード
Ctrl+a [        # コピーモード開始
v               # 選択開始
y               # ヤンク（コピー）
Ctrl+a ]        # ペースト
```

### WezTerm (.wezterm.lua)

- **カラースキーム**: Rosé Pine
- **フォント**: Ubuntu Mono (14pt) — フォールバックに Hiragino Sans
- **カーソル**: BlinkingBlock
- **背景透過**: 90%
- **タブバー**: タブが1つのときは非表示
- **日本語入力**: IME対応
- **キーバインド**: macOS用のバックスラッシュ入力（`¥` → `\`、`option + ¥` → `¥`）、`option + Enter` でフルスクリーン切替

### Git (.gitconfig)

- **エディタ**: neovim
- **マージツール**: nvimdiff
- **カスタムエイリアス**:
  - `git ls`: グラフ付きログ（簡潔版）
  - `git ll`: グラフ付きログ（詳細版）
  - `git la`: グラフ付きログ（完全版）
- **GitHub CLI統合**: 認証情報ヘルパー

### AI 開発環境の設定（.claude / .codex / .gemini）

各 AI CLI の設定をリポジトリで一元管理しています。`install.sh` は CLI の実行時データ（履歴・セッション等）を巻き込まないよう、ディレクトリ全体ではなく必要なエントリのみを個別にシンボリックリンクします。

- **`.claude/`**: Claude Code のグローバル指示（`CLAUDE.md`）、`settings.json`、カスタムサブエージェント（`agents/`）、スラッシュコマンド（`commands/`）、フック（`hooks/`）、スキル（`skills/`）、ワークフロー / セキュリティルール（`rules/`）、自作 MCP サーバー（`mcp-servers/`）、ステータスライン
- **`.codex/`**: Codex 向け指示（`AGENTS.md`）、エージェント定義（`agents/*.toml`）、フック（`hooks/` + `hooks.json.template`）、スキル（`skills/`）、`config.toml.template`（`hooks.json` / `config.toml` の実体は `install.sh` が `~/.codex/` 側へ生成）
  - `agents/` と `skills/` はディレクトリごとリンクします。共有スキルはリポジトリ内の相対シンボリックリンク（`.codex/skills/<name>` → `../../.claude/skills/<name>`）として定義しており、`.codex/hooks/_*.sh` と同じ方式です。共有の追加・削除はこのリンクの増減で行い、`install.sh` 側にスキル一覧は持ちません
  - `~/.codex/skills` はチェックアウト内を指すため、Codex が書き込む組込みスキル `.system/` はリポジトリ配下に出現します（`.gitignore` 済み）
- **`.gemini/`**: Gemini CLI の指示（`GEMINI.md`）と `settings.json`

> **⚠️ セキュリティモデル（`.claude/settings.json` はフックとセットで安全）**
>
> `settings.json` の `permissions.allow` は `Bash(npx:*)` / `Bash(pip:*)` /
> `Bash(cargo:*)` / `Bash(docker:*)` など**任意コード実行になり得るコマンドを自動承認**します。
> これは単体で安全なのではなく、`PreToolUse` フック `hooks/bash-review.py`
> が**実行前に Bash コマンドをレビューし、API/CLI 不在時は `ask`
> にフェイルクローズする**ことと**セットで**成り立っています（機密ファイル読取や
> `git push` も別フックでガード）。高リスクコマンド（再帰 `rm`・force push・
> パッケージ導入・インラインシェル等）は Gemini / Codex を並列実行する **AND
> ゲート**で、**両モデルが ALLOW で一致したときのみ許可**、DENY 一致で拒否、
> 判定が割れた場合は両判定を添えてユーザー確認（ask）へ回します。
>
> ただし `ls` / `cat` / `git log` のような読み取り系は、レイテンシ削減のため
> **レビューを呼ばずに素通しする高速パス（safe-skip）**を通ります。「すべての
> コマンドが LLM に見られる」わけではありません。この高速パスは機密パス・
> 出力先ファイル指定フラグ・シェル制御構文を含む場合には無効化されます
> （判定は `_bash_review_common.py` の `_can_skip_review`）。
>
> **このフックの限界（重要）**: bash-review は**暴走しがちなエージェントに対する
> ガードレールであって、敵対的なセキュリティ境界ではありません**。防御を積極的に
> 破ろうとする人間に耐えることは設計目標に含まれていません。**本当の境界は
> `settings.json` の `permissions.deny`** であり、bash-review はその上に乗る
> 助言レイヤーです。したがって `permissions.deny` 側に穴があると、フックが
> 唯一の砦になってしまいます（設計根拠と既知のトレードオフの全文は
> [.claude/hooks/README.md](../.claude/hooks/README.md) の "bash-review — design
> rationale & threat model" を参照）。
>
> したがって **`settings.json` だけを他環境へコピーし `hooks/` を導入しないと、
> 実行時ガードが外れて allowlist がそのまま「任意コード実行の自動承認」になります。**
> 必ず `hooks/`（と `bash-review.py` が使う `GEMINI_API_KEY`）もあわせて配置してください。
> `install.sh` は両方を同時にリンクするため、正規の手順で導入する限りこの前提は満たされます。

## ユーティリティスクリプト

### scripts/tmux_send_to_all_except_nvim.sh

tmuxの全ペインにコマンドを送信しますが、nvimが実行中のペインは除外します。

```bash
# 使用例
./scripts/tmux_send_to_all_except_nvim.sh "git status"
```

### scripts/update_ai_tools.sh

全てのAI開発ツールを一括で更新します。

```bash
# 実行
./scripts/update_ai_tools.sh

# 対象ツール
# - Claude Code
# - Codex
# - Gemini CLI
# - GitHub Copilot CLI
```

## カスタマイズ

### Zshテーマの変更

`.zshrc`を編集：

```bash
# テーマを変更
ZSH_THEME="your-theme-name"
```

### Vimプラグインの追加

`.vimrc`を編集：

```vim
" Plug install packagesセクションに追加
call plug#begin()
Plug 'username/plugin-name'
call plug#end()
```

その後、Vimで`:PlugInstall`を実行。

### tmuxプレフィックスキーの変更

`.tmux.conf`を編集：

```bash
# プレフィックスを変更（例: Ctrl+b）
set -g prefix C-b
bind C-b send-prefix
unbind C-a
```
