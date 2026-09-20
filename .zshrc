# PATH / fpath の重複エントリを自動除去する (ネストシェルでの肥大化防止)。
# fpath を含めるのは brew shellenv が FPATH を export するため: 子シェルへ
# 継承された状態で下の `fpath=(~/.docker/completions $fpath)` が走ると、
# ネストのたびに同じ要素が積み上がる。
typeset -U path PATH fpath FPATH

# OS 判定。Homebrew / macOS 固有のパス・エイリアスを Linux/WSL でそのまま
# 読み込むと存在しない /opt/homebrew を指してしまうため、uname でガードする。
# この戒めは LDFLAGS/CPPFLAGS が存在しない keg を指してネイティブビルド
# (pip の C 拡張ビルド等) を壊した事故に由来する。その 2 変数は下で設定しなく
# なったが、ガード自体は brew shellenv と PKG_CONFIG_PATH をなお守っている。
case "$(uname -s)" in
  Darwin) _os=macos ;;
  Linux) _os=linux ;;
  *) _os=other ;;
esac

# Homebrew を PATH に載せる。install_homebrew (install.sh) の
# `eval "$(brew shellenv)"` はスクリプトのプロセス内限定で終了と同時に消えるため、
# 下の ~/.local/bin 等とまったく同じ理由でここでも恒久化する。これが無いと
# Apple Silicon (/opt/homebrew は /etc/paths に載らない) では install.sh 完走後に
# 端末を開き直した時点で brew 本体と brew 導入物がまとめて PATH から消える。
# Intel の /usr/local は元から /etc/paths に載るが、HOMEBREW_PREFIX 等を
# 揃えるため同じ経路を通す。
#
# 下の PATH 追加より「前」に置くのが要点。shellenv は prepend するので、
# 後ろに置くと Homebrew 版が ~/go/bin や ~/.local/bin を追い越してしまう。
# 先に通しておけば Homebrew 公式手順 (~/.zprofile で eval) と同じ
# 「ユーザ側が優先」の順序になる。
#
# 条件を `_os == macos` だけにし、HOMEBREW_PREFIX が既にあっても毎回 eval する。
# 「設定済みなら省く」ガードを入れると入れ子の login シェルで壊れる: /etc/zprofile
# の path_helper が継承 PATH を組み替えて /opt/homebrew/bin を末尾へ回すため、
# 再 prepend を省いた瞬間 Homebrew が /usr/bin より後ろに落ちる (実測で
# `git` が /usr/bin/git に解決された)。冪等性はガードではなく上の typeset -U と
# 直下の typeset -xTU で型として持たせる — INFOPATH は素の文字列のままだと
# 呼び直すたびに同じ要素が積み上がるので、配列に tie して重複除去させる。
if [[ "$_os" == macos ]]; then
  for _brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$_brew" ]; then
      typeset -xTU INFOPATH infopath
      eval "$("$_brew" shellenv zsh)"
      break
    fi
  done
  unset _brew
fi

## Go
export PATH=~/go/bin:$PATH
export PATH=~/.npm-global/bin:$PATH
# install.sh がここへ入れるもの: uv / uvx、link_debian_alias が作る bat / fd
# (Debian のみ)、そして pip の user スキームが posix_user の環境では
# pip_install_user の ruff / bandit / mypy / autopep8 / isort。install.sh 側の
# export はスクリプトのプロセス内限定なので、ここで恒久化しないとインストール
# 直後から command -v が外れる (フックの Python 整形が無言で飛び、下の fzf
# preview の bat も消える)。
export PATH=~/.local/bin:$PATH
# macOS の pip は --user 先が ~/.local/bin ではない。Homebrew 版も Apple 版も
# osx_framework_user スキームで、スクリプトは ~/Library/Python/<X.Y>/bin に入る
# (posix_user = ~/.local/bin になるのは pyenv 版だけで、install_pyenv は ubuntu
# 限定)。上の行だけでは macOS で ruff が見つからないままになる。
# (N) は該当が無ければ黙って空に潰す glob 修飾子なので、この行は Linux では
# 無害な no-op になり OS ガードを要らなくする。* は残っている全バージョンを拾う。
path=(~/Library/Python/*/bin(N) $path)

if [[ "$_os" == macos ]]; then
  # Homebrew (Apple Silicon) 固有のパス群。Linux には存在しないため読み込まない。
  export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:$PKG_CONFIG_PATH"
  # ここで LDFLAGS / CPPFLAGS は設定しない。以前は php@8.4 の keg 向けに
  # 「代入」していたが、(1) install.sh が入れるのは php-cs-fixer の依存として
  # 引かれる素の php であって php@8.4 ではなく、このリポジトリで構築した
  # マシンでは一度も成立しない設定だった、(2) 代入なので ~/.zshenv / direnv /
  # 親シェル (tmux ペイン、入れ子 zsh) が入れた値を毎回捨てていた。
  # keg-only formula (openssl@3, zlib, readline ...) の brew info はまさに
  # この 2 変数への設定を案内するため、捨てるとネイティブビルドが落ちる。
  # 必要になったら ~/.zshenv 側で追記する形にすること。
fi

export ZSH="$HOME/.oh-my-zsh"

# set less options
export LESS="-i -M -R -x4"

ZSH_THEME="px-rose-pine"

plugins=(
  git
  zsh-autosuggestions
  zsh-syntax-highlighting
  z
)
# zsh-autosuggestions のサジェスト文字色 (cyan)
export ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=6'

# Docker Desktop の CLI 補完。oh-my-zsh が実行する compinit より前に fpath へ
# 追加しないと補完が読み込まれないため、ここで設定する。
fpath=(~/.docker/completions $fpath)

source $ZSH/oh-my-zsh.sh

export EDITOR=nvim

# using fzf
sshs () {
  t=$(cat ~/.ssh/config | grep 'Host ' | cut -f2 -d' ' | fzf --preview "cat ~/.ssh/config | sed -ne '/^Host {}$/,/^\s*$/p'")
  if [ -n "$t" ]; then
    ssh "$t"
  fi
}
cf () {
  selected_file=$(find . -type d -name "*" ! -regex ".*/node_modules/.*" ! -regex ".*/.git/.*" | fzf --extended)
  if [ -n "$selected_file" ]; then
    cd "$selected_file"
  fi
}
vf () {
  selected_file=$(fzf --extended --preview 'bat --style=numbers --color=always {}')
  if [ -n "$selected_file" ]; then
    # cd changes cwd to the file's dir, so open by basename -- reusing the
    # original (cwd-relative) path here looked for src/foo under src/, i.e.
    # src/src/foo, and opened an empty buffer for anything below the cwd.
    cd "$(dirname "$selected_file")" && nvim "$(basename "$selected_file")"
  fi
}

# download web contents
dwc () {
  if [ -z "$1" ]; then
    echo "Usage: dwc <url> [depth (default: 5)]" >&2
    return 1
  fi
  wget -r -l "${2:-5}" --convert-links --restrict-file-names=nocontrol -E "$1"
}

# wezterm
alias imgcat="wezterm imgcat"

# restart ibus (Linux/IBus 環境のみ)
if [[ "$_os" == linux ]]; then
  alias restart_ibus="ibus-daemon -drx"
fi

# typo
alias sl="ls"

# use nvim
alias v="nvim"
alias vim="nvim"
alias vimdiff="nvim -d"
alias view="nvim -R"

# or 'docker exec MyContainer nvim --headless --listen 0.0.0.0:22222'
alias nvim_listen="nvim --headless --listen 0.0.0.0:22222"
alias nvim_attach="nvim --remote-ui --server localhost:22222"

# change directory to workspace
alias cw="cd ~/work"

# Resolve a script inside the dotfiles checkout and print its absolute path.
# Prefer the location ~/.zshrc points to if it's a symlink into a dotfiles
# checkout (as install.sh sets up); otherwise fall back to known checkout
# locations, since ~/.zshrc may instead be a plain copy with no symlink to
# follow -- a hand-installed machine, or one that predates the symlink.
# ${:-...} lets us apply :A/:h modifiers to a literal path (there is no real
# parameter to attach them to).
#
# 名前のアンダースコアが 2 個なのは意図的。Claude Code はシェルの関数を
# `typeset +f | grep -vE '^_[^_]'` でスナップショットに焼くので、_ 1 個始まりの
# 名前は補完関数とみなされて捨てられる。呼び出し側の np()/update_ai_tools() は
# 普通の名前なので残り、消えたヘルパを呼んで command not found になる。
# private のつもりで _ 1 個に戻さないこと。
function __dotfiles_script() {
  local rel="$1" dotfiles_dir script
  local -a candidates=(
    "${${:-$HOME/.zshrc}:A:h}"
    "$HOME/work/github/dotfiles"
    "$HOME/dotfiles"
    "$HOME/.dotfiles"
  )
  for dotfiles_dir in "${candidates[@]}"; do
    script="$dotfiles_dir/$rel"
    if [ -f "$script" ]; then
      print -r -- "$script"
      return 0
    fi
  done
  echo "__dotfiles_script: could not find $rel (checked: ${(j:, :)candidates})" >&2
  return 1
}

# 新規プロジェクトの雛形作成。実処理は scripts/new_project.sh 側にあり、この
# 関数は「作成先へ cd する」ためだけに存在する (子プロセスは親シェルの cwd を
# 変えられない)。引数はすべてスクリプトへ素通しするので、オプションの知識は
# こちらには持たせないこと。移動先は一時ファイル経由で受け取り、スクリプトが
# 何も書かなかったとき (--dry-run / --help / 失敗) はその場に留まる。
#   np                 カレントディレクトリを整える
#   np ~/work/foo      作って移動する
#   np -n ~/work/foo   作らずに予定だけ見る
function np() {
  local script dir_file target ret=1
  script=$(__dotfiles_script scripts/new_project.sh) || return 1
  dir_file=$(mktemp "${TMPDIR:-/tmp}/np.XXXXXX") || return 1

  # 受け渡し用の一時ファイルの後始末。always ブロックは正常終了・エラー・
  # return は拾うが、シグナルでは走らない (Ctrl-C で実測済み) ので、trap と
  # 併用する。local_options / local_traps により、ここでのオプション変更と
  # trap は関数を抜けるときに元へ戻る。
  setopt local_options local_traps
  trap 'rm -f "$dir_file"' INT TERM HUP
  {
    NEW_PROJECT_DIR_FILE="$dir_file" "$script" "$@"
    ret=$?
    [ -s "$dir_file" ] && target=$(<"$dir_file")
  } always {
    rm -f "$dir_file"
  }

  if [ "$ret" -eq 0 ] && [ -n "$target" ]; then
    cd "$target" || return 1
  fi
  return $ret
}

# gimp (macOS の GIMP.app のみ)
if [[ "$_os" == macos ]]; then
  alias gimp="/Applications/GIMP.app/Contents/MacOS/gimp"
fi

# python environment
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init --path)"
  eval "$(pyenv init -)"
fi

# rust environment
export PATH="$HOME/.cargo/bin:$PATH"

command -v fzf >/dev/null 2>&1 && eval "$(fzf --zsh)"

# history
HISTFILE=~/.zsh_history
HISTSIZE=100000
SAVEHIST=100000
setopt append_history
setopt auto_pushd
setopt pushd_ignore_dups
setopt share_history
setopt hist_reduce_blanks
setopt hist_ignore_space
setopt hist_ignore_all_dups

# display japanese character
setopt print_eight_bit
setopt extended_glob
setopt braceccl
# correct command when mistyped
setopt correct

# bind key(Ctrl + ])
bindkey '^]' autosuggest-accept
bindkey '^n' autosuggest-accept

# When Neovim is closed
function precmd() {
  printf '\e[1 q'
}

# ai tools
# ollama
export OLLAMA_KEEP_ALIVE="-1"

## update
function update_ai_tools() {
  # チェックアウトの解決は __dotfiles_script() に集約してある (np() と共通)。
  local script
  script=$(__dotfiles_script scripts/update_ai_tools.sh) || return 1
  "$script"
}

## Claude CLI
alias c='claude'
alias cl='claude'
# Claude Code の実験的 AgentTeams (tmux teammate) の後始末。
# CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 + teammateMode=tmux では、lead を
# 強制終了したり teammate が権限待ちでハングすると teammate の claude プロセスが
# 終了せず tmux ペインが閉じ残る (idle タイムアウトによる強制 kill は存在しない)。
# teammate は `--agent-id/--agent-name/...` フラグ付きで起動されるため、その痕跡を
# pane_start_command から検出して一覧・kill する。死亡ペイン (remain-on-exit の
# 取り残し) も対象。自分の実行ペイン ($TMUX_PANE) は常に除外し、巻き込み事故を防ぐ。
#   claude-teammates        : teammate ペインを検出して一覧表示 (kill しない)
#   claude-teammates -f      : 検出したペインを kill (確認あり、-y で省略)
#   claude-teammates -i      : 全ペインから fzf で選んで kill (検出漏れ時の手動用)
function claude-teammates() {
  if [[ -z "$TMUX" ]]; then
    echo "claude-teammates: tmux セッション内で実行してください" >&2
    return 1
  fi

  local mode=list force=0 yes=0
  while (( $# )); do
    case "$1" in
      -f|--force|clean) force=1 ;;
      -y|--yes)         yes=1 ;;
      -i|--interactive) mode=interactive ;;
      -l|--list|list)   force=0 ;;
      -h|--help)
        print -r -- "Usage: claude-teammates [-f|--force] [-y] [-i|--interactive]"
        print -r -- "  (default)         teammate ペインを検出して一覧表示"
        print -r -- "  -f, --force       検出したペインを kill (自ペインは除外)"
        print -r -- "  -y, --yes         kill 前の確認をスキップ"
        print -r -- "  -i, --interactive fzf で全ペインから選んで kill"
        return 0 ;;
      *) echo "claude-teammates: unknown arg '$1' (see -h)" >&2; return 1 ;;
    esac
    shift
  done

  # 対話モード: 自ペイン以外の全ペインを fzf に流し、選んだものを kill (検出漏れ用)
  if [[ "$mode" == interactive ]]; then
    if ! command -v fzf >/dev/null 2>&1; then
      echo "claude-teammates: -i には fzf が必要です" >&2
      return 1
    fi
    local picks id
    picks=$(tmux list-panes -a \
        -F '#{pane_id} [#{session_name}:#{window_index}] #{pane_current_command} :: #{=50:pane_title}' \
      | awk -v me="$TMUX_PANE" '$1 != me' \
      | fzf --multi --prompt='kill teammate panes> ')
    [[ -z "$picks" ]] && { echo "選択なし。中止しました。"; return 0; }
    print -r -- "$picks" | while read -r id _; do
      tmux kill-pane -t "$id" 2>/dev/null && echo "killed $id" || echo "skip $id"
    done
    return 0
  fi

  # 自動検出: teammate 起動フラグ付き or 死亡ペインを収集 (自ペインは必ず除外)。
  # pane_start_command はスペースを含むためタブ区切りで読む。
  local -a targets
  local pane_id dead start
  while IFS=$'\t' read -r pane_id dead start; do
    [[ "$pane_id" == "$TMUX_PANE" ]] && continue
    if [[ "$dead" == "1" || "$start" == *--agent-* ]]; then
      targets+=("$pane_id")
    fi
  done < <(tmux list-panes -a -F $'#{pane_id}\t#{pane_dead}\t#{pane_start_command}')

  if (( ${#targets[@]} == 0 )); then
    echo "閉じ残った teammate ペインは見つかりませんでした。(検出漏れ時は -i で手動選択)"
    return 0
  fi

  echo "検出した teammate ペイン: ${#targets[@]} 件"
  local p
  for p in "${targets[@]}"; do
    tmux display-message -p -t "$p" '  #{pane_id}  [#{session_name}:#{window_index}]  #{=45:pane_title}'
  done

  if (( ! force )); then
    echo "kill するには: claude-teammates -f"
    return 0
  fi

  if (( ! yes )); then
    printf '%s' "上記 ${#targets[@]} 件を kill しますか? [y/N] "
    local ans; read -r ans
    case "$ans" in
      y|Y|yes|YES|Yes) ;;
      *) echo "中止しました。"; return 1 ;;
    esac
  fi

  for p in "${targets[@]}"; do
    tmux kill-pane -t "$p" 2>/dev/null && echo "killed $p" || echo "skip $p (already gone)"
  done
}
alias cct='claude-teammates'

## Codex CLI
alias cx='codex'
## GitHub Copilot CLI
alias cop='copilot'
## Gemini CLI
alias ge='gemini'
# push / commit / pull_request はリモートや履歴を変更するため -y (自動承認) は使わず、
# 対話モード (-i) で都度ユーザーに確認させる。
alias push='gemini -i "pushして"'
alias commit='gemini -i "commitして"'
alias pull_request='gemini -i "pr作成して"'
# commit_message / explain は変更を伴わない読み取り + テキスト出力のみのため -y を許容する
alias commit_message='gemini -y -p "現在の変更を確認してCommitメッセージを作成してください。Commitメッセージのみを出力してください。"'
alias explain='gemini -y -p "現在のディレクトリにあるコンテンツを確認して、どんなプロジェクトや構成なのかを要点をまとめて説明してください"'
function translate() {
  gemini -y -p  "これを日本語であれば英語、日本語以外であれば日本語に翻訳してください: $*"
}
## Gemma
alias gemma='ollama run gemma4:e4b'

### use claude commands
function mc() {
  case $1 in
    explain)
      shift
      claude --model "sonnet" -p "現在のディレクトリにあるコンテンツを確認して、どんなプロジェクトや構成なのかを要点をまとめて説明してください。mcpを利用してはいけません。"
      ;;
    translate)
      shift
      claude --model "haiku" -p "これを日本語であれば英語、日本語以外であれば日本語に翻訳してください: $*"
      ;;
    execute)
      shift
      claude --model "sonnet" -p "$*"
      ;;
    cli)
      shift
      # ここだけ "$@" なのは意図的。cli) は下の補完定義が「標準のClaudeコマンドを
      # 実行」と言うとおりの素通しなので、打った語を語のまま claude へ渡さないと
      # いけない。"$*" は全引数を 1 語に連結してしまうため、`mc cli mcp list` が
      # `claude "mcp list"` (argc=1) になり、claude CLI 側はそれをサブコマンドでは
      # なく 1 本のプロンプト文字列として解釈していた。引数無しの `mc cli` も
      # "$*" では空文字列を 1 個渡すことになり対話起動にならない。
      # 逆に上の translate) / execute) が "$*" なのは正しい。あちらは日本語の
      # プロンプト文へ語を埋め込む用途で、1 語に潰れるのが仕様そのもの。
      # 「揃える」つもりであちらを "$@" にするとバグを作り込むことになる。
      claude "$@"
      ;;
    push)
      shift
      claude --model "haiku" -p "pushして。pull requestを作成してはいけません。"
      ;;
    commit)
      shift
      claude --model "haiku" -p "commitして"
      ;;
    commit_message)
      shift
      claude --model "haiku" -p "現在の変更を確認してCommitメッセージを作成してください。Commitメッセージのみを出力してください。mcpを使用してはいけません。"
      ;;
    pull_request)
      shift
      claude --model "sonnet" -p "pr作成して。mcpを使用してはいけません。"
      ;;
    *)
      echo "Usage: mc(my_claude) {explain|translate|execute|cli|push|commit|commit_message|pull_request} [arguments...]"
      return 1
      ;;
  esac
}
_mc() {
  local context state line
  _arguments \
    '1:command:->commands' \
    '*::args:->args'
  case $state in
    commands)
      local -a commands
      commands=(
        'explain:現在のディレクトリの内容を分析してプロジェクトの概要を説明'
        'translate:日本語⇔英語の相互翻訳を実行'
        'execute:Claude Sonnetモデルでプロンプトを実行'
        'cli:標準のClaudeコマンドを実行'
        'push:変更のコミットメッセージを生成してpushする'
        'commit:変更のコミットメッセージを生成してcommitする'
        'commit_message:変更のcommitメッセージを生成'
        'pull_request:変更のpull requestを生成'
      )
      _describe 'mc commands' commands
      ;;
    args)
      # `*::args` rewrites `words` to the normal arguments only, so the
      # subcommand sits at words[1] (the idiom zsh's own _asciinema / _augeas
      # use); words[2] was the argument AFTER it and never matched a hint.
      case $words[1] in
        translate|execute|cli)
          _message "プロンプトまたは翻訳したいテキストを入力"
          ;;
        explain)
          _message "引数は不要です(現在のディレクトリを分析)"
          ;;
        push)
          _message "引数は不要です(変更のcommitとpush)"
          ;;
        commit)
          _message "引数は不要です(変更のcommit)"
          ;;
        commit_message)
          _message "引数は不要です(commit messageの生成)"
          ;;
        pull_request)
          _message "引数は不要です(pull requestの生成)"
          ;;
      esac
      ;;
  esac
}
compdef _mc mc

# API key やパスワードなどの秘密情報は ~/.zsh_secrets に書き、Git 管理下に置かない。
# `&&` ではなく if で書くのは、ここが .zshrc の最終行だから。`[ -f ... ] && ...` は
# ファイルが無いとき 1 を返し、それがそのまま .zshrc 自身の終了ステータスになる
# (install.sh の雛形生成をまだ走らせていないマシンがこれに当たる)。else の無い if は
# 条件が偽でも 0 を返すので、その窓を塞げる。
if [ -f ~/.zsh_secrets ]; then
  source ~/.zsh_secrets
fi
