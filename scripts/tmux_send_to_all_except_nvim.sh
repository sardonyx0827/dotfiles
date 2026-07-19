#!/bin/bash
set -euo pipefail

# Command to send
COMMAND="$*"

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
