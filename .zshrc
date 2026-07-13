# PATH の重複エントリを自動除去する (ネストシェルでの肥大化防止)
typeset -U path PATH

# OS 判定。Homebrew / macOS 固有のパス・エイリアスを Linux/WSL でそのまま
# 読み込むと、LDFLAGS/CPPFLAGS が存在しない /opt/homebrew を指してネイティブ
# ビルド (pip の C 拡張ビルド等) を壊す実害があるため、uname でガードする。
case "$(uname -s)" in
  Darwin) _os=macos ;;
  Linux) _os=linux ;;
  *) _os=other ;;
esac

## Go
export PATH=~/go/bin:$PATH
export PATH=~/.npm-global/bin:$PATH

if [[ "$_os" == macos ]]; then
  # Homebrew (Apple Silicon) 固有のパス群。Linux には存在しないため読み込まない。
  export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:$PKG_CONFIG_PATH"
  ## PHP (Homebrew php@8.4)
  export PATH="/opt/homebrew/opt/php@8.4/bin:$PATH"
  export PATH="/opt/homebrew/opt/php@8.4/sbin:$PATH"
  export LDFLAGS="-L/opt/homebrew/opt/php@8.4/lib"
  export CPPFLAGS="-I/opt/homebrew/opt/php@8.4/include"
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
    cd "$(dirname "$selected_file")"
    nvim "$selected_file"
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

# fzf は上部で `eval "$(fzf --zsh)"` により初期化済み。旧来の ~/.fzf.zsh の
# 読み込みと、oh-my-zsh が既に実行済みの compinit の再実行はいずれも重複のため削除した。
# Docker CLI 補完の fpath 追加は oh-my-zsh の source 前に移動済み。

# When Neovim is closed
function precmd() {
  printf '\e[1 q'
}

# ai tools
# ollama
export OLLAMA_KEEP_ALIVE="-1"

## update
function update_ai_tools() {
  # Resolve the dotfiles checkout. Prefer the location ~/.zshrc points to
  # if it's a symlink into a dotfiles checkout (as install.sh sets up);
  # otherwise fall back to known checkout locations, since ~/.zshrc may
  # instead be a plain copy with no symlink to follow (as on this
  # machine). ${:-...} lets us apply :A/:h modifiers to a literal path
  # (there is no real parameter to attach them to).
  local dotfiles_dir script
  local -a candidates=(
    "${${:-$HOME/.zshrc}:A:h}"
    "$HOME/work/github/dotfiles"
    "$HOME/dotfiles"
    "$HOME/.dotfiles"
  )
  for dotfiles_dir in "${candidates[@]}"; do
    script="$dotfiles_dir/scripts/update_ai_tools.sh"
    [ -f "$script" ] && break
  done
  if [ ! -f "$script" ]; then
    echo "update_ai_tools: could not find scripts/update_ai_tools.sh (checked: ${(j:, :)candidates})" >&2
    return 1
  fi
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
alias g='gemini'
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
      claude "$*"
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
      case $words[2] in
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
