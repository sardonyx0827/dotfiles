#!/bin/bash
set -euo pipefail

# The tmux user option `.tmux.conf`'s `bind S` uses as its mailbox.
TMUX_SEND_OPTION="@send_to_all_except_nvim"

# The command to send arrives one of two ways.
#   1. As arguments -- the manual path docs/configuration.md documents. The
#      calling shell has already done the word splitting, so rejoining with
#      "$*" is fine here.
#   2. Through the tmux user option -- the path `bind S` uses. tmux offers no
#      way to shell-quote a command-prompt response, so splicing it into
#      run-shell's command string let a typed quote break out of the shell
#      word and execute in the tmux server's shell. Passing it through an
#      option instead keeps the response away from any shell parser entirely.
if [ "$#" -gt 0 ]; then
  COMMAND="$*"
else
  # `-q` returns empty (status 0) for an unset option; `|| true` inside the
  # substitution covers tmux itself failing, so `set -e` cannot cut us off here.
  COMMAND=$(tmux show-options -gqv "$TMUX_SEND_OPTION" 2>/dev/null || true)
  # The mailbox is one-shot. Clear it as soon as it is read -- and before the
  # send loop, so a failure partway through cannot leave the previous command
  # armed to be replayed into every pane on the next argument-less run.
  tmux set-option -gu "$TMUX_SEND_OPTION" 2>/dev/null || true
fi

# Nothing to send: an empty prompt response (or a stray argument-less call)
# must not fire a bare Enter at every pane, nor pointlessly toggle
# synchronize-panes off and on. Bail out before either happens.
if [ -z "$COMMAND" ]; then
  exit 0
fi

# Get the current state of synchronize-panes ("" when the option is unset)
SYNC_STATE=$(tmux show-window-option -v synchronize-panes 2>/dev/null || true)

# Restore sync on exit no matter how the script ends (including a send-keys
# failure inside the loop below under `set -e`), so a single failed pane can
# never leave synchronize-panes stuck off.
restore_sync() {
  if [[ "$SYNC_STATE" == "on" ]]; then
    tmux set-window-option synchronize-panes on
  fi
}
trap restore_sync EXIT

# Temporarily turn off sync if it's on
if [[ "$SYNC_STATE" == "on" ]]; then
  tmux set-window-option synchronize-panes off
fi

# List all panes in the current window
tmux list-panes -F '#{pane_id} #{pane_current_command}' | while read -r PANE_ID COMMAND_NAME; do
  # If the running command is not 'nvim'
  if [[ "$COMMAND_NAME" != "nvim" ]]; then
    # Send the command using send-keys, then Enter to actually execute it.
    # `|| true` keeps one pane's failure from aborting the remaining panes.
    tmux send-keys -t "$PANE_ID" "$COMMAND" Enter || true
  fi
done
