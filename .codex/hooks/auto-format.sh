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
mkdir -p "$LOG_DIR"

# 共有ヘルパー(hook_log / hook_notify)と整形マトリクス(hook_format_file)。
# 実体は .claude/hooks/ 側、こちらは symlink。source する側で `exec 1>/dev/null`
# する前に読み込むため、共有ファイルは source 時に何も出力しない契約。
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../.claude/hooks/_hook_common.sh
. "$HOOK_DIR/_hook_common.sh"
# shellcheck source=../../.claude/hooks/_format_common.sh
. "$HOOK_DIR/_format_common.sh"
if ! declare -F hook_log >/dev/null 2>&1 ||
  ! declare -F hook_format_file >/dev/null 2>&1; then
  echo "auto-format.sh: could not load shared hook helpers from $HOOK_DIR" >&2
  exit 0
fi

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
# 1 ファイル整形は共有マトリクスに委譲する。対象の集約は Codex 固有なのでここ。
format_file() {
  hook_format_file "$1" "$LOG_FILE"
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
    hook_log "$LOG_FILE" "SKIP: 対象が $MAX_FILES 件を超えたため以降を打ち切り"
    break
  fi

  if format_file "$target"; then
    formatted_count=$((formatted_count + 1))
    last_basename=$(basename "$target")
  fi
done < <(collect_targets)

# フォーマッターが実行された場合のみ通知（対象外の拡張子では通知しない）
if [ "$formatted_count" -eq 1 ]; then
  hook_notify "Format Done" "$last_basename をフォーマットしました" 4
elif [ "$formatted_count" -gt 1 ]; then
  hook_notify "Format Done" "$formatted_count 件をフォーマットしました" 4
fi

exit 0
