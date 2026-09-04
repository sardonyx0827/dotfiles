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

tmux のコピー (`y` / `Enter`) は `~/.local/bin/pbcopy`（= `scripts/pbcopy`）を通ります。
Wayland セッションなら `wl-copy`、X11 セッションなら `xsel` を選ぶので、使っている
セッション側のパッケージが要ります（`xsel` はインストールスクリプトが自動で入れます）。

```bash
sudo apt-get install -y wl-clipboard   # Wayland セッションの場合
```

貼り付け (`Ctrl+a ]`) だけは `xsel` 固定です。

#### Android の Linux ターミナル (AVF) / X も Wayland も無い SSH 先

`install.sh` は macOS 以外で `scripts/pbcopy` を `~/.local/bin/pbcopy` へリンクします。
このスクリプトは「その環境で本当に届く出口」を上から順に選ぶディスパッチャです。

1. **Wayland** (`wl-copy`)
2. **X11** (`xsel`)
3. **OSC 52**（端末エスケープ。X も Wayland も無い SSH 先向け）

macOS でリンクしないのは、`.zshrc` の `export PATH="$HOME/.local/bin:$PATH"` が
`~/.local/bin` を無条件で `/usr/bin` より前に置くため、純正の `/usr/bin/pbcopy` を
黙って覆い隠してしまうからです（`~/.local/bin/env` の読み込みは条件付きなので、
順序を決めているのはこの export の方です）。

```bash
echo hello | pbcopy
```

`.tmux.conf` のコピー系バインド（`y` / `Enter`）も OS で分岐せずこの `pbcopy` を
通すので、矩形コピー（`C-v` で矩形トグル → `y`）もそのまま同じ経路に乗ります。

##### Android 側のクリップボードへ渡る仕組み

Pixel などの Android 端末上で動く Linux ターミナル (AVF の Debian VM) では、
**Wayland のクリップボードだけ**が Android 本体と繋がっています。ゲストエージェント
`/usr/bin/linux_vm_manager` が橋渡しをしていて、`readClipboard`（ゲスト → Android）で

```
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 wl-paste --no-newline
```

を、逆方向の `updateClipboard`（Android → ゲスト）で `wl-copy` を呼びます。したがって:

- `xsel` が書く X のクリップボードは、Xwayland ↔ weston の同期で結果的に追随する
  だけで、VM の中で閉じた経路です
- **OSC 52 はこの環境では届きません**。端末を配っているのは `ttyd` で、その WebView
  は 52 番の OSC ハンドラを一つも登録していません（`registerOscHandler` に 52 が無い）
- ttyd から起動したシェルには `DISPLAY=:0` だけが入っていて `WAYLAND_DISPLAY` は
  空なので、`pbcopy` はソケット (`$XDG_RUNTIME_DIR/wayland-0`) を見て既定名を補います

動作確認:

```bash
printf 'clipboard-check' | pbcopy   # tmux なら矩形選択して y
# → Android 側のアプリで長押し → 貼り付け
```

Android 側でコピーした文字列が VM に降りてくるか（`updateClipboard` 方向）は
`wl-paste` で確認できます。

##### 端末アプリが橋渡ししてくれない場合の逃げ道（共有ストレージ経由）

上の仕組みは「Android アプリが `readClipboard` を呼びに来る」ことが前提です。
呼びに来ないアプリ／バージョンでは、`wl-copy` まで届いていても本体のクリップ
ボードには入りません（ゲスト側から通知する口は無く、AIDL のメソッドはどちらも
Android から呼ぶ側です）。

その場合は共有ストレージを使います。`/mnt/shared` に Android の
`/storage/emulated/0` が virtiofs で見えていて、VM から書き込めます。

- コピーモードで **`Y`**（`y` の代わり）を押すと、`~/.tmux/clip_to_android.sh`
  が選択範囲を `/mnt/shared/Download/tmux-clip/` に書き出します（`latest.txt`
  と `index.html`）。ついでに `pbcopy` も呼ぶので VM 側のクリップボードにも入ります
- Android のブラウザで次を開き、ブックマークしておきます

```
file:///storage/emulated/0/Download/tmux-clip/index.html
```

- ページは 3 秒ごとに読み直すので、`Y` を押してブラウザに切り替えれば最新の
  内容が出ています。「コピー」を 1 タップで Android のクリップボードへ入ります
  （`file://` はセキュアコンテキストではないため `navigator.clipboard` は使えず、
  `execCommand('copy')` に落ちます。それも塞がれている場合はテキストを長押し →
  全選択 → コピー）

`y` と `Y` を分けているのは、`Y` では選択範囲が **Android のストレージに平文で
残る**からです（ストレージ権限を持つアプリから読めます）。途中経過は VM 内の
`/tmp`（tmpfs, 0600）も通りますが、こちらは終了時に消えます。パスワードのような
ものを流してしまったら消してください:

```bash
~/.tmux/clip_to_android.sh --clear
```

常に共有したい場合は `.tmux.conf` の `y` のバインドをこのスクリプトに差し替え
れば済みます（スクリプトの中で `pbcopy` も呼んでいます）。書き出し先は
`CLIP_TO_ANDROID_DIR` で変更できます。

##### OSC 52 に落ちる環境（素の SSH 先など）

tmux 内から OSC 52 を通すために `.tmux.conf` で `allow-passthrough on` を設定しています
（tmux 3.3 以降の既定は off で、未設定だと tmux 内でのみ終了ステータス 0 のまま
無言で失敗します）。ペインの中から送るときはエスケープを DCS パススルーで包み、
その内側の ESC は二重にする必要があります（`\033Ptmux;\033\033]52;...`）。

> **`allow-passthrough on` が開くもの**: この設定はサーバー全体 (`set -g`) に効き、
> `pbcopy` だけを通すわけではありません。ペインに出力を出せるプログラムはどれも
> エスケープを外側の端末へ素通しできるようになります。実害として現実的なのは
> **クリップボードの上書き**で、信用できないファイルを `cat` した、ログを `less` で
> 開いた、といった経路で OSC 52 が紛れ込むと、利用者のクリップボードが黙って
> 差し替わります（「便利そうなコマンドをコピペしたら別物が走る」型の攻撃）。
> 個人用の 1 人 tmux では許容できる取引ですが、共用サーバーでは見直してください。
> なお `all` ではなく `on` なので、背景ペインからの素通しは対象外です。

> **注意**: OSC 52 に落ちたときだけ `pbcopy` は端末 (`/dev/tty`) を必要とします。
> tmux の `copy-pipe` から呼ばれた場合は tmux サーバの子で制御端末を持たないため、
> クライアントの端末 (`#{client_tty}`) を tmux に問い合わせて直接書きます。それも
> 無い呼び出し（cron、フック、pty を割り当てない非対話リモート実行）では、届く
> 経路が無いことを終了ステータス 1 で返します。Wayland / X11 が使える環境では
> この制約はありません。

#### 端末内での日本語入力（uim-fep）

X/Wayland の入力メソッドが使えない環境では、端末とシェルの間に **uim-fep** を
挟んで日本語入力を行います。`.zshrc` が対話シェルの起動時に自動で
`exec uim-fep -e /usr/bin/zsh` へ置き換えるので、端末を開けばそのまま使えます。
変換エンジンとキー割り当ては `~/.uim`（既定 mozc、`Alt+j` でトグル）が決めます。

必要なパッケージ:

```bash
apt-get install -y uim uim-fep uim-mozc mozc-server
```

`-e` で zsh を明示しているのは、uim-fep の既定の子プロセスが `$SHELL` で、この
環境の `$SHELL` が `/bin/bash` のためです（`.tmux.conf` の `default-command` が
`${SHELL}` を使えないのと同じ事情）。

起動条件は次のすべてを満たしたときだけです。1つでも欠ければ `.zshrc` は素通り
します。

| 条件                     | 目的                                                             |
| ------------------------ | ---------------------------------------------------------------- |
| `UIM_FEP_PID` が未設定   | uim-fep が子シェルへ渡す変数。無視すると無限に自分自身を起動する |
| `NO_UIM_FEP` が未設定    | 利用者側の脱出口（下記）                                         |
| 標準入出力が端末         | パイプ越しに呼ばれるツールのシェルを乗っ取らせない               |
| `TERM` が `dumb` でない  | エディタが開くシェルなど                                         |
| `uim-fep` が PATH にある | 導入していない機械（macOS など）では何もしない                   |

**一時的に無効化する**には、追跡ファイルを編集せず環境変数で外します。

```bash
NO_UIM_FEP=1 zsh
```

> **確認しておくこと**: `Alt+j` のトグルが効くかどうかは、端末が Meta 修飾を
> どう送るかに依存します。`ESC` プレフィックスとして送る端末では `~/.uim` の
> `<Alt>j` が発火しないことがあります。効かない場合は `~/.uim` の
> `generic-on-key?` / `generic-off-key?` を別のキー（例: `"<Control>j"`）に
> 変更してください。

> **tmux での見え方**: `.zshrc` から起動するため、FEP はペインごとに 1 つずつ
> 立ち上がります（入力状態はペイン間で独立）。既定のステータス行
> (`-s lastline`) はペインの最下行を 1 行占有するので、邪魔なら
> `-s none` または `-s backtick` を指定してください。tmux の外だけで使いたい
> 場合は、起動条件に `[[ -z "$TMUX" ]]` を足します。

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

`.config/nvim` は Neovim 0.11+ の API（`vim.lsp.config` / `vim.hl` など）を前提に
しているため、ディストリ配布版の Neovim では初回起動時にエラーになります。
Debian 13 (trixie) は 0.10.4、Debian 12 (bookworm) は 0.7.2 と、いずれも要件を
満たしません（Android の Linux ターミナル (AVF) はこの Debian 上に構築されます）。

そのため `install.sh` は **APT の `neovim` を入れません**。代わりに公式リリースの
tarball を sha256 検証したうえで `~/.local/nvim` へ展開し、`~/.local/bin/nvim`
から張ったシンボリックリンクで参照します（`~/.local/bin` は `.zshrc` が
`/usr/bin` より前に置くため、古い APT 版が残っていても新しい方が優先されます）。
バージョンを上げるときは `install.sh` 冒頭の `NEOVIM_VERSION` と
`NEOVIM_SHA256_*` を**セットで**更新してください（片方だけだと照合に失敗し、
期待値と実際のハッシュを表示したうえで、何もインストールせずに終了します）。

```bash
# 実際に使われている Neovim と、その導入元を確認する
which -a nvim
nvim --version | head -1
ls -l ~/.local/bin/nvim          # -> ~/.local/nvim/bin/nvim

# 入れ直す（install.sh は同じバージョンなら再ダウンロードしません）
rm -rf ~/.local/nvim ~/.local/bin/nvim
./install.sh

# APT 版が残っていて紛らわしい場合は削除してよい
sudo apt-get remove -y neovim
```

すでに 0.11 以上の Neovim（Homebrew、自前ビルドなど）が PATH 上にある場合、
`install.sh` はそれを尊重して何もしません。

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
