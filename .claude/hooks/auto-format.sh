#!/bin/bash
# Claude Code HooksのPostToolUse用自動フォーマットスクリプト
# 標準入力からJSON形式のデータを受け取り、ファイルパスを抽出してフォーマットを実行
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、jq の
# パース失敗や個々のフォーマッターの失敗でフック自体を異常終了させては
# ならない(lint.sh / git-push-review.sh と同じ方針)。失敗は if 分岐と
# `|| true` で個別に処理し、最悪でも exit 0 で抜ける。

# jq が無い環境では処理できないため安全に抜ける
command -v jq >/dev/null 2>&1 || exit 0

# 共有ヘルパー(hook_log / hook_notify)と整形マトリクス(hook_format_file)。
# 実体は .claude/hooks/ 側、.codex/hooks/ 側は symlink。
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_common.sh
. "$HOOK_DIR/_hook_common.sh"
# shellcheck source=_format_common.sh
. "$HOOK_DIR/_format_common.sh"
# 読み込めていなければ fail-open で抜ける。core.symlinks=false の checkout で
# symlink がテキスト化した場合もここに落ちる。shellcheck は関数の存在までは
# 見ないので、確認しないと実行時まで気付けない。黙って進むと macOS では `log`
# が /usr/bin/log に解決され、記録が静かにシステムログへ消える。
if ! declare -F hook_log >/dev/null 2>&1 ||
  ! declare -F hook_format_file >/dev/null 2>&1; then
  echo "auto-format.sh: could not load shared hook helpers from $HOOK_DIR" >&2
  exit 0
fi

# ログ設定
LOG_DIR="$HOME/.claude/logs"
LOG_FILE="$LOG_DIR/format.log"
mkdir -p "$LOG_DIR"

# JSONからファイルパスを取得
FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
  echo "No file path found in input" >&2
  exit 0
fi

if [ ! -f "$FILE_PATH" ]; then
  echo "File does not exist: $FILE_PATH" >&2
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")

# 整形が実際に走って成功したときだけ通知する(対象外の拡張子では通知しない)。
if hook_format_file "$FILE_PATH" "$LOG_FILE"; then
  hook_notify "Format Done" "$BASENAME をフォーマットしました" 4
fi

echo "Formatting completed for $BASENAME"
exit 0
