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

Homebrewは `/opt/homebrew` にインストールされます。インストールスクリプトは自動的にPATHを設定します。

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

### 前提条件

```bash
# システムのアップデート
sudo apt-get update
sudo apt-get upgrade -y
```

### プラットフォーム固有の設定

#### クリップボード統合

tmuxでのクリップボード操作には `xsel` を使用します（インストールスクリプトで自動インストール）。

Waylandを使用している場合は `wl-clipboard` も必要になる場合があります：

```bash
sudo apt-get install -y wl-clipboard
```

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

Ubuntu 20.04などでNeovimのバージョンが古い場合は、PPAを使用：

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

1. **Vim**: [https://www.vim.org/download.php](https://www.vim.org/download.php)
2. **Neovim**: [https://neovim.io/](https://neovim.io/)
3. **tmux**: Windows Terminalの使用を推奨（tmuxの代替）
4. **WezTerm**: [https://wezfurlong.org/wezterm/](https://wezfurlong.org/wezterm/)

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
# lazy.nvimのクリーンインストール
rm -rf ~/.local/share/nvim
rm -rf ~/.config/nvim/lazy-lock.json
nvim  # 再度開いて自動インストール
```

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
```

### インストール済みツールの更新

```bash
# macOS
brew update && brew upgrade

# Ubuntu/Debian
sudo apt-get update && sudo apt-get upgrade

# AIツールの更新
./update_ai_tools.sh
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
