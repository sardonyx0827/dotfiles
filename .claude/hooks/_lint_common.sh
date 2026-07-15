#!/bin/bash
# _lint_common.sh
# lint.sh が共有する言語別 静的解析マトリクス。
#
# このファイルが実体で、.codex/hooks/_lint_common.sh は相対 symlink。編集はここだけ。
# 契約(source 時に副作用を持たない / exit せず return する / hook_ 名前空間)は
# _hook_common.sh のヘッダを参照。hook_log を使うので、source する側は先に
# _hook_common.sh を読み込んでいること。
#
# ■ なぜ wrapper と分けるか
#
# 対象の決め方とプロトコルが .claude 版と .codex 版で本質的に違う:
#   - Claude: PostToolUse で .tool_input.file_path の 1 ファイル
#   - Codex : PostToolUse だが apply_patch のマーカーから複数ファイル。stdout は
#             構造化出力として解釈されるため exec 1>/dev/null で捨てる
# 一方「1 ファイルをどう解析するか」は完全に同一で、以前は約 230 行が
# インデント 1 段違いで両者にコピペされていた(しかも js/ts, rs, go, java, c/c++,
# rb, php はテストが 1 件も無かった)。共有するのはこの解析部分だけで、
# 対象の集約・通知・終了コードの決定は wrapper に残す。
#
# ■ 失敗の判定方法が linter ごとに違う
#
# 大半は終了コードを見るが、clippy / checkstyle / cppcheck は「終了コード 0 の
# まま stdout に指摘を書く」ため出力を grep している。ここを取り違えると
# 「問題を見つけたのに通す」という最悪の壊れ方をするので、
# tests/test_lint_and_format.py::TestLintLanguageMatrix が両者を固定している。

# hook_lint_file <file> <errors_var_name> <log_file>
#
# 1 ファイルを解析する。問題があれば errors_var_name で指定された変数に生の
# エラー文字列を入れて 1 を返す。無ければ空文字を入れて 0 を返す。
# 表示用の整形(ファイル名の見出しや区切り線)は呼び出し元の責務。
#
# 注意: bash は動的スコープなので、errors_var_name にこの関数内の local と同じ
# 名前(LINT_ERRORS 等)を渡すと local 側に書き込まれ、呼び出し元には何も届かない。
# 静かに壊れるため下でガードしている。

hook_lint_file() {
  local FILE_PATH="$1"
  local hook_out_var="$2"
  local hook_log_file="$3"
  local EXTENSION="${FILE_PATH##*.}"
  local BASENAME
  BASENAME=$(basename "$FILE_PATH")
  local LINT_ERRORS=""
  local PROJECT_ROOT HAS_ESLINT_CONFIG ESLINT_BIN CONFIG OUTPUT HAS_MYPY_CONFIG RELATED cfg

  # 出力変数名がこの関数の local と衝突すると、printf -v は local を書き換えて
  # しまい呼び出し元には何も返らない。黙って通るより落とす。
  case "$hook_out_var" in
  FILE_PATH | EXTENSION | BASENAME | LINT_ERRORS | PROJECT_ROOT | OUTPUT | \
    HAS_ESLINT_CONFIG | ESLINT_BIN | CONFIG | HAS_MYPY_CONFIG | RELATED | cfg | \
    hook_out_var | hook_log_file)
    echo "hook_lint_file: output variable '$hook_out_var' collides with an internal local" >&2
    return 2
    ;;
  esac

  hook_log "$hook_log_file" "--- lint start: $FILE_PATH ---"
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
    hook_log "$hook_log_file" "FAILED: $BASENAME"
    printf "%b" "$LINT_ERRORS" >>"$hook_log_file"
    printf -v "$hook_out_var" '%s' "$LINT_ERRORS"
    return 1
  fi

  hook_log "$hook_log_file" "PASSED: $BASENAME"
  echo "All lint checks passed for $BASENAME"
  printf -v "$hook_out_var" '%s' ""
  return 0
}
