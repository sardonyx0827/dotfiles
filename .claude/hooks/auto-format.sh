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

# ログ・通知設定
LOG_DIR="$HOME/.claude/logs"
LOG_FILE="$LOG_DIR/format.log"
MAX_LOG_LINES=500
mkdir -p "$LOG_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
  local lines
  lines=$(wc -l <"$LOG_FILE")
  if [ "$lines" -gt "$MAX_LOG_LINES" ]; then
    tail -n "$MAX_LOG_LINES" "$LOG_FILE" >"${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
  fi
}

notify() {
  local title="$1"
  local message="$2"
  local timeout="${3:-5}"
  if command -v terminal-notifier >/dev/null 2>&1; then
    terminal-notifier -title "$title" -message "$message" -timeout "$timeout" 2>/dev/null
  elif command -v osascript >/dev/null 2>&1; then
    # 環境変数経由で値を渡すことで AppleScript インジェクションを防ぐ
    # (system attribute ではなく printenv 経由にすることで日本語の
    #  文字化け(MacRomanでの解釈)も避けられる)
    HOOK_NOTIFY_TITLE="$title" HOOK_NOTIFY_MESSAGE="$message" osascript \
      -e 'set titleText to do shell script "printenv HOOK_NOTIFY_TITLE || true"' \
      -e 'set msgText to do shell script "printenv HOOK_NOTIFY_MESSAGE || true"' \
      -e 'display notification msgText with title titleText' \
      2>/dev/null
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send --expire-time "$((timeout * 1000))" "$title" "$message" 2>/dev/null
  fi
}

# JSONからファイルパスを取得
FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)

# ファイルパスが取得できない場合は終了
if [ -z "$FILE_PATH" ]; then
  echo "No file path found in input" >&2
  exit 0
fi

# ファイルが存在しない場合は終了
if [ ! -f "$FILE_PATH" ]; then
  echo "File does not exist: $FILE_PATH" >&2
  exit 0
fi

# ファイル拡張子を取得
EXTENSION="${FILE_PATH##*.}"
BASENAME=$(basename "$FILE_PATH")
FORMATTED=false # フォーマッターが実際に実行され、かつ成功したか

log "--- format start: $FILE_PATH ---"
echo "Auto-formatting: $BASENAME"

# 拡張子に応じてフォーマッターを実行
case "$EXTENSION" in
# JavaScript/TypeScript/JSON/CSS/HTML/Markdown
js | jsx | ts | tsx | json | css | scss | less | html | htm | md | yaml | yml)
  if command -v prettier >/dev/null 2>&1; then
    echo "  Running Prettier..."
    if err=$(prettier --write "$FILE_PATH" 2>&1); then
      echo "  Prettier completed"
      FORMATTED=true
    else
      echo "  Prettier failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  else
    echo "  Prettier not found, skipping JS/TS formatting"
  fi
  ;;

# Python
py)
  if command -v ruff >/dev/null 2>&1; then
    # インポート整列(ruff互換)→整形の順で ruff に一本化する。
    # ここで isort を併用すると `ruff format` の結果を崩し、CI の
    # `ruff format --check` が落ちるため、ruff がある場合は isort を使わない。
    echo "  Running ruff (import sort + format)..."
    ruff check --select I --fix "$FILE_PATH" >/dev/null 2>&1 || true
    if err=$(ruff format "$FILE_PATH" 2>&1); then
      echo "  ruff completed"
      FORMATTED=true
    else
      echo "  ruff failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  elif command -v autopep8 >/dev/null 2>&1; then
    echo "  Running autopep8..."
    if err=$(autopep8 --in-place "$FILE_PATH" 2>&1); then
      echo "  autopep8 completed"
      FORMATTED=true
    else
      echo "  autopep8 failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
    # ruff が無い環境でのみ isort を併用する (ruff とは排他)
    if command -v isort >/dev/null 2>&1; then
      echo "  Running isort..."
      if err=$(isort "$FILE_PATH" 2>&1); then
        echo "  isort completed"
        FORMATTED=true
      else
        echo "  isort failed" >&2
        [ -n "$err" ] && echo "$err" >&2
      fi
    fi
  else
    echo "  No Python formatter found (ruff/autopep8)"
  fi
  ;;

# Rust
rs)
  if command -v rustfmt >/dev/null 2>&1; then
    echo "  Running rustfmt..."
    if err=$(rustfmt "$FILE_PATH" 2>&1); then
      echo "  rustfmt completed"
      FORMATTED=true
    else
      echo "  rustfmt failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  elif command -v cargo >/dev/null 2>&1; then
    echo "  Running cargo fmt..."
    if err=$(cd "$(dirname "$FILE_PATH")" && cargo fmt 2>&1); then
      echo "  cargo fmt completed"
      FORMATTED=true
    else
      echo "  cargo fmt failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  else
    echo "  No Rust formatter found (rustfmt/cargo)"
  fi
  ;;

# Go
go)
  if command -v gofmt >/dev/null 2>&1; then
    echo "  Running gofmt..."
    if err=$(gofmt -w "$FILE_PATH" 2>&1); then
      echo "  gofmt completed"
      FORMATTED=true
    else
      echo "  gofmt failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  fi
  if command -v goimports >/dev/null 2>&1; then
    echo "  Running goimports..."
    if err=$(goimports -w "$FILE_PATH" 2>&1); then
      echo "  goimports completed"
      FORMATTED=true
    else
      echo "  goimports failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  fi
  ;;

# Java
java)
  if command -v google-java-format >/dev/null 2>&1; then
    echo "  Running google-java-format..."
    if err=$(google-java-format --replace "$FILE_PATH" 2>&1); then
      echo "  google-java-format completed"
      FORMATTED=true
    else
      echo "  google-java-format failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  else
    echo "  google-java-format not found"
  fi
  ;;

# C/C++
c | cpp | cc | cxx | h | hpp)
  if command -v clang-format >/dev/null 2>&1; then
    echo "  Running clang-format..."
    if err=$(clang-format -i "$FILE_PATH" 2>&1); then
      echo "  clang-format completed"
      FORMATTED=true
    else
      echo "  clang-format failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  else
    echo "  clang-format not found"
  fi
  ;;

# Ruby
rb)
  if command -v rubocop >/dev/null 2>&1; then
    echo "  Running rubocop..."
    if err=$(rubocop --auto-correct "$FILE_PATH" 2>&1); then
      echo "  rubocop completed"
      FORMATTED=true
    else
      echo "  rubocop failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  else
    echo "  rubocop not found"
  fi
  ;;

# PHP
php)
  if command -v php-cs-fixer >/dev/null 2>&1; then
    echo "  Running php-cs-fixer..."
    if err=$(php-cs-fixer fix "$FILE_PATH" 2>&1); then
      echo "  php-cs-fixer completed"
      FORMATTED=true
    else
      echo "  php-cs-fixer failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  else
    echo "  php-cs-fixer not found"
  fi
  ;;

# Shell scripts
sh | bash)
  if command -v shfmt >/dev/null 2>&1; then
    echo "  Running shfmt..."
    if err=$(shfmt -i 2 -w "$FILE_PATH" 2>&1); then
      echo "  shfmt completed"
      FORMATTED=true
    else
      echo "  shfmt failed" >&2
      [ -n "$err" ] && echo "$err" >&2
    fi
  else
    echo "  shfmt not found"
  fi
  ;;

*)
  echo "  No formatter configured for .$EXTENSION files"
  ;;
esac

log "DONE: $BASENAME"
echo "Formatting completed for $BASENAME"

# フォーマッターが実行された場合のみ通知（対象外の拡張子では通知しない）
if $FORMATTED; then
  notify "Format Done" "$BASENAME をフォーマットしました" 4
fi

exit 0
