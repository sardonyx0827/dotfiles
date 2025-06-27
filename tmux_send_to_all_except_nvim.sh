#!/bin/bash

# 送信したいコマンド
COMMAND="$*"

# 現在のウィンドウのすべてのペインをリスト
tmux list-panes -F '#{pane_id} #{pane_current_command}' | while read -r PANE_ID COMMAND_NAME; do
    # 実行中のコマンドが 'nvim' でない場合
    if [[ "$COMMAND_NAME" != "nvim" ]]; then
        # send-keys でコマンドを送信
        tmux send-keys -t "$PANE_ID" "$COMMAND"
    fi
done
