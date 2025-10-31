# Dotfiles

個人用の開発環境設定ファイル（dotfiles）のリポジトリです。Vim、Zsh、Tmux、Neovim、WezTermなどの設定と、AI開発ツールのセットアップスクリプトを含んでいます。

## 🛠️ 技術スタック

<!-- Shell & Terminal -->

[zsh-shield]: https://img.shields.io/badge/Zsh-F15A24?style=for-the-badge&logo=zsh&logoColor=white
[zsh-url]: https://www.zsh.org/
[ohmyzsh-shield]: https://img.shields.io/badge/Oh_My_Zsh-1A2C34?style=for-the-badge&logo=ohmyzsh&logoColor=white
[ohmyzsh-url]: https://ohmyz.sh/
[tmux-shield]: https://img.shields.io/badge/tmux-1BB91F?style=for-the-badge&logo=tmux&logoColor=white
[tmux-url]: https://github.com/tmux/tmux
[wezterm-shield]: https://img.shields.io/badge/WezTerm-4E49EE?style=for-the-badge&logo=wezterm&logoColor=white
[wezterm-url]: https://wezfurlong.org/wezterm/

<!-- Editors -->

[vim-shield]: https://img.shields.io/badge/Vim-019733?style=for-the-badge&logo=vim&logoColor=white
[vim-url]: https://www.vim.org/
[neovim-shield]: https://img.shields.io/badge/Neovim-57A143?style=for-the-badge&logo=neovim&logoColor=white
[neovim-url]: https://neovim.io/
[vimplug-shield]: https://img.shields.io/badge/vim--plug-019733?style=for-the-badge&logo=vim&logoColor=white
[vimplug-url]: https://github.com/junegunn/vim-plug
[lazy-shield]: https://img.shields.io/badge/lazy.nvim-57A143?style=for-the-badge&logo=neovim&logoColor=white
[lazy-url]: https://github.com/folke/lazy.nvim
[vscode-shield]: https://img.shields.io/badge/VS_Code-007ACC?style=for-the-badge&logo=visualstudiocode&logoColor=white
[vscode-url]: https://code.visualstudio.com/

<!-- Git -->

[git-shield]: https://img.shields.io/badge/Git-F05032?style=for-the-badge&logo=git&logoColor=white
[git-url]: https://git-scm.com/
[github-shield]: https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github&logoColor=white
[github-url]: https://github.com/

<!-- AI Tools -->

[claude-shield]: https://img.shields.io/badge/Claude_Code-181818?style=for-the-badge&logo=anthropic&logoColor=white
[claude-url]: https://www.anthropic.com/
[codex-shield]: https://img.shields.io/badge/Codex-412991?style=for-the-badge&logo=openai&logoColor=white
[codex-url]: https://openai.com/
[gemini-shield]: https://img.shields.io/badge/Gemini-8E75B2?style=for-the-badge&logo=google&logoColor=white
[gemini-url]: https://ai.google.dev/
[copilot-shield]: https://img.shields.io/badge/GitHub_Copilot-181717?style=for-the-badge&logo=githubcopilot&logoColor=white
[copilot-url]: https://github.com/features/copilot

<!-- Languages -->

[lua-shield]: https://img.shields.io/badge/Lua-2C2D72?style=for-the-badge&logo=lua&logoColor=white
[lua-url]: https://www.lua.org/
[vimlang-shield]: https://img.shields.io/badge/Vimscript-019733?style=for-the-badge&logo=vim&logoColor=white
[vimlang-url]: https://www.vim.org/
[shellscript-shield]: https://img.shields.io/badge/Shell_Script-4EAA25?style=for-the-badge&logo=gnu-bash&logoColor=white
[shellscript-url]: https://www.gnu.org/software/bash/

### シェル & ターミナル

[![Zsh][zsh-shield]][zsh-url]
[![Oh My Zsh][ohmyzsh-shield]][ohmyzsh-url]
[![tmux][tmux-shield]][tmux-url]
[![WezTerm][wezterm-shield]][wezterm-url]

### エディタ

[![Vim][vim-shield]][vim-url]
[![Neovim][neovim-shield]][neovim-url]
[![vim-plug][vimplug-shield]][vimplug-url]
[![lazy.nvim][lazy-shield]][lazy-url]
[![vsCode][vscode-shield]][vscode-url]

### バージョン管理

[![Git][git-shield]][git-url]
[![GitHub][github-shield]][github-url]

### AI開発ツール

[![Claude Code][claude-shield]][claude-url]
[![Codex][codex-shield]][codex-url]
[![Gemini][gemini-shield]][gemini-url]
[![GitHub Copilot][copilot-shield]][copilot-url]

### プログラミング言語

[![Lua][lua-shield]][lua-url]
[![Vimscript][vimlang-shield]][vimlang-url]
[![Shell Script][shellscript-shield]][shellscript-url]

## 📁 ファイル構成

```
.
├── .claude/                        # Claude Code設定
│   ├── agents/                     # カスタムエージェント
│   ├── commands/                   # カスタムコマンド
│   └── hooks/                      # フック設定
├── .codex/                         # Codex設定
├── .config/                        # アプリケーション設定
│   ├── Code/                       # VS Code設定
│   ├── mcphub/                     # MCP Hub設定
│   └── nvim_lazy/                  # Neovim (lazy.nvim) 設定
├── .gemini/                        # Gemini CLI設定
├── .oh-my-zsh/                     # Oh My Zsh設定
│   └── custom/                     # カスタムテーマとプラグイン
├── .gitconfig                      # Git設定
├── .gitignore_global               # グローバルgitignore
├── .tmux.conf                      # tmux設定
├── .vimrc                          # Vim設定
├── .wezterm.lua                    # WezTerm設定
├── .zshrc                          # Zsh設定
├── tmux_send_to_all_except_nvim.sh # tmuxユーティリティ
└── update_ai_tools.sh              # AIツール更新スクリプト
```

## 🚀 セットアップ

### 前提条件

以下のツールがインストールされている必要があります：

- [Homebrew](https://brew.sh/) (macOS)
- [Git](https://git-scm.com/)
- [Zsh](https://www.zsh.org/)
- [Node.js & npm](https://nodejs.org/) (AIツール用)

### インストール手順

1. **リポジトリのクローン**

```bash
git clone https://github.com/yourusername/dotfiles.git ~/dotfiles
cd ~/dotfiles
```

2. **シンボリックリンクの作成**

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

# Neovim設定
mkdir -p ~/.config
ln -sf ~/dotfiles/.config/nvim_lazy ~/.config/nvim

# Claude Code設定
ln -sf ~/dotfiles/.claude ~/.claude

# Codex設定
ln -sf ~/dotfiles/.codex ~/.codex

# Gemini設定
ln -sf ~/dotfiles/.gemini ~/.gemini
```

3. **Oh My Zshのインストール**

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# カスタムテーマのシンボリックリンク
ln -sf ~/dotfiles/.oh-my-zsh/custom ~/.oh-my-zsh/custom
```

4. **必要なツールのインストール**

```bash
# Homebrewパッケージ
brew install vim neovim tmux

# WezTerm
brew install --cask wezterm

# フォント
brew install --cask font-ubuntu-mono

# Node.js グローバルパッケージ用ディレクトリ
mkdir -p ~/.npm-global
npm config set prefix '~/.npm-global'
```

5. **Vimプラグインのインストール**

```bash
# vim-plugのインストール
curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim

# Neovim用
curl -fLo ~/.config/nvim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim

# Vimプラグインのインストール
vim +PlugInstall +qall
```

6. **Neovim (lazy.nvim) のセットアップ**

```bash
# Neovimを開いてlazy.nvimが自動インストールされるのを待つ
nvim
```

7. **シェルの再読み込み**

```bash
source ~/.zshrc
```

## 🤖 AI開発ツールのセットアップ

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

```bash
npm install -g @github/copilot
```

### 一括更新

```bash
# すべてのAIツールを更新
./update_ai_tools.sh
```

## ⚙️ 主要設定の説明

### Zsh (.zshrc)

- **テーマ**: Rose Pine (`px-rose-pine`)
- **プラグイン**: Oh My Zsh プラグインシステム
- **PATH設定**: Go、PHP、Node.js グローバルパッケージ
- **エイリアス**: 便利なコマンドショートカット

```bash
# 主要なPATH設定
export PATH=~/go/bin:$PATH                      # Go
export PATH=~/.npm-global/bin:$PATH             # npm global
export PATH="/opt/homebrew/opt/php@8.4/bin:$PATH"  # PHP
```

### Vim (.vimrc)

- **プラグインマネージャー**: vim-plug
- **テーマ**: rosepine
- **主要プラグイン**:
  - NERDTree: ファイルエクスプローラー
  - vim-fugitive: Git統合
  - vim-airline: ステータスライン
  - fzf: ファジーファインダー
- **対応言語**: C, Elixir, Go, Haskell, HTML, JavaScript, Lisp, Lua, Perl, Python, Ruby, TypeScript

### Neovim (.config/nvim_lazy/)

- **プラグインマネージャー**: lazy.nvim
- **テーマ**: rosepine
- **モダンなNeovim設定**: Luaベース
- **LSP対応**: 言語サーバープロトコル統合

### tmux (.tmux.conf)

- **プレフィックスキー**: `Ctrl+a` (デフォルトの `Ctrl+b` から変更)
- **256色ターミナル**: RGBカラー対応
- **viモード**: コピーモードでviキーバインド使用
- **クリップボード統合**:
  - macOS: pbcopy/pbpaste
  - Linux: xsel

#### 主要なキーバインド

```
# ペイン操作
Ctrl+a h/j/k/l  # ペイン間移動 (Vim風)
Ctrl+a H/J/K/L  # ペイン入れ替え
Ctrl+a -        # 横分割
Ctrl+a |        # 縦分割

# ウィンドウ操作
Ctrl+a c        # 新規ウィンドウ
Ctrl+a n/p      # 次/前のウィンドウ

# コピーモード
Ctrl+a [        # コピーモード開始
v               # 選択開始
y               # ヤンク（コピー）
Ctrl+a ]        # ペースト
```

### WezTerm (.wezterm.lua)

- **フォント**: Ubuntu Mono (Medium, 14pt)
- **カーソル**: BlinkingBlock
- **背景透過**: 80%
- **日本語入力**: IME対応
- **キーバインド**: macOS用のバックスラッシュ入力設定

### Git (.gitconfig)

- **エディタ**: neovim
- **マージツール**: nvimdiff
- **カスタムエイリアス**:
  - `git ls`: グラフ付きログ（簡潔版）
  - `git ll`: グラフ付きログ（詳細版）
  - `git la`: グラフ付きログ（完全版）
- **GitHub CLI統合**: 認証情報ヘルパー

## 🔧 ユーティリティスクリプト

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

## 🎨 カラーテーマ

このdotfilesは **Rose Pine** カラーテーマをベースにしています。

- **Zsh**: px-rose-pine テーマ
- **Vim**: rosepine テーマ
- **Neovim**: rosepine テーマ
- **tmux**: カスタムカラー設定（Rose Pine風）

## 📝 カスタマイズ

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

## 🐛 トラブルシューティング

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

## 📚 参考リンク

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
- [GitHub Copilot](https://github.com/features/copilot)
- [Gemini API](https://ai.google.dev/)

## 📄 ライセンス

このdotfilesは個人使用を目的としています。自由に使用・改変してください。

## 🤝 コントリビューション

改善提案やバグ報告は Issue または Pull Request でお願いします。

---

**作成者**: [sardonyx0827](https://github.com/sardonyx0827)
