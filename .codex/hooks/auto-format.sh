#!/bin/bash
# Codex Hooks の Stop 用自動フォーマットスクリプト
# ターン終了時に、作業ツリーの変更ファイルへフォーマッターを実行する。
#
# ■ なぜ Claude 版と違い PostToolUse ではなく Stop なのか
#
# Claude 版は Write/Edit ごと(PostToolUse)に整形するが、Codex で同じことを
# すると Codex の編集そのものを壊す。Codex の編集ツール apply_patch は
# 「このファイルは今こういう内容のはず」という前提で差分を当てるため、
# 編集直後にフォーマッターがファイルを書き換えると、後続のパッチが
#
#     apply_patch verification failed: Failed to find expected lines
#
# で失敗する。Codex はリトライし、最終的にシェル経由でファイルを書くので
# PostToolUse(matcher: Write|Edit|MultiEdit)にも掛からず、結局未整形の
# まま残る。1 回の編集で終わるターンは無事だが、複数回編集するターンでは
# 再現する(実測で確認済み)。
#
# そのため「Codex がもう編集しないと分かっている時点」= Stop まで整形を
# 遅らせる。lint.sh はファイルを変更しないためこの競合が無く、PostToolUse
# のままで即時フィードバックできる。
#
# ■ 対象ファイルの決め方
#
# Stop の payload にはファイルパスが含まれないため、git の作業ツリー差分
# から対象を決める(stop-audit.sh と同じ方針)。この呼び出しで触ったファイル
# だけを狙うことはできないので、作業中の無関係な変更も整形対象になりうる。
#
# ■ その他の Codex 差分
#
# - Codex はフックの stdout を構造化出力(JSON)として解釈するため、平文を
#   出すとフックが失敗扱いになる。進捗表示は捨て、記録はログに残す。
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、個々の
# フォーマッターの失敗でフック自体を異常終了させてはならない
# (lint.sh / git-push-review.sh と同じ方針)。失敗は if 分岐と `|| true` で
# 個別に処理し、最悪でも exit 0 で抜ける。
#
# Stop の payload は使わない(対象は git 差分から決める)ため、stdin は読まず
# jq にも依存しない。

# -------------------------------------------------------------------
# ログ・通知設定
# -------------------------------------------------------------------
LOG_DIR="$HOME/.codex/logs"
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

# -------------------------------------------------------------------
# 対象ファイルの特定
#
# Stop の payload にはファイルパスが無いため、git の作業ツリー差分から
# 決める(冒頭の「対象ファイルの決め方」を参照)。
# -------------------------------------------------------------------
# 一度に処理する上限。作業ツリーが大きく汚れているときに際限なく走らせない
# ための安全弁(打ち切った場合はログに残す)。
MAX_FILES=50

collect_targets() {
  local repo_root
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
  [ -z "$repo_root" ] && return 0

  # 変更ファイル + 未追跡ファイル(apply_patch は新規作成も行う)。両者は排他
  # なので重複しない。git はリポジトリルート相対のパスを返すため絶対パス化する。
  #
  # -z が必須: 既定の core.quotePath が有効だと、引用符や非 ASCII(日本語の
  # ファイル名など)を含むパスを `"evil\".sh"` のようにクォートして返すため、
  # そのままでは開けず黙って整形対象から漏れる。出力も NUL 区切りにして、
  # 改行を含むファイル名でも壊れないようにする。
  {
    git -C "$repo_root" diff --name-only -z HEAD 2>/dev/null
    git -C "$repo_root" ls-files --others --exclude-standard -z 2>/dev/null
  } | while IFS= read -r -d '' f; do
    [ -n "$f" ] && printf '%s/%s\0' "$repo_root" "$f"
  done
}

# 1 ファイルをフォーマットする。実際にフォーマッターが走って成功したら 0 を返す。
format_file() {
  local FILE_PATH="$1"
  local EXTENSION="${FILE_PATH##*.}"
  local BASENAME
  BASENAME=$(basename "$FILE_PATH")
  local FORMATTED=false # フォーマッターが実際に実行され、かつ成功したか
  local err

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
    log "DONE: $BASENAME (formatted)"
  else
    log "DONE: $BASENAME (フォーマッター未実行: 未導入か対象外拡張子)"
  fi

  # フォーマッターが走って成功していれば 0、そうでなければ 1 を返す
  $FORMATTED
}

# -------------------------------------------------------------------
# メイン: 特定した対象を 1 件ずつフォーマットする
#
# Codex はフックの stdout を構造化出力(hookSpecificOutput の JSON)として
# 解釈するため、平文を出すとフック自体が失敗扱いになる(Claude は平文を
# 許容するのでここが .claude 版との差分)。進捗表示は捨て、記録はログ
# ファイルに残す。stderr は lint.sh の exit 2 フィードバックで使うため残す。
#
# collect_targets の stdout はプロセス置換のパイプに繋がるため、この
# リダイレクトの影響は受けない(対象リストは正しく読める)。
# -------------------------------------------------------------------
exec 1>/dev/null

formatted_count=0
target_count=0
last_basename=""

while IFS= read -r -d '' target; do
  [ -z "$target" ] && continue
  # apply_patch はファイル削除も行うため、実体が無いものは対象外
  [ -f "$target" ] || continue

  target_count=$((target_count + 1))
  if [ "$target_count" -gt "$MAX_FILES" ]; then
    log "SKIP: 対象が $MAX_FILES 件を超えたため以降を打ち切り"
    break
  fi

  if format_file "$target"; then
    formatted_count=$((formatted_count + 1))
    last_basename=$(basename "$target")
  fi
done < <(collect_targets)

# フォーマッターが実行された場合のみ通知（対象外の拡張子では通知しない）
if [ "$formatted_count" -eq 1 ]; then
  notify "Format Done" "$last_basename をフォーマットしました" 4
elif [ "$formatted_count" -gt 1 ]; then
  notify "Format Done" "$formatted_count 件をフォーマットしました" 4
fi

exit 0
