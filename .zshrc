# If you come from bash you might have to change your $PATH.
# export PATH=$HOME/bin:/usr/local/bin:$PATH
## Go
export PATH=/home/sardonyx0827/go/bin:$PATH
export PATH=~/.npm-global/bin:$PATH
export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:$PKG_CONFIG_PATH"
## PHP
export PATH="/opt/homebrew/opt/php@8.4/bin:$PATH"
export PATH="/opt/homebrew/opt/php@8.4/sbin:$PATH"
export LDFLAGS="-L/opt/homebrew/opt/php@8.0/lib"
export CPPFLAGS="-I/opt/homebrew/opt/php@8.0/include"

# Path to your oh-my-zsh installation.
export ZSH="$HOME/.oh-my-zsh"

# set less options
export LESS="-i -M -R -x4"

# Set name of the theme to load --- if set to "random", it will
# load a random theme each time oh-my-zsh is loaded, in which case,
# to know which specific one was loaded, run: echo $RANDOM_THEME
# See https://github.com/ohmyzsh/ohmyzsh/wiki/Themes
#ZSH_THEME="risto" # fish like
ZSH_THEME="kennethreitz" # 1
#ZSH_THEME="gallois" # 2
#ZSH_THEME="eastwood" # 3
#ZSH_THEME="agnoster" # 4

# Set list of themes to pick from when loading at random
# Setting this variable when ZSH_THEME=random will cause zsh to load
# a theme from this variable instead of looking in $ZSH/themes/
# If set to an empty array, this variable will have no effect.
# ZSH_THEME_RANDOM_CANDIDATES=( "robbyrussell" "agnoster" )

# Uncomment the following line to use case-sensitive completion.
# CASE_SENSITIVE="true"

# Uncomment the following line to use hyphen-insensitive completion.
# Case-sensitive completion must be off. _ and - will be interchangeable.
# HYPHEN_INSENSITIVE="true"

# Uncomment one of the following lines to change the auto-update behavior
# zstyle ':omz:update' mode disabled  # disable automatic updates
# zstyle ':omz:update' mode auto      # update automatically without asking
# zstyle ':omz:update' mode reminder  # just remind me to update when it's time

# Uncomment the following line to change how often to auto-update (in days).
# zstyle ':omz:update' frequency 13

# Uncomment the following line if pasting URLs and other text is messed up.
# DISABLE_MAGIC_FUNCTIONS="true"

# Uncomment the following line to disable colors in ls.
# DISABLE_LS_COLORS="true"

# Uncomment the following line to disable auto-setting terminal title.
# DISABLE_AUTO_TITLE="true"

# Uncomment the following line to enable command auto-correction.
# ENABLE_CORRECTION="true"

# Uncomment the following line to display red dots whilst waiting for completion.
# You can also set it to another string to have that shown instead of the default red dots.
# e.g. COMPLETION_WAITING_DOTS="%F{yellow}waiting...%f"
# Caution: this setting can cause issues with multiline prompts in zsh < 5.7.1 (see #5765)
# COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Uncomment the following line if you want to change the command execution time
# stamp shown in the history command output.
# You can set one of the optional three formats:
# "mm/dd/yyyy"|"dd.mm.yyyy"|"yyyy-mm-dd"
# or set a custom format using the strftime function format specifications,
# see 'man strftime' for details.
# HIST_STAMPS="mm/dd/yyyy"

# Would you like to use another custom folder than $ZSH/custom?
# ZSH_CUSTOM=/path/to/new-custom-folder

# Which plugins would you like to load?
# Standard plugins can be found in $ZSH/plugins/
# Custom plugins may be added to $ZSH_CUSTOM/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(
  git
  zsh-autosuggestions
  zsh-syntax-highlighting
  z
)
# zsh-autosuggestions (5 or 6)
export ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=6'

source $ZSH/oh-my-zsh.sh

# User configuration

# export MANPATH="/usr/local/man:$MANPATH"

# You may need to manually set your language environment
# export LANG=en_US.UTF-8

# Preferred editor for local and remote sessions
# if [[ -n $SSH_CONNECTION ]]; then
#   export EDITOR='vim'
# else
#   export EDITOR='mvim'
# fi
export EDITOR=nvim
# Compilation flags
# export ARCHFLAGS="-arch x86_64"

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.
#
# Example aliases
# alias zshconfig="mate ~/.zshrc"
# alias ohmyzsh="mate ~/.oh-my-zsh"

# create work container on docker
cdi () {
  wezterm start -- bash -c "cd ~/work/github/first_boot_setup/docker/; bash create_docker_image.sh"
}

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
  wget -r -l $2 --convert-links --restrict-file-names=nocontrol -E $1
}

# wezterm
alias imgcat="wezterm imgcat"

# restart ibus
alias restart_ibus="ibus-daemon -drx"

# use nvim
alias v="nvim"
alias vim="nvim"
alias vimdiff="nvim -d"
alias view="nvim -R"

# use gh copilots
eval "$(gh copilot alias -- zsh)"
alias ghs="gh copilot suggest"

# or 'docker exec MyContainer nvim --headless --listen 0.0.0.0:22222'
alias nvim_listen="nvim --headless --listen 0.0.0.0:22222"
alias nvim_attach="nvim --remote-ui --server localhost:22222"

# change directory to workspace
alias cw="cd ~/work"

# ollama commands
alias dsollama="cd ~/work/sandbox/ollama/ && docker compose up -d && cd -"
alias deollama="cd ~/work/sandbox/ollama/ && docker compose down && cd -"

# vibe kanban
alias kanban="npx vibe-kanban"

# gimp
alias gimp="/Applications/GIMP.app/Contents/MacOS/gimp"

# python environment
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"

eval "$(fzf --zsh)"

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

# completion for gh command. need this solution "gh completion -s zsh > /usr/local/share/zsh/site-functions/_gh"
autoload -U compinit
compinit -i

# bind key(Ctrl + ])
bindkey '^]' autosuggest-accept
bindkey '^n' autosuggest-accept

[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh
# The following lines have been added by Docker Desktop to enable Docker CLI completions.
fpath=(/Users/sardonyx0827/.docker/completions $fpath)
autoload -Uz compinit
compinit
# End of Docker CLI completions

# yazi settings
function y() {
  local tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
  yazi "$@" --cwd-file="$tmp"
  if cwd="$(command cat -- "$tmp")" && [ -n "$cwd" ] && [ "$cwd" != "$PWD" ]; then
    builtin cd -- "$cwd"
  fi
  rm -f -- "$tmp"
}

# ai tools
## update
function update_ai_tools() {
  ~/work/github/dotfiles/update_ai_tools.sh
}
## gemini cli
alias push='gemini -y -p "pushして"'
alias commit_message='gemini -y -p "現在の変更を確認してCommitメッセージを作成してください。Commitメッセージのみを出力してください。"'
alias pull_request='gemini -y -p "pr作成して"'
alias explain='gemini -y -p "現在のディレクトリにあるコンテンツを確認して、どんなプロジェクトや構成なのかを要点をまとめて説明してください"'
function translate() {
  gemini -y -p  "これを日本語であれば英語、日本語以外であれば日本語に翻訳してください: $*"
}
## codex cli
alias push_codex='codex exec --full-auto "pushして"'
alias commit_message_codex='codex exec --full-auto "現在の変更を確認してCommitメッセージを作成してください。Commitメッセージのみを出力してください。"'
alias pull_request_codex='codex exec --full-auto "pr作成して"'
alias explain_codex='codex exec --skip-git-repo-check "現在のディレクトリにあるコンテンツを確認して、どんなプロジェクトや構成なのかを要点をまとめて説明してください"'
function translate_codex() {
  codex exec --skip-git-repo-check "これを日本語であれば英語、日本語以外であれば日本語に翻訳してください: $*"
}
