#!/bin/bash
# Claude Code HooksのPostToolUse用静的解析スクリプト
# auto-format.sh実行後に呼び出すことを想定
# exit 2 でエラー内容をClaudeにフィードバックし、自動修正を促す
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、個々の
# linter が未導入/実行失敗でも他の言語のチェックやスクリプト全体を止めては
# ならない。各コマンドの失敗は `if ! OUTPUT=$(...)` / `command -v` チェックで
# 個別に処理し、最悪でも exit 0 で抜ける。

# jq が無い環境では処理できないため安全に抜ける
command -v jq >/dev/null 2>&1 || exit 0

# 共有ヘルパー(hook_log / hook_notify)。実体はこの .claude/hooks/ 側だけで、
# Codex 側は複製もリンクも持たず ../../.claude/hooks を自力で解決して読む。
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_common.sh
. "$HOOK_DIR/_hook_common.sh"
# 言語別マトリクスは Codex 版と共有する。実体はこの .claude/hooks/ 側だけで、
# Codex 側は複製もリンクも持たず ../../.claude/hooks を自力で解決して読む。
# shellcheck source=_lint_common.sh
. "$HOOK_DIR/_lint_common.sh"
# 読み込めていなければ fail-open で抜ける。shellcheck は関数の存在までは見ない
# ので、ここで確認しないと実行時まで気付けない。黙って進むと macOS では `log` が
# /usr/bin/log に解決され、記録が静かにシステムログへ消える。
if ! declare -F hook_log >/dev/null 2>&1 ||
  ! declare -F hook_lint_file >/dev/null 2>&1; then
  echo "lint.sh: could not load shared hook helpers from $HOOK_DIR" >&2
  exit 0
fi

# ログ設定
LOG_DIR="$HOME/.claude/logs"
LOG_FILE="$LOG_DIR/lint.log"
mkdir -p "$LOG_DIR"

# JSONからファイルパスを取得（auto-format.shと同じ方法）
FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

if [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

# 通知に使う。解析そのものは共有側が FILE_PATH から導出する。
BASENAME=$(basename "$FILE_PATH")
lint_errors=""

if hook_lint_file "$FILE_PATH" lint_errors "$LOG_FILE"; then
  hook_notify "Lint Passed" "$BASENAME のチェックが完了しました" 5
  exit 0
fi

# 共有側は生のエラー文字列だけを返す。Claude 向けの見出しと区切り線はここで付ける。
hook_notify "Lint Failed" "$BASENAME に問題が見つかりました" 15
echo "" >&2
echo "Lint errors found in $BASENAME:" >&2
echo "---" >&2
# `%b` ではなく `\n` だけを実改行へ戻す。`%b` は `\c` を「以降の出力を打ち切る」と
# 解釈するので、linter が引用したソース行に `\c` があると後続の指摘が Claude に
# 一件も届かない (exit 2 でブロックはするが、直すべき内容が渡らない)。
printf '%s' "${lint_errors//\\n/$'\n'}" >&2
echo "---" >&2
echo "Please fix the above issues." >&2
exit 2 # Claudeが内容を読んで自動修正を試みる
