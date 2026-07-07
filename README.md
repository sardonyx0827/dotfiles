# Dotfiles

個人用の開発環境設定ファイル（dotfiles）のリポジトリです。Zsh、Vim、Neovim、tmux、WezTerm などの設定に加え、Claude Code・Codex・Gemini CLI・GitHub Copilot CLI といった AI 開発ツールのエージェント／スキル／フック設定とセットアップスクリプトを一括管理しています。全体を [Rosé Pine](https://rosepinetheme.com/) カラースキームで統一し、macOS / Ubuntu / WSL に対応した `install.sh` でシンボリックリンクを自動生成します。

## 技術スタック

- **シェル / ターミナル**: Zsh, Oh My Zsh, tmux, WezTerm
- **エディタ**: Neovim (lazy.nvim), Vim (vim-plug), VS Code
- **バージョン管理**: Git, GitHub
- **AI開発ツール**: Claude Code, Codex, Gemini CLI, GitHub Copilot CLI
- **言語**: Lua, Vimscript, Shell Script

## ファイル構成

```
.
├── .claude/                        # Claude Code設定
│   ├── agents/                     # カスタムサブエージェント (architect, code-reviewer 等)
│   ├── archive/                    # 旧エージェント / コマンド / ルールのアーカイブ
│   ├── commands/                   # カスタムスラッシュコマンド (tdd, verify 等)
│   ├── hooks/                      # フック (auto-format, lint, bash-review 等)
│   ├── mcp-servers/                # 自作MCPサーバー (gemini-consultant)
│   ├── rules/                      # ワークフロー / セキュリティルール
│   ├── skills/                     # スキル定義 (coding-standards 等)
│   ├── CLAUDE.md                   # グローバル指示
│   ├── settings.json               # Claude Code設定
│   └── statusline-command.sh       # ステータスライン
├── .codex/                         # Codex設定
│   ├── agents/                     # Codexエージェント定義 (.toml)
│   ├── hooks/                      # Codexフック (+ hooks.json)
│   ├── skills/                     # Codexスキル (.system + 厳選共有)
│   ├── AGENTS.md                   # Codex向け指示
│   └── config.toml                 # Codex設定
├── .config/                        # アプリケーション設定
│   ├── Antigravity/                # Antigravity (settings / keybindings)
│   ├── Code/                       # VS Code (settings / keybindings)
│   └── nvim/                       # Neovim (lazy.nvim) 設定
├── .gemini/                        # Gemini CLI設定 (GEMINI.md, settings.json)
├── .github/workflows/              # GitHub Actions CI (pytest / ruff / bandit / shellcheck)
├── .oh-my-zsh/                     # Oh My Zsh設定
│   └── custom/themes/              # カスタムテーマ (px-rose-pine)
├── .vim/rc/                        # Vim設定本体 (分割ロード: 00-plugins, 10-basic ...)
├── .vscode/                        # VS Code (ワークスペース) 設定
├── .gitconfig                      # Git設定
├── .gitignore_global               # グローバルgitignore
├── .tmux.conf                      # tmux設定
├── .vimrc                          # 薄いローダー (.vim/rc/*.vim を順次source)
├── .wezterm.lua                    # WezTerm設定
├── .zshrc                          # Zsh設定
├── INSTALL_PLATFORM.md             # プラットフォーム別インストール / トラブルシューティング
├── install.sh                      # クロスプラットフォーム対応インストールスクリプト
├── pytest.ini                      # pytest設定
├── tests/                          # フック / スクリプトの pytest スイート (hermetic)
├── tmux_send_to_all_except_nvim.sh # tmuxユーティリティ
└── update_ai_tools.sh              # AIツール更新スクリプト
```

## セットアップ

### 自動インストール（推奨）

**対応プラットフォーム**: macOS、Ubuntu/Debian、Windows (WSL/Git Bash)

1. **リポジトリのクローン**

```bash
git clone https://github.com/sardonyx0827/dotfiles.git ~/dotfiles
cd ~/dotfiles
```

2. **自動インストールスクリプトの実行**

```bash
./install.sh
```

このスクリプトは以下を自動的に行います：

- プラットフォームの検出（macOS/Ubuntu/Windows）
- 必要なパッケージのインストール
  - macOS: Homebrew経由でVim、Neovim、tmux、WezTermなど
  - Ubuntu: APT経由でVim、Neovim、tmux、WezTermなど
- CLI ツール（ripgrep、fd、bat、universal-ctags、tree-sitter、lazydocker など）のインストール
- Oh My Zshとプラグインのインストール
- vim-plugのインストール
- Node.jsとnpmのセットアップ
- Linter / Formatter（prettier、eslint など。フックが利用）のインストール
- フォントのインストール
- 設定ファイルのシンボリックリンク作成（既存ファイルは自動バックアップ）
- Claude Code への MCP サーバー登録
- デフォルトシェルをZshに変更

3. **AIツールのインストール（オプション）**

スクリプト実行中に AI 開発ツール（Claude Code / Codex / Gemini CLI / GitHub Copilot CLI）のインストールを選択できます。

4. **ターミナルの再起動**

```bash
# インストール完了後、ターミナルを再起動
```

5. **Neovimのセットアップ完了**

```bash
# Neovimを開いてlazy.nvimプラグインを自動インストール
nvim
```

> **注意**: プラットフォーム固有の詳細な手順やトラブルシューティングについては、[INSTALL_PLATFORM.md](INSTALL_PLATFORM.md)を参照してください。

### 手動インストール

自動インストールが利用できない場合や、個別にカスタマイズしたい場合は以下の手順を実行してください。

#### 前提条件

- [Git](https://git-scm.com/)
- [Homebrew](https://brew.sh/) (macOS) または APT (Ubuntu/Debian)

#### 手順

<details>
<summary>1. リポジトリのクローン</summary>

```bash
git clone https://github.com/sardonyx0827/dotfiles.git ~/dotfiles
cd ~/dotfiles
```

</details>

<details>
<summary>2. パッケージマネージャーのセットアップ</summary>

**macOS:**

```bash
# Homebrewのインストール（未インストールの場合）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Ubuntu/Debian:**

```bash
sudo apt-get update
```

</details>

<details>
<summary>3. 必要なパッケージのインストール</summary>

**macOS:**

```bash
brew install git zsh vim neovim tmux curl wget
brew install --cask wezterm
brew tap homebrew/cask-fonts
brew install --cask font-ubuntu-mono font-hack-nerd-font
```

**Ubuntu/Debian:**

```bash
sudo apt-get install -y git zsh vim neovim tmux curl wget build-essential xsel

# WezTerm
curl -fsSL https://apt.fury.io/wez/gpg.key | sudo gpg --yes --dearmor -o /usr/share/keyrings/wezterm-fury.gpg
echo 'deb [signed-by=/usr/share/keyrings/wezterm-fury.gpg] https://apt.fury.io/wez/ * *' | sudo tee /etc/apt/sources.list.d/wezterm.list
sudo apt-get update
sudo apt-get install -y wezterm

# フォント
sudo apt-get install -y fonts-ubuntu fonts-hack
```

</details>

<details>
<summary>4. Node.jsのインストール</summary>

**macOS:**

```bash
brew install node
```

**Ubuntu/Debian:**

```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs
```

**共通:**

```bash
# npm グローバルディレクトリの設定
mkdir -p ~/.npm-global
npm config set prefix '~/.npm-global'
```

</details>

<details>
<summary>5. Oh My Zshのインストール</summary>

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# zsh-autosuggestions
git clone https://github.com/zsh-users/zsh-autosuggestions ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions

# zsh-syntax-highlighting
git clone https://github.com/zsh-users/zsh-syntax-highlighting ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
```

</details>

<details>
<summary>6. vim-plugのインストール</summary>

```bash
# Vim用
curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim

# Neovim用
curl -fLo ~/.config/nvim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
```

</details>

<details>
<summary>7. シンボリックリンクの作成</summary>

```bash
# Zsh設定
ln -sf ~/dotfiles/.zshrc ~/.zshrc

# Vim設定
ln -sf ~/dotfiles/.vimrc ~/.vimrc

# tmux設定
ln -sf ~/dotfiles/.tmux.conf ~/.tmux.conf

# Git設定
ln -sf ~/dotfiles/.gitconfig ~/.gitconfig
ln -sf ~/dotfiles/.gitignore_global ~/.gitignore_global

# WezTerm設定
ln -sf ~/dotfiles/.wezterm.lua ~/.wezterm.lua

# Neovim設定 (リポジトリ上は .config/nvim に格納)
mkdir -p ~/.config
ln -sf ~/dotfiles/.config/nvim ~/.config/nvim

# Claude Code設定 (CLIの実行時データを巻き込まないよう個別にリンク)
mkdir -p ~/.claude
for e in CLAUDE.md settings.json statusline-command.sh agents archive commands hooks mcp-servers rules skills; do
  ln -sf ~/dotfiles/.claude/$e ~/.claude/$e
done

# Codex設定
mkdir -p ~/.codex
for e in AGENTS.md config.toml hooks; do
  ln -sf ~/dotfiles/.codex/$e ~/.codex/$e
done
# hooks.json はテンプレートから生成 (リポジトリに hooks.json 実体は無い)
sed "s|__HOME__|$HOME|g" ~/dotfiles/.codex/hooks.json.template > ~/.codex/hooks.json
# agents/ と skills/ は Codex がシンボリックリンクのスキャンを無視するためコピーする
# (対象スキルの一覧など詳細は install.sh の setup_codex を参照)
cp -R ~/dotfiles/.codex/agents ~/.codex/agents

# Gemini設定
mkdir -p ~/.gemini
for e in GEMINI.md settings.json; do
  ln -sf ~/dotfiles/.gemini/$e ~/.gemini/$e
done

# Oh My Zsh カスタムテーマ
ln -sf ~/dotfiles/.oh-my-zsh/custom ~/.oh-my-zsh/custom

# tmuxヘルパースクリプト (.tmux.conf の `bind S` が参照)
mkdir -p ~/.tmux
ln -sf ~/dotfiles/tmux_send_to_all_except_nvim.sh ~/.tmux/tmux_send_to_all_except_nvim.sh
```

</details>

<details>
<summary>8. Vimプラグインのインストール</summary>

```bash
vim +PlugInstall +qall
```

</details>

<details>
<summary>9. デフォルトシェルの変更</summary>

```bash
chsh -s $(which zsh)
```

</details>

<details>
<summary>10. ターミナルの再起動とNeovimセットアップ</summary>

```bash
# ターミナルを再起動後
nvim  # lazy.nvimが自動的にプラグインをインストール
```

</details>
</details>

## AI開発ツールのセットアップ

### Claude Code

```bash
# インストール方法は公式ドキュメントを参照
# https://docs.anthropic.com/claude-code
```

### Codex

```bash
npm install -g @openai/codex
```

### Gemini CLI

```bash
npm install -g @google/gemini-cli
```

### GitHub Copilot CLI

npm パッケージ `@github/copilot` として配布されています（単体の `copilot` コマンド）。

```bash
npm install -g @github/copilot
```

### MCP サーバーの登録

`install.sh` は Claude Code に以下の MCP サーバーをユーザースコープで登録します（冪等）。手動で行う場合は `claude mcp add` を使用します。

| サーバー            | 用途                            |
| ------------------- | ------------------------------- |
| `github`            | GitHub Copilot MCP (HTTP)       |
| `context7`          | 最新ライブラリドキュメント取得  |
| `codex`             | Codex 連携                      |
| `serena`            | コードベース解析 (LSP)          |
| `MCP_DOCKER`        | Docker MCP ゲートウェイ         |
| `drawio`            | 図の生成                        |
| `gemini-consultant` | 自作 Gemini 相談用 MCP サーバー |

### 一括更新

```bash
# すべてのAIツールを更新 (Claude Code / Codex / Gemini CLI / Copilot CLI)
./update_ai_tools.sh
```

## 主要設定の説明

### Zsh (.zshrc)

- **テーマ**: `px-rose-pine`（`.oh-my-zsh/custom/themes/` のカスタムテーマ）
- **プラグイン**: `git` / `zsh-autosuggestions` / `zsh-syntax-highlighting` / `z`
- **PATH設定**: Go、npm global、PHP 8.4、pyenv、Rust (cargo)
- **言語環境**: `pyenv`（インストール時のみ初期化）、Rust の cargo bin
- **履歴**: 10万件・`share_history` / 重複除去などの最適化
- **fzf 連携関数**: `cf`（ディレクトリ移動）/ `vf`（プレビュー付きで開く）/ `sshs`（SSH ホスト選択）
- **AI ツールのエイリアス / 関数**:
  - `c` / `cl`: Claude Code、`cx`: Codex、`ge` / `g`: Gemini CLI、`cop`: GitHub Copilot CLI
  - `mc`（補完付き）: `mc explain` / `mc translate` / `mc commit` / `mc push` など Claude を用途別モデルで起動
  - `commit` / `push` / `pull_request` / `translate`: Gemini CLI ベースの Git・翻訳ショートカット
  - `update_ai_tools`: `update_ai_tools.sh` を実行

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
  - vim-lsp + vim-lsp-settings + asyncomplete 系: LSP・補完（対象ファイルを開いて `:LspInstallServer` でサーバー導入。キーマップは Neovim 側 `lsp.lua` と同一: `gd` / `K` / `<leader>ra` / `<leader>ca` / `gr` / `[d` / `]d` など）
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
- **AI 連携**: avante.nvim / copilot.lua
- **カラースキーム**: Rosé Pine（gruvbox / kanagawa / tokyonight / onedark なども同梱）

### tmux (.tmux.conf)

- **プレフィックスキー**: `Ctrl+a` (デフォルトの `Ctrl+b` から変更)
- **256色ターミナル**: RGBカラー対応
- **viモード**: コピーモードでviキーバインド使用
- **マウス操作**: ペイン端のダブルクリックで分割、それ以外はコピーモード
- **テーマ / プラグイン**（tpm で管理）: `rose-pine/tmux`（moon）、`tmux-mode-indicator`、`tmux-sensible`、`tmux-logging`
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
- **フォント**: Ubuntu Mono (Medium, 14pt) — フォールバックに Hiragino Sans
- **カーソル**: BlinkingBlock
- **背景透過**: 90%
- **タブバー**: タブが1つのときは非表示
- **日本語入力**: IME対応
- **キーバインド**: macOS用のバックスラッシュ入力（`option + ¥` → `\`）、`option + Enter` でフルスクリーン切替

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
- **`.codex/`**: Codex 向け指示（`AGENTS.md`）、エージェント定義（`agents/*.toml`）、フック（`hooks/` + `hooks.json`）、スキル（`skills/` — 組込み `.system` と `.claude/skills` から厳選した共有スキル）、`config.toml`
- **`.gemini/`**: Gemini CLI の指示（`GEMINI.md`）と `settings.json`

## ユーティリティスクリプト

### tmux_send_to_all_except_nvim.sh

tmuxの全ペインにコマンドを送信しますが、nvimが実行中のペインは除外します。

```bash
# 使用例
./tmux_send_to_all_except_nvim.sh "git status"
```

### update_ai_tools.sh

全てのAI開発ツールを一括で更新します。

```bash
# 実行
./update_ai_tools.sh

# 対象ツール
# - Claude Code
# - Codex
# - Gemini CLI
# - GitHub Copilot CLI
```

## テスト

`tests/` に pytest ベースのテストスイートがあります。Python フック
(`.claude/hooks/*.py` など) は stdin・外部 API・通知をモックした上で直接実行し、
シェルスクリプト (`install.sh`、各フック、statusline など) はサブプロセスとして
一時 HOME + スタブ PATH 環境で実行します。実通知・実 API コール・実ログへの
書き込みは発生しません。

```bash
# 全テストを実行
python3 -m pytest

# 特定ファイルのみ
python3 -m pytest tests/test_bash_review.py -v
```

Python フック（`.claude/hooks` / `.claude/mcp-servers` / `.codex/hooks`）は
`run_hook` フィクスチャが in-process で `exec` するため、カバレッジを実測できます。
CI（`.github/workflows/ci.yml`）は `pytest-cov` でブランチカバレッジを測定し、
**90% を下回るとジョブが失敗**します（実測は約 95%、除外設定は `.coveragerc`）。
ローカルで測る場合:

```bash
pip install "pytest-cov==7.0.0"
python3 -m pytest \
  --cov=.claude/hooks --cov=.claude/mcp-servers --cov=.codex/hooks \
  --cov-report=term-missing --cov-fail-under=90
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

## トラブルシューティング

### Vimプラグインがインストールされない

```bash
# vim-plugを再インストール
curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim

# Vimでプラグインをインストール
vim +PlugInstall +qall
```

### tmuxで256色が表示されない

`.zshrc`または`.bashrc`に以下を追加：

```bash
export TERM=xterm-256color
```

### フォントが正しく表示されない

Powerlineパッチ済みフォントをインストール：

```bash
brew tap homebrew/cask-fonts
brew install --cask font-hack-nerd-font
```

### Neovimでエラーが発生する

```bash
# lazy.nvimのクリーンインストール
rm -rf ~/.local/share/nvim
rm -rf ~/.config/nvim/lazy-lock.json
nvim  # lazy.nvimが自動的に再インストールされる
```

## 参考リンク

### ドキュメント

- [Vim Documentation](https://www.vim.org/docs.php)
- [Neovim Documentation](https://neovim.io/doc/)
- [tmux Manual](https://man.openbsd.org/tmux.1)
- [Oh My Zsh Wiki](https://github.com/ohmyzsh/ohmyzsh/wiki)
- [WezTerm Documentation](https://wezfurlong.org/wezterm/)

### プラグイン

- [vim-plug](https://github.com/junegunn/vim-plug)
- [lazy.nvim](https://github.com/folke/lazy.nvim)
- [NERDTree](https://github.com/preservim/nerdtree)
- [vim-fugitive](https://github.com/tpope/vim-fugitive)

### AI Tools

- [Claude Code](https://docs.anthropic.com/claude-code)
- [Codex](https://github.com/openai/codex)
- [Gemini API](https://ai.google.dev/)
- [GitHub Copilot CLI](https://github.com/features/copilot)

## ライセンス

個人使用目的のdotfilesです。自由に使用・改変してください。
