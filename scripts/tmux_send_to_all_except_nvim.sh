#!/bin/bash
set -euo pipefail

# Command to send
COMMAND="$*"

# Get the current state of synchronize-panes ("" when the option is unset)
SYNC_STATE=$(tmux show-window-option -v synchronize-panes 2>/dev/null || true)

# Temporarily turn off sync if it's on
if [[ "$SYNC_STATE" == "on" ]]; then
  tmux set-window-option synchronize-panes off
fi

# List all panes in the current window
tmux list-panes -F '#{pane_id} #{pane_current_command}' | while read -r PANE_ID COMMAND_NAME; do
  # If the running command is not 'nvim'
  if [[ "$COMMAND_NAME" != "nvim" ]]; then
    # Send the command using send-keys
    tmux send-keys -t "$PANE_ID" "$COMMAND"
  fi
done

# Restore sync if it was on initially
if [[ "$SYNC_STATE" == "on" ]]; then
  tmux set-window-option synchronize-panes on
fi
