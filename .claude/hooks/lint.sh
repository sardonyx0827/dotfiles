#!/bin/bash
# Claude Code HooksのPostToolUse用静的解析スクリプト
# auto-format.sh実行後に呼び出すことを想定
# exit 2 でエラー内容をClaudeにフィードバックし、自動修正を促す

# jq が無い環境では処理できないため安全に抜ける
command -v jq >/dev/null 2>&1 || exit 0

# ログ設定
LOG_DIR="$HOME/.claude/logs"
LOG_FILE="$LOG_DIR/lint.log"
MAX_LOG_LINES=500 # これを超えたら古い行を削除
mkdir -p "$LOG_DIR"

# タイムスタンプ付きでログに書き込み、行数が上限を超えたらローテーション
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
  # 行数チェックして超過分を削除（tailで末尾MAX_LOG_LINES行だけ残す）
  local lines
  lines=$(wc -l <"$LOG_FILE")
  if [ "$lines" -gt "$MAX_LOG_LINES" ]; then
    tail -n "$MAX_LOG_LINES" "$LOG_FILE" >"${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
  fi
}

# 通知を送る関数（macOS: terminal-notifier/osascript、Linux: notify-send）
notify() {
  local title="$1"
  local message="$2"
  local timeout="${3:-5}"
  # terminal-notifier が入っていれば長めに表示、なければosascript/notify-sendにフォールバック
  if command -v terminal-notifier >/dev/null 2>&1; then
    # -timeout: 表示秒数（FAILEDは長め、PASSEDは短め）
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

# JSONからファイルパスを取得（auto-format.shと同じ方法）
FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

if [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

EXTENSION="${FILE_PATH##*.}"
BASENAME=$(basename "$FILE_PATH")
LINT_ERRORS=""

log "--- lint start: $FILE_PATH ---"
echo "Linting: $BASENAME"

# 言語別 静的解析

case "$EXTENSION" in

# JavaScript / TypeScript
js | jsx | ts | tsx)
  PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)

  # ESLint設定ファイルの存在確認
  HAS_ESLINT_CONFIG=false
  if [ -n "$PROJECT_ROOT" ]; then
    for cfg in eslint.config.js eslint.config.mjs eslint.config.cjs .eslintrc .eslintrc.js .eslintrc.json .eslintrc.yml; do
      [ -f "$PROJECT_ROOT/$cfg" ] && HAS_ESLINT_CONFIG=true && break
    done
  fi

  ESLINT_BIN=""
  if [ -n "$PROJECT_ROOT" ] && [ -x "$PROJECT_ROOT/node_modules/.bin/eslint" ]; then
    ESLINT_BIN="$PROJECT_ROOT/node_modules/.bin/eslint"
  elif command -v eslint >/dev/null 2>&1; then
    ESLINT_BIN="eslint"
  fi

  if $HAS_ESLINT_CONFIG && [ -n "$ESLINT_BIN" ]; then
    echo "  Running ESLint ($ESLINT_BIN)..."
    if ! OUTPUT=$("$ESLINT_BIN" "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[ESLint]\n${OUTPUT}\n"
    else
      echo "  ESLint passed"
    fi
  elif ! $HAS_ESLINT_CONFIG; then
    echo "  ESLint config not found, skipping"
  else
    echo "  ESLint not found"
  fi

  # TypeScriptの型チェック（tsconfig.jsonが存在する場合のみ）
  if [[ "$EXTENSION" == "ts" || "$EXTENSION" == "tsx" ]]; then
    if [ -n "$PROJECT_ROOT" ] && [ -f "$PROJECT_ROOT/tsconfig.json" ]; then
      if command -v tsc >/dev/null 2>&1; then
        echo "  Running tsc (type check)..."
        if ! OUTPUT=$(cd "$PROJECT_ROOT" && tsc --noEmit 2>&1); then
          # 変更ファイルに関連するエラーのみ抽出
          RELATED=$(echo "$OUTPUT" | grep "$BASENAME")
          if [ -n "$RELATED" ]; then
            LINT_ERRORS="${LINT_ERRORS}[TypeScript]\n${RELATED}\n"
          fi
        else
          echo "  tsc passed"
        fi
      fi
    fi
  fi
  ;;

# Python
py)
  if command -v ruff >/dev/null 2>&1; then
    echo "  Running ruff check..."
    if ! OUTPUT=$(ruff check "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[ruff]\n${OUTPUT}\n"
    else
      echo "  ruff passed"
    fi
  else
    echo "  ruff not found"
  fi

  if command -v bandit >/dev/null 2>&1; then
    echo "  Running bandit (security)..."
    # -ll: 中程度以上の重大度のみ報告
    if ! OUTPUT=$(bandit -ll -q "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[bandit - security]\n${OUTPUT}\n"
    else
      echo "  bandit passed"
    fi
  else
    echo "  bandit not found"
  fi

  # mypy: 型チェック（mypy.iniかpyproject.tomlがある場合のみ）
  PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
  HAS_MYPY_CONFIG=false
  if [ -n "$PROJECT_ROOT" ]; then
    [ -f "$PROJECT_ROOT/mypy.ini" ] && HAS_MYPY_CONFIG=true
    [ -f "$PROJECT_ROOT/pyproject.toml" ] && grep -q "\[tool.mypy\]" "$PROJECT_ROOT/pyproject.toml" && HAS_MYPY_CONFIG=true
  fi
  if $HAS_MYPY_CONFIG && command -v mypy >/dev/null 2>&1; then
    echo "  Running mypy (type check)..."
    if ! OUTPUT=$(mypy "$FILE_PATH" --ignore-missing-imports 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[mypy]\n${OUTPUT}\n"
    else
      echo "  mypy passed"
    fi
  fi
  ;;

# Rust
rs)
  # clippy: Rustの公式linter（cargoが必要）
  PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
  if [ -n "$PROJECT_ROOT" ] && [ -f "$PROJECT_ROOT/Cargo.toml" ]; then
    if command -v cargo >/dev/null 2>&1; then
      echo "  Running cargo clippy..."
      OUTPUT=$(cd "$PROJECT_ROOT" && cargo clippy --quiet 2>&1)
      if echo "$OUTPUT" | grep -q "^error"; then
        LINT_ERRORS="${LINT_ERRORS}[clippy]\n${OUTPUT}\n"
      else
        echo "  cargo clippy passed"
      fi
    fi
  else
    echo "  Cargo.toml not found, skipping clippy"
  fi
  ;;

# Go
go)
  if command -v go >/dev/null 2>&1; then
    echo "  Running go vet..."
    if ! OUTPUT=$(go vet "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[go vet]\n${OUTPUT}\n"
    else
      echo "  go vet passed"
    fi
  fi

  if command -v staticcheck >/dev/null 2>&1; then
    echo "  Running staticcheck..."
    if ! OUTPUT=$(staticcheck "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[staticcheck]\n${OUTPUT}\n"
    else
      echo "  staticcheck passed"
    fi
  else
    echo "  staticcheck not found (optional)"
  fi
  ;;

# Java
java)
  if command -v checkstyle >/dev/null 2>&1; then
    echo "  Running checkstyle..."
    # プロジェクトにcheckstyle.xmlがあればそれを使用、なければGoogle規約
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
    CONFIG="google"
    [ -f "$PROJECT_ROOT/checkstyle.xml" ] && CONFIG="$PROJECT_ROOT/checkstyle.xml"
    OUTPUT=$(checkstyle -c "$CONFIG" "$FILE_PATH" 2>&1)
    if echo "$OUTPUT" | grep -q "\[ERROR\]"; then
      LINT_ERRORS="${LINT_ERRORS}[checkstyle]\n${OUTPUT}\n"
    else
      echo "  checkstyle passed"
    fi
  else
    echo "  checkstyle not found"
  fi
  ;;

# C / C++
c | cpp | cc | cxx | h | hpp)
  if command -v cppcheck >/dev/null 2>&1; then
    echo "  Running cppcheck..."
    OUTPUT=$(cppcheck --enable=warning,style,performance,portability \
      --suppress=missingInclude \
      "$FILE_PATH" 2>&1)
    if echo "$OUTPUT" | grep -qE "\(error\)|\(warning\)"; then
      LINT_ERRORS="${LINT_ERRORS}[cppcheck]\n${OUTPUT}\n"
    else
      echo "  cppcheck passed"
    fi
  else
    echo "  cppcheck not found"
  fi
  ;;

# Ruby
rb)
  # rubocop: フォーマットとlintを兼ねる（auto-format.shでは--auto-correctのみ実行済み）
  # ここでは修正できなかった残存エラーをClaudeにフィードバック
  if command -v rubocop >/dev/null 2>&1; then
    echo "  Running rubocop (lint only)..."
    if ! OUTPUT=$(rubocop --no-color "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[rubocop]\n${OUTPUT}\n"
    else
      echo "  rubocop passed"
    fi
  else
    echo "  rubocop not found"
  fi
  ;;

# PHP
php)
  if command -v phpstan >/dev/null 2>&1; then
    echo "  Running phpstan..."
    if ! OUTPUT=$(phpstan analyse "$FILE_PATH" --no-progress 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[phpstan]\n${OUTPUT}\n"
    else
      echo "  phpstan passed"
    fi
  # php -l: 構文チェックのみ（フォールバック）
  elif command -v php >/dev/null 2>&1; then
    echo "  Running php -l (syntax check)..."
    if ! OUTPUT=$(php -l "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[php syntax]\n${OUTPUT}\n"
    else
      echo "  php syntax OK"
    fi
  else
    echo "  phpstan / php not found"
  fi
  ;;

# Shell scripts
sh | bash)
  if command -v shellcheck >/dev/null 2>&1; then
    echo "  Running shellcheck..."
    if ! OUTPUT=$(shellcheck "$FILE_PATH" 2>&1); then
      LINT_ERRORS="${LINT_ERRORS}[shellcheck]\n${OUTPUT}\n"
    else
      echo "  shellcheck passed"
    fi
  else
    echo "  shellcheck not found"
  fi
  ;;

*)
  echo "  No linter configured for .$EXTENSION files"
  ;;
esac

# エラーがあればClaudeにフィードバック (exit 2)

if [ -n "$LINT_ERRORS" ]; then
  log "FAILED: $BASENAME"
  printf "%b" "$LINT_ERRORS" >>"$LOG_FILE"
  notify "Lint Failed" "$BASENAME に問題が見つかりました" 15

  echo "" >&2
  echo "Lint errors found in $BASENAME:" >&2
  echo "---" >&2
  printf "%b" "$LINT_ERRORS" >&2
  echo "---" >&2
  echo "Please fix the above issues." >&2
  exit 2 # Claudeが内容を読んで自動修正を試みる
fi

log "PASSED: $BASENAME"
notify "Lint Passed" "$BASENAME のチェックが完了しました" 5
echo "All lint checks passed for $BASENAME"
exit 0
