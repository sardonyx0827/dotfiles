#!/bin/bash
# _format_common.sh
# auto-format.sh が共有する言語別 フォーマッタマトリクス。
#
# このファイルが実体で、.codex/hooks/_format_common.sh は相対 symlink。編集はここだけ。
# 契約(source 時に副作用を持たない / exit せず return する / hook_ 名前空間)は
# _hook_common.sh のヘッダを参照。hook_log を使うので、source する側は先に
# _hook_common.sh を読み込んでいること。
#
# ■ なぜ wrapper と分けるか
#
# 「1 ファイルをどう整形するか」は両者で完全に同一だが、いつ何を対象にするかが違う:
#   - Claude: PostToolUse で .tool_input.file_path の 1 ファイル
#   - Codex : Stop で git の作業ツリーから複数ファイル。PostToolUse に置くと
#             整形でファイルが変わり apply_patch が「期待した行が無い」と失敗する
#             (経緯は .codex/hooks/auto-format.sh の冒頭)
#
# ■ fail-open
#
# フォーマッターの失敗はユーザーの編集を巻き添えにしてはならないので、
# 個々の失敗は stderr に出すだけで握り、フック全体は 0 で抜ける。
# この関数は「整形が実際に走って成功したか」だけを終了コードで返し、
# それを通知に使うかどうかは wrapper が決める。

# hook_format_file <file> <log_file>
#
# 1 ファイルを整形する。フォーマッターが実行され成功したら 0、未導入・対象外
# 拡張子・失敗なら 1 を返す(いずれも「フックとしては正常」であることに注意)。

hook_format_file() {
  local FILE_PATH="$1"
  local hook_log_file="$2"
  local EXTENSION="${FILE_PATH##*.}"
  local BASENAME
  BASENAME=$(basename "$FILE_PATH")
  local FORMATTED=false # フォーマッターが実際に実行され、かつ成功したか
  local err

  hook_log "$hook_log_file" "--- format start: $FILE_PATH ---"
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
    # ruff (import整列 + フォーマッター)
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

  # stdout は捨てているため、フォーマッターが実際に走ったのか、それとも
  # 見つからず素通りしたのかはログにしか残らない。両者を必ず区別する
  # (でないと「DONE なのに整形されていない」が診断不能になる)。
  if $FORMATTED; then
    hook_log "$hook_log_file" "DONE: $BASENAME (formatted)"
  else
    hook_log "$hook_log_file" "DONE: $BASENAME (フォーマッター未実行: 未導入か対象外拡張子)"
  fi

  # フォーマッターが走って成功していれば 0、そうでなければ 1 を返す
  $FORMATTED
}
