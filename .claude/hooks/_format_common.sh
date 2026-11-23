#!/bin/bash
# _format_common.sh
# auto-format.sh が共有する言語別 フォーマッタマトリクス。
#
# このファイルが唯一の実体。.codex/hooks/ 側には複製もリンクも無く、あちらの
# auto-format.sh が cd -P で ../../.claude/hooks を解決して直接読む。編集はここだけ。
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

# ■ プロジェクトの設定を尊重する (他人のリポジトリを既定スタイルで書き換えない)
#
# このフックは自分のリポジトリだけでなく、作業中のあらゆるリポジトリで走る。設定の
# 無いリポジトリで既定スタイルの整形を掛けると、編集と無関係な差分がファイル全体に
# 出て、他人のコードを汚す。そのため:
#   - prettier   : 編集ファイルから git root まで遡って設定が見つかったときだけ走る
#                  (.prettierrc* / prettier.config.* / package.json の "prettier" キー)
#   - clang-format: .clang-format / _clang-format が見つかったときだけ走る
#   - shfmt      : .editorconfig がインデントを決めているときはスタイルフラグを渡さない
#                  (shfmt は `-i` 等が無いときだけ .editorconfig を読み、渡すと無視する)。
#                  無いときは従来どおり `-i 2`
# 設定が無くて走らなかった理由は SKIP_NOTE として DONE のログに残す。
#
# symlink の対象は整形しない: shfmt は symlink を通常ファイルに置き換えることを拒否し、
# prettier も "is a symbolic link" で拒否する。拒否されて失敗するだけでなく、ログが
# 「未導入」と誤記されるので、先に「symlink のため skipped」と明示して抜ける。
# symlink 先を整形する案は採らない: 先が別のリポジトリや編集と無関係な場所にあると、
# そこの設定も確認せずに書き換えることになる。

# _format_prettier_config_exists <start_dir> <root_dir>
#
# <start_dir> から <root_dir> まで遡って prettier の設定があれば 0。package.json は
# "prettier" キーを持つ場合だけ設定とみなす: devDependencies に prettier があるだけ
# (ほぼ全ての prettier 導入済みプロジェクト) では設定ではなく、そこで探索も止めない
# (monorepo は親に設定がある)。package.json は厳密な JSON なので jq で見る。
_format_prettier_config_exists() {
  local dir root name
  dir=$(cd "$1" 2>/dev/null && pwd -P) || return 1
  root=$(cd "$2" 2>/dev/null && pwd -P) || return 1
  while [ -n "$dir" ]; do
    for name in .prettierrc .prettierrc.json .prettierrc.yaml .prettierrc.yml .prettierrc.json5 \
      .prettierrc.js .prettierrc.cjs .prettierrc.mjs .prettierrc.ts .prettierrc.cts .prettierrc.mts \
      .prettierrc.toml prettier.config.js prettier.config.cjs prettier.config.mjs \
      prettier.config.ts prettier.config.cts prettier.config.mts; do
      [ -f "$dir/$name" ] && return 0
    done
    if [ -f "$dir/package.json" ] && command -v jq >/dev/null 2>&1 &&
      jq -e 'has("prettier")' "$dir/package.json" >/dev/null 2>&1; then
      return 0
    fi
    [ "$dir" = "$root" ] && return 1
    [ "$dir" = "/" ] && return 1
    dir=$(dirname "$dir")
  done
  return 1
}

# _format_editorconfig_sets_indent <file>
#
# <file> から上へ、.editorconfig が (root = true で止まるまで) シェルスクリプトの
# インデントを決めているとき 0。`[*]` / `[**]` か、sh / bash を含む節にある
# indent_style / indent_size / tab_width だけを数える: 別言語の節 (`[*.py]`) や、
# インデントを決めない .editorconfig (charset だけ等) を理由にスタイルフラグを外すと、
# shfmt は既定のタブ整形に落ちて、ファイル全体を書き換えてしまう。
_format_editorconfig_sets_indent() {
  local dir ec
  dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd -P) || return 1
  while [ -n "$dir" ]; do
    ec="$dir/.editorconfig"
    if [ -f "$ec" ]; then
      if awk '
        /^[[:space:]]*\[/ {
          sect = $0
          sub(/^[[:space:]]*\[/, "", sect)
          sub(/\][[:space:]]*$/, "", sect)
          ok = (sect == "*" || sect == "**" || sect ~ /(^|[^[:alnum:]])(sh|bash)([^[:alnum:]]|$)/)
          next
        }
        ok && /^[[:space:]]*(indent_style|indent_size|tab_width)[[:space:]]*[=:]/ { found = 1 }
        END { exit(found ? 0 : 1) }
      ' "$ec" 2>/dev/null; then
        return 0
      fi
      grep -qiE '^[[:space:]]*root[[:space:]]*=[[:space:]]*true' "$ec" 2>/dev/null && return 1
    fi
    [ "$dir" = "/" ] && return 1
    dir=$(dirname "$dir")
  done
  return 1
}

# hook_format_file <file> <log_file>
#
# 1 ファイルを整形する。フォーマッターが実行され成功したら 0、未導入・対象外
# 拡張子・設定が無い・symlink・失敗なら 1 を返す(いずれも「フックとしては正常」で
# あることに注意)。

hook_format_file() {
  local FILE_PATH="$1"
  local hook_log_file="$2"
  local EXTENSION="${FILE_PATH##*.}"
  local BASENAME
  BASENAME=$(basename "$FILE_PATH")
  local FORMATTED=false # フォーマッターが実際に実行され、かつ成功したか
  local err
  local PRETTIER_BIN PROJECT_ROOT
  local SKIP_NOTE="" shfmt_args=()

  hook_log "$hook_log_file" "--- format start: $FILE_PATH ---"
  echo "Auto-formatting: $BASENAME"

  # symlink は整形しない (理由は上の「プロジェクトの設定を尊重する」節)。ここで抜けないと、
  # 下の DONE が「未導入か対象外拡張子」と書いて、診断を誤らせる。
  if [ -L "$FILE_PATH" ]; then
    echo "  Skipped (symlink): formatters refuse to replace a symlink"
    hook_log "$hook_log_file" "DONE: $BASENAME (skipped (symlink): not formatted; the target of the link is not touched)"
    return 1
  fi

  case "$EXTENSION" in
  # JavaScript/TypeScript/JSON/CSS/HTML/Markdown
  js | jsx | ts | tsx | json | css | scss | less | html | htm | md | yaml | yml)
    # ローカル解決: 編集ファイルのディレクトリから git root まで遡って一番
    # 近い node_modules/.bin/prettier を探す (hook_find_nearest_bin、
    # _hook_common.sh)。`command -v prettier` だけでは (a) prettier がプロジェクトの
    # node_modules にしか無い (= 標準的な devDependency 導入) と
    # 「Prettier not found」のまま未整形で抜け、(b) ローカルと PATH の両方に
    # あると PATH 側が勝ってプロジェクトの固定バージョンを無視する。
    # git リポジトリの外では遡る境界が無いので、編集ファイル自身のディレクトリ
    # 1 段だけを見て、それ以降は PATH にフォールバックする(無制限に遡ると
    # 無関係な node_modules まで拾いかねないため)。
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
    [ -z "$PROJECT_ROOT" ] && PROJECT_ROOT=$(dirname "$FILE_PATH")
    PRETTIER_BIN=$(hook_find_nearest_bin "$(dirname "$FILE_PATH")" "$PROJECT_ROOT" prettier)
    if [ -z "$PRETTIER_BIN" ] && command -v prettier >/dev/null 2>&1; then
      PRETTIER_BIN="prettier"
    fi
    if [ -n "$PRETTIER_BIN" ]; then
      if _format_prettier_config_exists "$(dirname "$FILE_PATH")" "$PROJECT_ROOT"; then
        echo "  Running Prettier ($PRETTIER_BIN)..."
        if err=$("$PRETTIER_BIN" --write "$FILE_PATH" 2>&1); then
          echo "  Prettier completed"
          FORMATTED=true
        else
          echo "  Prettier failed" >&2
          [ -n "$err" ] && echo "$err" >&2
        fi
      else
        echo "  Prettier skipped: no prettier config found up to $PROJECT_ROOT"
        SKIP_NOTE="skipped: no prettier config (.prettierrc*, prettier.config.*, package.json \"prettier\") up to $PROJECT_ROOT"
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
      # --force-exclude が必須: ruff は明示的に渡したパスには `exclude` /
      # `extend-exclude` を適用しないため、フラグ無しだとプロジェクトが除外した
      # ファイル (生成物・vendor 等) まで書き換えてしまう。除外されたファイルは
      # ruff が何もせず終了コード 0 で返すので、下の分岐ではそのまま「整形済み」と
      # して扱われる (エラーにならない)。lint 側 (_lint_common.sh) と必ず揃える。
      ruff check --select I --fix --force-exclude "$FILE_PATH" >/dev/null 2>&1 || true
      if err=$(ruff format --force-exclude "$FILE_PATH" 2>&1); then
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
      PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
      [ -z "$PROJECT_ROOT" ] && PROJECT_ROOT=$(dirname "$FILE_PATH")
      if [ -n "$(hook_find_nearest_file "$(dirname "$FILE_PATH")" "$PROJECT_ROOT" .clang-format _clang-format)" ]; then
        echo "  Running clang-format..."
        if err=$(clang-format -i "$FILE_PATH" 2>&1); then
          echo "  clang-format completed"
          FORMATTED=true
        else
          echo "  clang-format failed" >&2
          [ -n "$err" ] && echo "$err" >&2
        fi
      else
        echo "  clang-format skipped: no .clang-format / _clang-format found up to $PROJECT_ROOT"
        SKIP_NOTE="skipped: no .clang-format / _clang-format up to $PROJECT_ROOT"
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
      # .editorconfig がインデントを決めているときは、スタイルフラグを渡さず shfmt に
      # 読ませる (`-i 2` を渡すと .editorconfig は無視される)。無いときは従来どおり 2。
      shfmt_args=(-i 2)
      if _format_editorconfig_sets_indent "$FILE_PATH"; then
        shfmt_args=()
        echo "  .editorconfig sets the indent; shfmt reads it"
      fi
      echo "  Running shfmt..."
      if err=$(shfmt "${shfmt_args[@]}" -w "$FILE_PATH" 2>&1); then
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
  elif [ -n "$SKIP_NOTE" ]; then
    hook_log "$hook_log_file" "DONE: $BASENAME ($SKIP_NOTE)"
  else
    hook_log "$hook_log_file" "DONE: $BASENAME (フォーマッター未実行: 未導入か対象外拡張子)"
  fi

  # フォーマッターが走って成功していれば 0、そうでなければ 1 を返す
  $FORMATTED
}
