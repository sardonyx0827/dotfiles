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

- Ubuntu 22.04 LTS 以降
- Debian 12 (Bookworm) 以降

> **Debian 11 (Bullseye) は対象外です。** 標準の `python3` が 3.9 で、`gemini-consultant`（MCP サーバー）が使う Python の `mcp` パッケージ（`Requires-Python >=3.10`）を入れられません。
> そのため `install.sh` の `gemini-consultant` の手順が毎回失敗します。Debian 11 で使うなら、先に 3.10 以降の Python（`pyenv install 3.12` など）を用意してください。

> **`scripts/gen_codex_agents.py` には Python 3.11 以降が必要です。** 標準ライブラリの `tomllib`（3.11 で追加）を使うためです。
> 標準の `python3` は Ubuntu 22.04 が 3.10 で足りません（Ubuntu 24.04 と Debian 12 は満たします）。
> `install.sh` 自体はこのスクリプトを実行しないので、Ubuntu 22.04 でもインストールは通ります。
> 必要になるのは、`.claude/agents/` を編集して `.codex/agents/` を作り直すとき（CI の `--check` と同じ処理）だけです。
> その場合は `pyenv install 3.12` などで 3.11 以降を用意し、その Python で実行してください。

> **Neovim は 0.12 以降が必要です（CI は v0.12.4 で検証）。** `install.sh` は `apt` から `neovim` を入れますが、
> 現行の Ubuntu LTS（24.04 を含む）の `apt` 版は古く、この設定の要件を満たしません。
> 後述の「[Neovimのバージョンが古い](#neovimのバージョンが古い)」の手順で新しい版を入れてください。
> `install.sh` は `apt` の手順の最後に `nvim --version` を確認し、0.12 未満（または読み取れない・`nvim` が無い）場合は
> `[WARNING]` でこの節を案内します。`apt` では新しい版を入れられないため、失敗としては数えず（終了コードは 1 になりません）、そのまま続行します。

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

この設定は Neovim 0.12 以降を前提にしています（`vim.keymap.set` の `buf` オプションなど 0.12 の API に移行済み。`vim.lsp.config` は 0.11+ の機能。CI は v0.12.4 で検証）。22.04・24.04 を含む現行の Ubuntu LTS の `apt` 版 `neovim` は古く、この要件を満たしません。`nvim --version` で確認し、PPA（または公式リリース）を使用：

```bash
sudo add-apt-repository ppa:neovim-ppa/unstable
sudo apt-get update
sudo apt-get install -y neovim
```

#### fzf のキーバインド（Ctrl-T / Ctrl-R / Alt-C）が効かない

`fzf --zsh` は fzf 0.48 以降のオプションで、それ以前の `apt` 版（Ubuntu 22.04 / 24.04 など）には存在しません。
`.zshrc` は `fzf --zsh` が成功したときだけその出力を読み込み、失敗した場合は `apt` 版が同梱する
`/usr/share/doc/fzf/examples/key-bindings.zsh` と `completion.zsh`（無ければ `/usr/share/fzf/` 配下）を読み込むので、
通常は何もしなくてもキーバインドが使えます。それでも効かない場合は `fzf --version` と、上のファイルが存在するかを確認してください。

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

## Docker

`install.sh` は Docker を導入し、最後に `docker info` でデーモンに届くかを確かめます。
届かなくても警告を出すだけで、失敗したステップには数えません（終了コードは 1 になりません）。
導入直後はデーモンにまだ届かないのが普通だからです。Docker Desktop をまだ起動していないことや、
`docker` グループに入る前からのログインセッションが理由になります。

### macOS（Docker Desktop）

- Homebrew Cask の `docker-desktop` で導入します（旧名 `docker` の Cask も導入済みとして扱います）。
  Cask を使わず `/Applications/Docker.app` に置いた場合も導入済みとみなし、入れ直しません。
- 判定は Cask / アプリで行い、`docker` CLI の有無は見ません。CLI は Docker Desktop を**一度起動するまで PATH に載らない**ためです。
  CLI が見つからない間は、実行のたびに「一度起動する」よう案内します。

  ```bash
  open -a Docker   # 初回起動で docker CLI が PATH に入り、エンジンが立ち上がる
  docker info      # 応答すれば準備完了
  ```

- Docker Desktop が無く `docker` CLI だけがある場合は、別のランタイム（OrbStack / Colima など）が入っているとみなします。
  そのときは Docker Desktop を入れません。

### Ubuntu / Debian（Docker Engine）

- Docker 公式の**署名付き apt リポジトリ**から `docker-ce`、`docker-ce-cli`、`containerd.io`、`docker-buildx-plugin`、
  `docker-compose-plugin` を入れます。`get.docker.com` のスクリプトを `sudo sh` で実行することはしません。
- 鍵は `/etc/apt/keyrings/docker.asc` に置き、ソースは `/etc/apt/sources.list.d/docker.list` に書きます。
  鍵の主鍵の指紋が `install.sh` 冒頭の `DOCKER_APT_KEY_FPR`（`9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88`）と一致しないとき、
  `install.sh` は鍵を消し、リポジトリを足さず、失敗として記録します。
  照合するのは root が書いた鍵ファイルそのものです。
- 配布元は `/etc/os-release` で選びます。Ubuntu は `linux/ubuntu`、Debian は `linux/debian`、
  Mint などの派生は `ID_LIKE` と `UBUNTU_CODENAME` で選びます。判定できないときは導入しません。
- エンジンが入ったら、`id -un` で得たユーザーを `docker` グループに加えます（`$USER` は使いません）。
  再実行時に加わっていなければ、もう一度加えます。グループ自体が無い環境では何もしません（rootless Docker や、WSL の Docker Desktop 連携など）。
  反映にはログアウトとログインのし直し、またはそのシェルでの `newgrp docker` が必要です。

  ```bash
  newgrp docker    # または一度ログアウトしてログインし直す
  docker info      # 応答しなければ: sudo systemctl start docker
  ```

Node.js も同じ方式です。NodeSource の署名付きリポジトリ（`/etc/apt/keyrings/nodesource.gpg`、`node_<NODESOURCE_NODE_MAJOR>.x`）から入れます。
主鍵の指紋は `NODESOURCE_APT_KEY_FPR` と照合します。
NodeSource は鍵の指紋を公開していないので、この値は固定した時点で配布されていた鍵のものです。
鍵が差し替わったときは、黙って信用せずに照合で止まります。

### hadolint（Dockerfile の linter）

- macOS は `brew install hadolint` で入れます。
- Ubuntu / Debian は GitHub のリリースバイナリ（`HADOLINT_VERSION`）を一時ディレクトリへ取得し、
  `HADOLINT_SHA256_X86_64` / `HADOLINT_SHA256_ARM64` と sha256 が一致したときだけ、自分の権限で `~/.local/bin/hadolint` に置きます。
  一致しなければ入れずに、失敗として記録します。リリースバイナリの無いアーキテクチャでは警告だけ出します。
- どちらも、`hadolint` がすでに PATH にあれば何もしません。

### csb（コンテナサンドボックスの CLI）

`csb` は、リポジトリのスナップショットを使い捨てのコンテナへ写して作業し、成果をパッチでホストへ戻すコマンドです。

- `install.sh` は `~/.local/bin/csb` を、symlink ではなく 3 行の `/bin/sh` ラッパー（0755 の通常ファイル）として書きます（`.zshrc` が `~/.local/bin` を PATH に入れます）。
  - 中身は、起動の環境変数（`DEVELOPER_DIR` / `TOOLCHAINS` / `SDKROOT` / `__PYVENV_LAUNCHER__` / `PYTHONEXECUTABLE` / `PYTHONHOME` / `PYTHONPATH`）の `unset` と、`exec '<python>' -I -S '<HOME>/.claude/hooks/claude_sandbox.py' "$@"`（どちらも絶対パス）です。`_sandbox_gate.csb_wrapper_text` から生成します。
  - bash-review は、このファイルがテンプレートとバイト列まで一致し、`<python>` がフック自身を動かしているインタプリタと同じパスのときだけ csb を静的に許可します。手で書き換えたものや symlink は信頼しません。
- `<python>` は、bash-review のランチャーがフックを動かすのと同じ、root 所有のシステムの python3 です。PATH の `python3`・pyenv の shim・`.python-version` は使いません。
  - 候補はこの順に見ます。macOS は `/Library/Developer/CommandLineTools/usr/bin/python3` → `/usr/bin/python3`、Linux は `/usr/bin/python3` です。
  - 採るのは、パス・途中の symlink・祖先のディレクトリがすべて root 所有で group / other が書けず、実行できる通常ファイルで、3.9 以上のものです。
  - 見つからなければ csb は入れず、失敗として記録します。macOS では `xcode-select --install` で Command Line Tools を入れます。
  - csb は、パッケージとツールの導入が済んだ後（Homebrew の導入の後）で書きます。新しい macOS の `/usr/bin/python3` は Command Line Tools が無いと、起動するとインストールのダイアログを出す代用品だからです。
  - macOS で Command Line Tools が無いとき（`xcode-select -p` が失敗するとき）は、python を起動せず、警告を出して csb を飛ばします。失敗には数えません。`--dry-run` でも同じです。Tools を入れてから `./install.sh` を再実行してください。
  - 候補のパス（`.../usr/bin/python3`）は Python の更新で変わらないので、更新の後に書き直す必要はありません。

> **bash-review も同じインタプリタで動きます。** 信頼できるシステムの python3（root 所有で 3.9 以上）が無いと、フックは毎回 fail-closed になります（Claude は ask、Codex はブロック）。対応している Ubuntu 22.04 以降と Debian 12 以降は、標準の `/usr/bin/python3` がこれを満たします。

- チェックアウトを直接は指さず、`~/.claude/hooks` のリンク越しに指します。そのため、チェックアウトを移しても再実行で追従します。
- 以前の symlink の `csb` や手で置いたファイルは、バックアップしてから置き換えます。実ファイルは移さずにコピーを残し、新しいラッパーを `mv -f` で上書きするので、置き換えに失敗しても元の `csb` はそのまま残ります。symlink は旧リンク先を記録してから置き換えます。`--dry-run` では何を書くかだけを表示します。

### バージョンの固定（ピン）を上げる

固定値はすべて `install.sh` 冒頭にあり、上げ方もそこのコメントに書いてあります。対象は Docker / NodeSource の鍵の指紋、`NODESOURCE_NODE_MAJOR`、hadolint の版と sha256 です。
MCP サーバーの serena（`SERENA_REF`）、context7（`CONTEXT7_MCP_VERSION`）、Python の `mcp` パッケージ（`MCP_PY_VERSION`）も同じ場所にあります。
MCP サーバーは、固定値を書き換えて `./install.sh` を再実行すると、登録内容の違うものだけが登録し直されます。
登録し直す前に、旧登録（`claude mcp get <名前>` の出力）を `~/.dotfiles_backup_<日時>/mcp-<名前>.txt` に 0600 で保存します。`--api-key` のように自分で足した引数や環境変数は、ここに残ります。
画面には旧引数を出さず、保存先のパスだけを出します。保存できなかったときは、登録を消さずに失敗として記録します。消した後の登録に失敗したときも、保存先を添えて失敗として記録します。

---

## 共通トラブルシューティング

### install.sh が終了コード 1 で終わった

個々のインストール手順が失敗しても `install.sh` は警告を出して続行し、最後に失敗したステップの一覧を表示して終了コード `1` で終わります（`Installation completed!` は表示されません）。

```text
[ERROR] Installation finished with 2 failed step(s):
  - uv
  - zsh-autosuggestions
[INFO] Reasons are printed above as [WARNING] or [ERROR]. Fix the cause and re-run ./install.sh (finished steps are skipped).
```

1. 一覧の各項目について、ログの上の方にある `[WARNING] Failed to install <名前>` や `[ERROR] Could not ...` などの行と、その直後に字下げして出るインストーラ自身のエラー出力で原因を確認する
2. 原因（ネットワーク、`sudo` の権限、パッケージ名の変更など）を直す
3. `./install.sh` を再実行する。何度実行しても安全です。導入済みのツールは入れ直しません（PATH に無い pip `--user` の置き場所と、npm の AI CLI も導入済みとして検出します）。正しく張れているリンクにも触りません。MCP サーバーは、登録内容が `install.sh` の固定値と違うときだけ登録し直します

一覧には、パッケージ・ツール・プラグイン関連の失敗に加えて、最初のステップ（dotfiles のリンク作成）で書き込めなかったものも出ます。たとえば `link <パス>`、`Claude config links`、`git config (os.gitconfig)`、`Gemini settings.json` です。リンク作成のステップは、1 か所が書き込めなくても残りのリンクを張り終えてから次へ進みます。`./install.sh --help` で終了コードの意味（`0` 成功 / `1` 失敗したステップあり / `2` 引数の誤り）を確認できます。

置き換えたファイルとリンクの行き先はバックアップに残ります。実ファイルは `~/.dotfiles_backup_<日時>/` へ元のパス構造のまま移します（描画した `~/.codex/hooks.json` と `~/.local/bin/csb` は、置き換えに失敗しても元のファイルが残るよう、移さずにコピーを残して上書きします）。別の場所を指していた symlink（stow のリンクなど）は、旧リンク先を `~/.dotfiles_backup_<日時>/symlinks.txt` に `<パス> -> <旧リンク先>` の形で 1 行ずつ記録してから張り替えます。

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
# install.sh と同じ commit (install.sh 冒頭の VIM_PLUG_REF) を取る
ref=$(sed -n 's/^VIM_PLUG_REF="\(.*\)"$/\1/p' ~/dotfiles/install.sh)
curl -fLo ~/.vim/autoload/plug.vim --create-dirs \
    "https://raw.githubusercontent.com/junegunn/vim-plug/$ref/plug.vim"

vim +PlugInstall +qall
```

`install.sh` は vim-plug を `master` ではなく commit 固定（`VIM_PLUG_REF`）で取得します。
`master` から取ると、`install.sh` が検証済みとして扱うものとは別のバイト列が入り得ます。
上の手順は、`install.sh` と同じ commit を取ります。`./install.sh` を再実行しても同じものが入ります（`plug.vim` が無いときだけ取得します）。

### Node.js npmパッケージのインストールに失敗

```bash
# npmキャッシュのクリア
npm cache clean --force

# グローバルディレクトリの再設定
# ('~/.npm-global' とクォートするとチルダが展開されず、`~` という名前のディレクトリが
#  カレントディレクトリにできてしまう)
mkdir -p ~/.npm-global
npm config set prefix "$HOME/.npm-global"

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
