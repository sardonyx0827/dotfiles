#!/bin/bash
# _hook_common.sh
# lint.sh / auto-format.sh が共有するログ出力とデスクトップ通知。
#
# このファイルが実体で、.codex/hooks/_hook_common.sh は相対 symlink。編集はここだけ。
# 経緯と Codex の symlink 無視バグが適用されない理由は _bash_review_common.py の
# ヘッダを参照(あちらと同じ理屈: このファイルを開くのはシェルであって Codex の
# 設定スキャナではない)。
#
# ■ source 側との契約
#
#   - このファイルは関数定義だけを持つ。source 時に副作用を起こさない:
#     出力しない、mkdir しない、set/shopt/IFS/trap/cwd を触らない。Codex 版の
#     lint.sh は `exec 1>/dev/null` の前後どちらで source しても安全でなければ
#     ならず、また fail-open 設計のフックに `set -e` を持ち込んではならないため。
#   - 関数内では exit せず return する。終了コードの決定は wrapper の責務。
#   - 関数名は hook_、変数名は HOOK_ で名前空間を切る。bash は動的スコープなので、
#     wrapper 側の local と衝突すると静かに壊れる。
#
# ■ なぜログファイルを引数で渡すか
#
# 以前は各 wrapper が $LOG_FILE を暗黙の global として持ち、log() がそれを読んで
# いた。共有すると「どの変数が設定済みでなければならないか」がファイルを跨いで
# 見えなくなるため、明示的に渡す。宛先が .claude/logs と .codex/logs で分かれる
# のは wrapper 側の関心事。

# hook_log <log_file> <message...>
#
# タイムスタンプ付きで追記し、標準出力にも流す(Codex 版は exec 1>/dev/null 済み
# なので実質ログのみ)。行数が上限を超えたら古い行を捨てる。
hook_log() {
  local log_file="$1"
  shift
  local max_lines="${HOOK_LOG_MAX_LINES:-500}"

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$log_file"

  local lines
  lines=$(wc -l <"$log_file" 2>/dev/null) || return 0
  [ "$lines" -gt "$max_lines" ] 2>/dev/null || return 0

  # ローテーションは読んで書き戻す操作なので、フックが並行して走ると衝突する
  # (Claude と Codex のセッションが同時に動く、1 ターンで複数ファイルが処理
  # される、など珍しくない)。
  #
  # 固定名の ${log_file}.tmp を使っていた頃は、2 つのプロセスが同じ中間ファイル
  # を開いて互いの内容を潰し合い、上限 50 行のログが 15 行まで削れた。さらに
  # 先に mv した側に負けたプロセスの `mv: ... No such file or directory` が
  # stderr へ漏れていた。lint.sh の stderr はモデルへの指摘を返す経路なので、
  # これは lint の出力に化ける。
  #
  # プロセスごとに一意な中間ファイルを作れば衝突しない。mv は同一ディレクトリ内
  # なので rename(2) 相当で不可分に差し替わり、ログは常にどちらかの完全な
  # スナップショットになる(競り負けた側の数行が落ちることはあるが、ローテーション
  # とはそういうものなので許容する)。
  #
  # flock は macOS に無いのでロックは使わない。また、ここで何が失敗しても
  # 呼び出し元に影響させない: ログ取りがフックの成否を変えてはならないので、
  # エラーは捨てて必ず 0 で返す。
  local tmp
  tmp=$(mktemp "${log_file}.XXXXXX" 2>/dev/null) || return 0
  if tail -n "$max_lines" "$log_file" >"$tmp" 2>/dev/null; then
    mv "$tmp" "$log_file" 2>/dev/null || rm -f "$tmp" 2>/dev/null
  else
    rm -f "$tmp" 2>/dev/null
  fi
  return 0
}

# hook_notify <title> <message> [timeout_seconds]
#
# macOS は terminal-notifier(表示秒数を指定できる)を優先し、無ければ osascript。
# Linux は notify-send。いずれも無ければ黙って何もしない(通知は付加価値であり、
# フックの成否を左右してはならない)。
hook_notify() {
  local title="$1"
  local message="$2"
  local timeout="${3:-5}"

  if command -v terminal-notifier >/dev/null 2>&1; then
    terminal-notifier -title "$title" -message "$message" -timeout "$timeout" 2>/dev/null
  elif command -v osascript >/dev/null 2>&1; then
    # 値は環境変数経由で渡す。AppleScript のソースに文字列を埋め込むと、
    # ファイル名に " を含むケースでインジェクションになる。
    # system attribute ではなく printenv を使うのは、日本語が MacRoman として
    # 解釈されて文字化けするのを避けるため。
    HOOK_NOTIFY_TITLE="$title" HOOK_NOTIFY_MESSAGE="$message" osascript \
      -e 'set titleText to do shell script "printenv HOOK_NOTIFY_TITLE || true"' \
      -e 'set msgText to do shell script "printenv HOOK_NOTIFY_MESSAGE || true"' \
      -e 'display notification msgText with title titleText' \
      2>/dev/null
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send --expire-time "$((timeout * 1000))" "$title" "$message" 2>/dev/null
  fi
}
