# プラットフォーム別インストール詳細ガイド

このドキュメントでは、各プラットフォーム特有のインストール手順と注意事項を説明します。

## macOS

### 前提条件

- macOS 11.0 (Big Sur) 以降
- Xcode Command Line Tools

### Xcode Command Line Toolsのインストール

```bash
xcode-select --install
```

### プラットフォーム固有の設定

#### Apple Silicon (M1/M2/M3) の場合

Homebrewは `/opt/homebrew` にインストールされます。インストールスクリプト自身が行うPATH設定（`brew shellenv`）はスクリプトの実行中だけ有効です。恒久的なPATH設定は、リンクされる `.zshrc` が `brew shellenv` を実行することで行われます（端末を開き直すと反映）。

#### Intel Mac の場合

Homebrewは `/usr/local` にインストールされます。

### トラブルシューティング

#### Homebrewのインストールに失敗する

```bash
# Homebrewの公式サイトから最新のインストールコマンドを確認
open https://brew.sh
```

#### WezTermが起動しない

```bash
# Rosetta 2が必要な場合（Apple Siliconのみ）
softwareupdate --install-rosetta
```

#### tmuxでクリップボードが動作しない

tmux設定（`.tmux.conf`）で `pbcopy` と `pbpaste` を使用しています。これらはmacOSに標準で含まれています。

---

## Ubuntu/Debian

### 対応バージョン

- Ubuntu 20.04 LTS 以降
- Debian 11 (Bullseye) 以降

> **Neovim は 0.12 以降が必要です（CI は v0.12.4 で検証）。** `install.sh` は `apt` から `neovim` を入れますが、
> 現行の Ubuntu LTS（24.04 を含む）の `apt` 版は古く、この設定の要件を満たしません。
> 後述の「[Neovimのバージョンが古い](#neovimのバージョンが古い)」の手順で新しい版を入れてください。

### 前提条件

```bash
# システムのアップデート
sudo apt-get update
sudo apt-get upgrade -y
```

### プラットフォーム固有の設定

#### クリップボード統合

tmuxでのクリップボード操作には `xsel` を使用します（インストールスクリプトで自動インストール）。

この設定のクリップボード連携（`.tmux.conf`）は `xsel` のみを呼び出します。`wl-copy` / `wl-paste`（`wl-clipboard`）は使っていません。

#### フォントの追加設定

```bash
# フォントキャッシュの更新
fc-cache -fv
```

### トラブルシューティング

#### WezTermのリポジトリが追加できない

GPGキーの問題が発生した場合：

```bash
# 古いキーを削除
sudo rm /usr/share/keyrings/wezterm-fury.gpg

# 再度インストールスクリプトを実行
./install.sh
```

#### Neovimのバージョンが古い

この設定は Neovim 0.12 以降を前提にしています（`vim.keymap.set` の `buf` オプションなど 0.12 の API に移行済み。`vim.lsp.config` は 0.11+ の機能。CI は v0.12.4 で検証）。Ubuntu 20.04 に限らず、24.04 を含む現行の Ubuntu LTS の `apt` 版 `neovim` は古く、この要件を満たしません。`nvim --version` で確認し、PPA（または公式リリース）を使用：

```bash
sudo add-apt-repository ppa:neovim-ppa/unstable
sudo apt-get update
sudo apt-get install -y neovim
```

#### tmuxでマウス操作が動作しない

tmux 2.1以降が必要です。バージョン確認：

```bash
tmux -V
```

---

## Windows

### 対応環境

1. **WSL2（推奨）**
2. Git Bash

### WSL2でのインストール（推奨）

#### 1. WSL2のセットアップ

```powershell
# PowerShellを管理者権限で実行
wsl --install
```

#### 2. Ubuntuのインストール

```powershell
wsl --install -d Ubuntu
```

#### 3. Ubuntuを起動してインストール

```bash
# WSL内で実行
git clone https://github.com/sardonyx0827/dotfiles.git
cd dotfiles
./install.sh
```

### Git Bashでのインストール

#### 前提条件

- [Git for Windows](https://git-scm.com/download/win)
- [Node.js for Windows](https://nodejs.org/)

#### 制限事項

Git Bash環境では以下の機能に制限があります：

- パッケージマネージャー（Homebrew/APT）が使用できない
- 一部のツールは手動インストールが必要
- シンボリックリンクの作成に管理者権限が必要な場合がある

#### 手動インストールが必要なツール

Git Bash では `install.sh` は OS パッケージ（Homebrew / APT 相当）の導入を行わず、警告を出すだけです。次のものは自分で用意してください：

1. **Vim**: [https://www.vim.org/download.php](https://www.vim.org/download.php)
2. **Neovim**: [https://neovim.io/](https://neovim.io/)
3. **tmux**: Windows Terminalの使用を推奨（tmuxの代替）
4. **WezTerm**: [https://wezfurlong.org/wezterm/](https://wezfurlong.org/wezterm/)
5. **zsh**: 未導入の場合、デフォルトシェルの変更（`chsh`）は警告してスキップされます。Oh My Zsh も zsh を前提とします
6. **フォント**: `install.sh` が導入するのは macOS / Ubuntu のみです
7. **CLI ツール**（fzf、jq、ripgrep、bat、fd、universal-ctags）
8. **GitHub CLI（gh）**: Windows 向けの導入経路はありません。既に入っている場合のみ、git の資格情報ヘルパーとして使われます
9. **glow / Docker / lazydocker / pyenv**: 導入は macOS / Ubuntu のみです（pyenv は macOS では Homebrew 経由）
10. **Node.js / Python**: Git Bash では自動導入されません（無い場合は npm / pip 製のツールも導入されません）
11. **Linter / Formatter**: prettier・eslint・ruff など npm / pip 製のものは Node.js / Python があれば導入されます。それ以外は、scoop があれば shellcheck / shfmt / cppcheck のみ自動導入され、残り（clang-format、staticcheck など）は手動です

### Windows Terminalの設定

WSL2を使用する場合、Windows Terminalを推奨します：

```powershell
# Microsoft Storeからインストール
winget install Microsoft.WindowsTerminal
```

### トラブルシューティング

#### シンボリックリンクの作成に失敗

管理者権限でGit Bashを実行：

```bash
# 開発者モードを有効にする（Windows 10/11）
# 設定 → 更新とセキュリティ → 開発者向け → 開発者モード
```

#### リポジトリ内のシンボリックリンクがテキストファイルになる

開発者モード（OS 権限）と `core.symlinks`（Git の設定）は別物で、前者だけ有効でも
後者が無効だと clone 時にシンボリックリンクがリンク先パスの入ったテキストファイル
として展開される。Git for Windows はインストーラのチェックボックス次第で
`core.symlinks=false` になっているため、有効化する：

```bash
git config --global core.symlinks true
# 設定済みのクローンには遡って効かないので、クローンし直す
```

**フック（`.codex/hooks/`）はこの設定に依存しない。** 共有ロジックの実体は
`.claude/hooks/` 側の 1 本だけで、Codex 側は複製もリンクも持たず、自分の物理パス
から `../../.claude/hooks` を解決して直接読む。`core.symlinks=false` でも壊れない
（`pytest tests/test_hook_sync.py` が追跡 symlink ゼロを含めて固定している）。

一方 `.codex/skills/` は `.claude/skills/` 配下の各スキルへのシンボリックリンク
として追跡している。これがテキスト化すると **Codex が該当スキルを認識しなくなる**
（フックのように実行時エラーにはならず、静かに欠落する）。スキルを使うなら上記の
`core.symlinks=true` を有効にしてクローンし直すこと。

#### 文字コードの問題

UTF-8を有効にする：

```bash
# .bashrcまたは.bash_profileに追加
export LANG=ja_JP.UTF-8
export LC_ALL=ja_JP.UTF-8
```

#### tmuxが使用できない

Windows環境ではtmuxの代わりにWindows Terminalのタブ機能を使用することを推奨します。

---

## 共通トラブルシューティング

### install.sh が終了コード 1 で終わった

個々のインストール手順が失敗しても `install.sh` は警告を出して続行し、最後に失敗したステップの一覧を表示して終了コード `1` で終わります（`Installation completed!` は表示されません）。

```text
[ERROR] Installation finished with 2 failed step(s):
  - uv
  - zsh-autosuggestions
[INFO] Reasons are printed above as [WARNING]. Fix the cause and re-run ./install.sh (finished steps are skipped).
```

1. 一覧の各項目について、ログの上の方にある `[WARNING] Failed to install <名前>` などの行と、その直後に字下げして出るインストーラ自身のエラー出力で原因を確認する
2. 原因（ネットワーク、`sudo` の権限、パッケージ名の変更など）を直す
3. `./install.sh` を再実行する。導入済みのステップはスキップされるので、何度実行しても安全

dotfiles のシンボリックリンクは最初のステップで張り終えているため、一覧に出るのはその後のパッケージ・ツール・プラグイン関連の失敗です。`./install.sh --help` で終了コードの意味（`0` 成功 / `1` 失敗したステップあり / `2` 引数の誤り）を確認できます。

### Zshプラグインが動作しない

```bash
# プラグインの再インストール
rm -rf ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
rm -rf ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting

git clone https://github.com/zsh-users/zsh-autosuggestions ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
git clone https://github.com/zsh-users/zsh-syntax-highlighting ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
```

### Neovimプラグインのインストールに失敗

```bash
# lazy.nvimのクリーンインストール（プラグインに加え、Mason で入れた LSP / DAP も消える）
rm -rf ~/.local/share/nvim
nvim  # 再度開いて自動インストール
```

`~/.config/nvim` はリポジトリの `.config/nvim` へのシンボリックリンクで、`lazy-lock.json`
（プラグインのバージョン固定）は**リポジトリで追跡されているファイル**です。消すとピン留めが失われるので
削除しないでください。lockfile に合わせて入れ直すには、Neovim 内で `:Lazy restore` を実行します。
Mason で入れていた LSP / DAP は `:Mason` から入れ直します。
lockfile を書き換えてしまった場合は `git -C ~/dotfiles checkout -- .config/nvim/lazy-lock.json` で戻せます。

### Vimプラグインのインストールに失敗

```bash
# vim-plugの再インストール
rm ~/.vim/autoload/plug.vim
curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim

vim +PlugInstall +qall
```

### Node.js npmパッケージのインストールに失敗

```bash
# npmキャッシュのクリア
npm cache clean --force

# グローバルディレクトリの再設定
mkdir -p ~/.npm-global
npm config set prefix '~/.npm-global'

# PATHの確認
echo $PATH | grep .npm-global
```

### フォントが正しく表示されない

```bash
# macOS
brew install --cask font-hack-nerd-font

# Ubuntu/Debian
sudo apt-get install -y fonts-hack

# フォントキャッシュの更新（Linux）
fc-cache -fv
```

### 権限エラーが発生する

```bash
# dotfilesディレクトリの所有権を確認
ls -la ~/dotfiles

# 必要に応じて所有権を変更
sudo chown -R $USER:$USER ~/dotfiles
```

---

## インストール検証

インストールが正しく完了したか確認する手順：

```bash
# 1. シェルの確認
echo $SHELL  # /bin/zsh または /usr/bin/zsh が表示されるべき

# 2. 各ツールのバージョン確認
vim --version | head -1
nvim --version | head -1
tmux -V
node --version
npm --version

# 3. シンボリックリンクの確認
ls -la ~/.zshrc
ls -la ~/.vimrc
ls -la ~/.tmux.conf
ls -la ~/.config/nvim

# 4. Zshテーマの確認
echo $ZSH_THEME  # px-rose-pine が表示されるべき

# 5. Zshプラグインの確認
ls ~/.oh-my-zsh/custom/plugins/
```

---

## アップデート

### dotfilesの更新

```bash
cd ~/dotfiles
git pull origin main

# 設定の再適用（冪等）
./install.sh
```

`git pull` だけでは反映されないものがあります。`~/.codex/hooks.json` は `install.sh` が
テンプレートから生成したコピー（シンボリックリンクではない）で、また `~/.claude/mods` のように
新しく追加されたリンクは `install.sh` が張るまで存在しません。pull の後は `./install.sh` を
再実行してください（`./install.sh --dry-run` で事前に確認できます）。

### インストール済みツールの更新

```bash
# macOS
brew update && brew upgrade

# Ubuntu/Debian
sudo apt-get update && sudo apt-get upgrade

# AIツールの更新
./scripts/update_ai_tools.sh
```

---

## サポート

問題が解決しない場合：

1. [Issues](https://github.com/sardonyx0827/dotfiles/issues)で検索
2. 新しいIssueを作成（以下の情報を含める）：
   - OS/ディストリビューションとバージョン
   - エラーメッセージの全文
   - 実行したコマンド
   - 関連するログファイル
