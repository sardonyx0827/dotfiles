# セットアップ（手動インストール・復旧・トラブルシューティング）

このドキュメントは [README.md](../README.md) から分離した詳細手順です。推奨の自動インストール（`./install.sh`）は [README のセットアップ](../README.md#セットアップ) を参照してください。

## 目次

- [手動インストール](#手動インストール)
- [元に戻す（バックアップと復旧）](#元に戻すバックアップと復旧)
- [トラブルシューティング](#トラブルシューティング)

## 手動インストール

自動インストールが利用できない場合や、個別にカスタマイズしたい場合は以下の手順を実行してください。

### 前提条件

- [Git](https://git-scm.com/)
- [Homebrew](https://brew.sh/) (macOS) または APT (Ubuntu/Debian)

### 手順

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
<summary>6. vim-plug のインストール（Vim のみ）</summary>

```bash
# Vim 用（Neovim には不要）
curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
    https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
```

> **Neovim は vim-plug を使いません。** プラグインは lazy.nvim 管理で、初回の `nvim`
> 起動時に lazy.nvim が自動でブートストラップし、プラグインを導入します（`install.sh`
> も Neovim には vim-plug を入れません）。

> **`install.sh` は同じ取得元を commit 固定で取ります。** 上の手動コマンドは
> アップストリーム公式の形（`master`）のままなので、両者は異なるバイト列を取得し
> 得ます。`install.sh` 側が固定している理由と pin の更新手順は、スクリプト冒頭の
> `*_REF` 変数のコメントを参照してください。Homebrew / Oh My Zsh の手動手順
> （上の 1. と 5.）も同様です。

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
for e in CLAUDE.md settings.json statusline-command.sh agents commands hooks mcp-servers rules skills; do
  ln -sf ~/dotfiles/.claude/$e ~/.claude/$e
done

# Codex設定 (agents/ と skills/ もディレクトリごとリンク。
#             共有スキルの実体は .codex/skills/ 内の .claude/skills への相対リンク)
mkdir -p ~/.codex
for e in AGENTS.md hooks agents skills; do
  ln -sf ~/dotfiles/.codex/$e ~/.codex/$e
done
# hooks.json はテンプレートから生成 (リポジトリに hooks.json 実体は無い)
sed "s|__HOME__|$HOME|g" ~/dotfiles/.codex/hooks.json.template > ~/.codex/hooks.json
# config.toml も同様にテンプレートから「初回のみ」複製する。symlink にしない
# のは、Codex が実行時にこのファイルへ mcp_servers の Authorization ヘッダ等を
# 書き込むため。リンクするとトークンがリポジトリ側に現れ git add 一回で漏れる。
# 既存の config.toml があれば Codex の書き込み内容を消さないよう手を触れない。
[ -e ~/.codex/config.toml ] || cp ~/dotfiles/.codex/config.toml.template ~/.codex/config.toml

# Gemini設定
mkdir -p ~/.gemini
for e in GEMINI.md settings.json; do
  ln -sf ~/dotfiles/.gemini/$e ~/.gemini/$e
done

# Oh My Zsh カスタムテーマ
ln -sf ~/dotfiles/.oh-my-zsh/custom ~/.oh-my-zsh/custom

# tmuxヘルパースクリプト (.tmux.conf の `bind S` が参照)
mkdir -p ~/.tmux
ln -sf ~/dotfiles/scripts/tmux_send_to_all_except_nvim.sh ~/.tmux/tmux_send_to_all_except_nvim.sh
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

## 元に戻す（バックアップと復旧）

`install.sh` はシンボリックリンクを張る前に、**既存の実ファイルを退避**します（シンボリックリンクは自前の生成物とみなして退避せず削除します）。

**退避先**: `~/.dotfiles_backup_<YYYYMMDD_HHMMSS>/`

実行のたびにタイムスタンプ付きの新しいディレクトリが作られ、**`$HOME` からの相対パス構造がそのまま保たれます**（`backup_if_real` / `install.sh`）。フラットに basename だけで退避すると、`settings.json` のように別ディレクトリで同名のファイルが上書きで消えるためです。

```bash
# 退避ディレクトリの一覧（新しい順）
ls -dt ~/.dotfiles_backup_*

# 中身の確認（例: .zshrc は同じ相対パスに入っている）
tree ~/.dotfiles_backup_20260720_193000
```

### リンクのかかり方（復旧前に把握しておくこと）

`install.sh` のリンク方式は**混在**しているため、戻す前に対象が何なのかを確認してください。

| 方式                           | 例                              | 実体                                                                     |
| ------------------------------ | ------------------------------- | ------------------------------------------------------------------------ |
| ディレクトリごと symlink       | `~/.config/nvim`                | リンク1本                                                                |
| 実ディレクトリ内に個別 symlink | `~/.claude/`、`~/.codex/`       | ディレクトリは実体、中身がリンク（CLI の実行時データを巻き込まないため） |
| 単体ファイルの symlink         | `~/.tmux.conf`、`~/.vimrc` など | リンク1本                                                                |

```bash
# 対象がリンクか実体かを確認する（-> が出ればシンボリックリンク）
ls -ld ~/.config/nvim ~/.claude ~/.claude/settings.json
```

### 個別に元に戻す

```bash
BACKUP=~/.dotfiles_backup_20260720_193000   # 対象の退避ディレクトリ
TARGET=~/.tmux.conf                          # 戻したいパス

# 1. install.sh が張ったリンクなら外す（実体だった場合は退避済みなので何も無い）
[ -L "$TARGET" ] && rm "$TARGET"

# 2. 退避したものを同じ相対パスへ戻す（-a でディレクトリ・属性ごと）
cp -a "$BACKUP/.tmux.conf" "$TARGET"
```

`~/.claude/settings.json` のように**実ディレクトリの中の個別リンク**を戻す場合も同じ手順です（消すのは `~/.claude` ではなくその中のリンク1本）。`~/.config/nvim` のような**ディレクトリごとのリンク**は、リンク1本を外してから `cp -a "$BACKUP/.config/nvim" ~/.config/nvim` で戻します。

### 全体を元に戻す

一括アンインストールのコマンドは用意していません。上記を、退避ディレクトリに入っているエントリぶん繰り返してください。

```bash
# 戻す候補の一覧（$HOME からの相対パスで表示）
cd "$BACKUP" && find . -mindepth 1
```

なお退避ディレクトリに入っているのは**そのとき実ファイルだった分だけ**です。2 回目以降の実行では対象が既に symlink になっており（`backup_if_real` はリンクを退避せず削除する）、退避ディレクトリは空か、ほぼ空になります。

> **⚠️ 退避とリンク作成は原子的ではありません。** `backup_if_real`（退避）と
> `link_entry`（リンク作成）は別ステップなので、その間で `Ctrl-C` やクラッシュが
> 起きると、元のパスには何も無い状態で処理が終わります。**このときファイルは
> 消えていません。上記の退避ディレクトリに入っています。** 中断した場合はまず
> `ls -dt ~/.dotfiles_backup_*` を確認してください。

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
brew install --cask font-hack-nerd-font
```

### Neovimでエラーが発生する

```bash
# lazy.nvimのクリーンインストール
rm -rf ~/.local/share/nvim
rm -rf ~/.config/nvim/lazy-lock.json
nvim  # lazy.nvimが自動的に再インストールされる
```

