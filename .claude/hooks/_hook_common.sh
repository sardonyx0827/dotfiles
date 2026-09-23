#!/bin/bash
# _hook_common.sh
# lint.sh / auto-format.sh が共有するログ出力・デスクトップ通知・ローカル
# node_modules/.bin 解決。
#
# このファイルが唯一の実体。.codex/hooks/ 側には複製もリンクも無く、あちらの
# lint.sh / auto-format.sh が cd -P で ../../.claude/hooks を解決して直接読む。
# 編集はここだけ。
# 実体を 1 つにしてドリフトを構造的に防ぐ経緯は _bash_review_common.py のヘッダを参照。
#
# ■ source 側との契約
#
#   - このファイルは関数定義だけを持つ。source 時に副作用を起こさない:
#     出力しない、mkdir しない、set/shopt/IFS/trap/cwd を触らない。Codex 版の
#     lint.sh は `exec 1>/dev/null` の前後どちらで source しても安全でなければ
#     ならず、また fail-open 設計のフックに `set -e` を持ち込んではならないため。
#   - 関数内では exit せず return する。終了コードの決定は wrapper の責務。
#   - 関数名は hook_ で名前空間を切るが、変数名は統一されていない
#     (hook_lint_file/hook_format_file 内部の FILE_PATH/EXTENSION/BASENAME/
#     LINT_ERRORS 等は無プレフィックス)。bash は動的スコープなので、呼び出し元
#     の変数名と衝突すると静かに壊れる。衝突対策は命名規則ではなく、
#     hook_lint_file 内の禁止名リストで個別に行っている。
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

# hook_find_nearest_bin <start_dir> <root_dir> <bin_name>
#
# <start_dir> から <root_dir> まで遡りながら node_modules/.bin/<bin_name> を
# 探し、最初に見つかった (= 呼び出し元に最も近い) 実行可能ファイルの絶対パスを
# 標準出力に書いて 0 を返す。見つからなければ何も書かず 1 を返す。
#
# ESLint (_lint_common.sh) と Prettier (_format_common.sh) が同じ形の壊れ方を
# していた: ローカルのバイナリを PROJECT_ROOT (または git root) 直下でしか
# 見ておらず、npm ワークスペース/monorepo で実体が packages/<pkg>/node_modules
# にしかない構成では見つからないまま PATH へフォールバックし、グローバル未導入
# なら「見つからない」まま静かにスキップしていた (ESLint は「見つからない」を
# 素通しして exit 0、Prettier はさらに、グローバル版が PATH にあるとそちらの
# バージョンで整形してプロジェクトの固定版を無視する)。ESLint 設定ファイル探索
# (_lint_common.sh の eslint_dir/eslint_root ループ) と同じ「近い方が勝つ」規則
# で歩く。
#
# 呼び出しは必ず `X=$(hook_find_nearest_bin ...)` の形を取ること。command
# substitution はサブシェルで実行されるため、この関数内の変数名が何であれ
# 呼び出し元の変数と衝突しない (hook_lint_file の printf -v 方式とは違い、
# 出力が変数名越しではなく stdout 越しなので、動的スコープの衝突問題自体が
# 起きない。hook_lint_file 側の禁止名リストを更新する必要もない)。
#
# 物理パスで判定するのは _go_dir_has_analyzable_package や eslint_root と同じ
# 理由: symlink 経由の論理パスのままだと <root_dir> との文字列比較がずれ、
# ループが root に到達したと判定できず "/" まで遡り続ける。
hook_find_nearest_bin() {
  local dir root name candidate
  dir=$(cd "$1" 2>/dev/null && pwd -P) || return 1
  root=$(cd "$2" 2>/dev/null && pwd -P) || return 1
  name="$3"
  while [ -n "$dir" ]; do
    candidate="$dir/node_modules/.bin/$name"
    if [ -x "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
    [ "$dir" = "$root" ] && return 1
    [ "$dir" = "/" ] && return 1
    dir=$(dirname "$dir")
  done
  return 1
}
