#!/bin/bash
# Codex Hooks の PostToolUse 用静的解析スクリプト
# exit 2 でエラー内容をCodexにフィードバックし、自動修正を促す
#
# ■ auto-format.sh とはフックの掛かる場所が違う
#
# Claude 版は auto-format → lint を両方 PostToolUse に並べるが、Codex 版で
# は auto-format だけ Stop に移してある。フォーマッターがファイルを書き換え
# ると Codex の apply_patch が「期待した行が無い」と失敗するため(詳細は
# auto-format.sh の冒頭)。lint はファイルを変更しないのでこの競合が無く、
# PostToolUse に置いて即時フィードバックできる。
#
# したがって「整形済みの状態を lint する」という前提は Codex 版では成立
# しない。整形前のコードを解析することになるが、フォーマッターで直る類の
# 指摘(インデント等)は Stop 時に解消されるため実害は小さい。
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、個々の
# linter が未導入/実行失敗でも他の言語のチェックやスクリプト全体を止めては
# ならない。各コマンドの失敗は `if ! OUTPUT=$(...)` / `command -v` チェックで
# 個別に処理し、最悪でも exit 0 で抜ける。

# jq が無い環境では処理できないため安全に抜ける
command -v jq >/dev/null 2>&1 || exit 0

# -------------------------------------------------------------------
# 共有ヘルパー(hook_log / hook_notify)
#
# 実体は .claude/hooks/_hook_common.sh で、こちらの _hook_common.sh はそこへの
# symlink。source 側で `exec 1>/dev/null` する前に読み込むため、共有ファイルは
# source 時に何も出力しない契約になっている(詳細は _hook_common.sh のヘッダ)。
# -------------------------------------------------------------------
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../.claude/hooks/_hook_common.sh
. "$HOOK_DIR/_hook_common.sh"
# 読み込めていなければ fail-open で抜ける。checkout で symlink がテキスト化した
# 場合(core.symlinks=false)もここに落ちる。黙って進むと macOS では `log` が
# /usr/bin/log に解決されてしまい、記録が静かにシステムログへ消える。
if ! declare -F hook_log >/dev/null 2>&1; then
  echo "lint.sh: could not load _hook_common.sh from $HOOK_DIR" >&2
  exit 0
fi

# -------------------------------------------------------------------
# ログ設定
# -------------------------------------------------------------------
LOG_DIR="$HOME/.codex/logs"
LOG_FILE="$LOG_DIR/lint.log"
mkdir -p "$LOG_DIR"

# -------------------------------------------------------------------
# 対象ファイルの特定
#
# Claude の Write/Edit/MultiEdit は .tool_input.file_path に単一ファイルを
# 入れて渡すが、Codex の編集ツール apply_patch はこのキーを持たず、かつ
# 1 回の呼び出しで複数ファイルを変更しうる。そのため file_path が取れない
# 場合は git の作業ツリー差分から対象を復元する(auto-format.sh と同じ方針)。
# -------------------------------------------------------------------
INPUT=$(cat)

# git 差分フォールバック時に一度に処理する上限(打ち切った場合はログに残す)
MAX_FALLBACK_FILES=50

# 全対象ファイル分のエラーを集約する(最後にまとめて exit 2 で返す)
ALL_ERRORS=""
FAILED_COUNT=0
PASSED_COUNT=0
LAST_BASENAME=""

# パッチ内のパスは cwd 相対で入ってくるため絶対パスに直す。
# 対象リストは NUL 区切りで受け渡す(改行を含むファイル名でも壊れないように)。
resolve_path() {
  local p="$1" root
  case "$p" in
  /*)
    printf '%s\0' "$p"
    return 0
    ;;
  esac
  if [ -f "$PWD/$p" ]; then
    printf '%s\0' "$PWD/$p"
    return 0
  fi
  root=$(git rev-parse --show-toplevel 2>/dev/null)
  if [ -n "$root" ] && [ -f "$root/$p" ]; then
    printf '%s\0' "$root/$p"
    return 0
  fi
  # 解決できなければそのまま返す(呼び出し元の -f チェックで落ちる)
  printf '%s\0' "$p"
}

collect_targets() {
  local file_path
  file_path=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

  # Claude の Write/Edit/MultiEdit 経路: 単一ファイルが直接渡ってくる
  if [ -n "$file_path" ]; then
    printf '%s\0' "$file_path"
    return 0
  fi

  # Codex の apply_patch 経路。tool_input.command にパッチ本文が入っているので
  # マーカーから対象を取る。git 差分で代用すると、この呼び出しと無関係な作業中
  # の変更まで解析対象になり、既存のエラーで exit 2 して Codex を無関係な修正に
  # 走らせてしまうため、ここは正確に絞る必要がある。
  local cmd
  cmd=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

  case "$cmd" in
  *"*** Begin Patch"*)
    # Delete File は対象外(消えたファイルは解析しない)。Move to は移動後を解析。
    printf '%s' "$cmd" | sed -n \
      -e 's/^\*\*\* Update File: //p' \
      -e 's/^\*\*\* Add File: //p' \
      -e 's/^\*\*\* Move to: //p' |
      while IFS= read -r p; do
        [ -n "$p" ] && resolve_path "$p"
      done
    return 0
    ;;
  esac

  # ここに来るのは未知の payload 形状。log は tee で stdout にも出るため、
  # 対象リストを汚さないようログはファイルにだけ残す。
  local tool_name
  tool_name=$(printf '%s' "$INPUT" | jq -r '.tool_name // "?"' 2>/dev/null)
  hook_log "$LOG_FILE" "unknown payload: tool_name=$tool_name -> git 差分にフォールバック" >/dev/null

  local repo_root
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
  [ -z "$repo_root" ] && return 0

  # 変更ファイル + 未追跡ファイル。両者は排他なので重複しない。
  # -z が必須: 既定の core.quotePath が有効だと、引用符や非 ASCII(日本語の
  # ファイル名など)を含むパスを `"evil\".sh"` のようにクォートして返すため、
  # そのままでは開けず黙って対象から漏れる。
  {
    git -C "$repo_root" diff --name-only -z HEAD 2>/dev/null
    git -C "$repo_root" ls-files --others --exclude-standard -z 2>/dev/null
  } | while IFS= read -r -d '' f; do
    [ -n "$f" ] && printf '%s/%s\0' "$repo_root" "$f"
  done
}

# -------------------------------------------------------------------
# 言語別 静的解析
#
# 1 ファイルを解析する。問題が見つかったら ALL_ERRORS に積んで 1 を返す。
# -------------------------------------------------------------------
lint_file() {
  local FILE_PATH="$1"
  local EXTENSION="${FILE_PATH##*.}"
  local BASENAME
  BASENAME=$(basename "$FILE_PATH")
  local LINT_ERRORS=""
  local PROJECT_ROOT HAS_ESLINT_CONFIG ESLINT_BIN CONFIG OUTPUT HAS_MYPY_CONFIG RELATED cfg

  hook_log "$LOG_FILE" "--- lint start: $FILE_PATH ---"
  echo "Linting: $BASENAME"

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
    # ruff: flake8/isort/pyupgrade互換の高速オールインワンlinter
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

    # bandit: セキュリティ脆弱性の検出
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

    # staticcheck: go vet より高度な解析
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
    # checkstyle: コーディング規約チェック
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
    # ここでは修正できなかった残存エラーをCodexにフィードバック
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
    # phpstan: 型推論ベースの高精度静的解析
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
      if ! OUTPUT=$(shellcheck -x -P SCRIPTDIR "$FILE_PATH" 2>&1); then
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

  if [ -n "$LINT_ERRORS" ]; then
    hook_log "$LOG_FILE" "FAILED: $BASENAME"
    printf "%b" "$LINT_ERRORS" >>"$LOG_FILE"
    # 呼び出し元でまとめて stderr に出すため、ここでは積むだけにする
    ALL_ERRORS="${ALL_ERRORS}Lint errors found in ${BASENAME}:\n---\n${LINT_ERRORS}---\n"
    return 1
  fi

  hook_log "$LOG_FILE" "PASSED: $BASENAME"
  echo "All lint checks passed for $BASENAME"
  return 0
}

# -------------------------------------------------------------------
# メイン: 特定した対象を 1 件ずつ解析する
#
# Codex はフックの stdout を構造化出力(hookSpecificOutput の JSON)として
# 解釈するため、平文を出すとフック自体が失敗扱いになる(Claude は平文を
# 許容するのでここが .claude 版との差分)。進捗表示は捨て、記録はログ
# ファイルに残す。エラー内容は exit 2 + stderr で Codex に返すため、
# stderr は閉じない。
# -------------------------------------------------------------------
exec 1>/dev/null

target_count=0

while IFS= read -r -d '' target; do
  [ -z "$target" ] && continue
  # apply_patch はファイル削除も行うため、実体が無いものは対象外
  [ -f "$target" ] || continue

  target_count=$((target_count + 1))
  if [ "$target_count" -gt "$MAX_FALLBACK_FILES" ]; then
    hook_log "$LOG_FILE" "SKIP: 対象が $MAX_FALLBACK_FILES 件を超えたため以降を打ち切り"
    break
  fi

  LAST_BASENAME=$(basename "$target")
  if lint_file "$target"; then
    PASSED_COUNT=$((PASSED_COUNT + 1))
  else
    FAILED_COUNT=$((FAILED_COUNT + 1))
  fi
done < <(collect_targets)

# -------------------------------------------------------------------
# エラーがあればCodexにフィードバック (exit 2)
# -------------------------------------------------------------------

if [ -n "$ALL_ERRORS" ]; then
  if [ "$FAILED_COUNT" -eq 1 ]; then
    hook_notify "Lint Failed" "$LAST_BASENAME に問題が見つかりました" 15
  else
    hook_notify "Lint Failed" "$FAILED_COUNT 件のファイルに問題が見つかりました" 15
  fi

  echo "" >&2
  printf "%b" "$ALL_ERRORS" >&2
  echo "Please fix the above issues." >&2
  exit 2 # Codexが内容を読んで自動修正を試みる
fi

# 解析対象が 1 件も無かった場合は通知しない(対象外の拡張子など)
if [ "$PASSED_COUNT" -eq 1 ]; then
  hook_notify "Lint Passed" "$LAST_BASENAME のチェックが完了しました" 5
elif [ "$PASSED_COUNT" -gt 1 ]; then
  hook_notify "Lint Passed" "$PASSED_COUNT 件のチェックが完了しました" 5
fi
exit 0
