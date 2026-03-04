#!/bin/bash

# 送信したいコマンド
COMMAND="$*"

# synchronize-panes の現在の状態を取得
SYNC_STATE=$(tmux show-window-option -v synchronize-panes 2>/dev/null)

# sync がオンなら一時的にオフにする
if [[ "$SYNC_STATE" == "on" ]]; then
	tmux set-window-option synchronize-panes off
fi

# 現在のウィンドウのすべてのペインをリスト
tmux list-panes -F '#{pane_id} #{pane_current_command}' | while read -r PANE_ID COMMAND_NAME; do
	# 実行中のコマンドが 'nvim' でない場合
	if [[ "$COMMAND_NAME" != "nvim" ]]; then
		# send-keys でコマンドを送信
		tmux send-keys -t "$PANE_ID" "$COMMAND"
	fi
done

# sync がオンだった場合は元に戻す
if [[ "$SYNC_STATE" == "on" ]]; then
	tmux set-window-option synchronize-panes on
fi
