#!/bin/bash
# _hook_common.sh
# lint.sh / auto-format.sh / stop-audit.sh が共有するログ出力・デスクトップ通知・
# ローカル node_modules/.bin 解決・設定ファイルの近い順探索・git 作業ツリーの変更
# ファイル選択と追加行の抽出・出力の上限・portable な timeout。
#
# このファイルが唯一の実体。.codex/hooks/ 側には複製もリンクも無く、あちらの
# lint.sh / auto-format.sh / stop-audit.sh が cd -P で ../../.claude/hooks を
# 解決して直接読む。編集はここだけ。
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
# 暗黙の global ($LOG_FILE) だと、共有したときに「どの変数が設定済みでなければ
# ならないか」がファイルを跨いで見えなくなるため、明示的に渡す。宛先が
# .claude/logs と .codex/logs で分かれるのは wrapper 側の関心事。

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
  # される、など珍しくない)。固定名の中間ファイルだと、2 つのプロセスが互いの
  # 内容を潰し合ってログが大きく削れ、先に mv した側に負けたプロセスの
  # `mv: ... No such file or directory` が stderr へ漏れる。lint.sh の stderr は
  # モデルへの指摘を返す経路なので、それは lint の出力に化ける。
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
# ESLint (_lint_common.sh) と Prettier (_format_common.sh) が使う。ローカルの
# バイナリを PROJECT_ROOT (または git root) 直下でしか見ないと、npm ワーク
# スペース/monorepo のように実体が packages/<pkg>/node_modules にしかない構成
# では見つからず、PATH へフォールバックしてしまう。グローバル未導入なら静かに
# スキップし (ESLint は exit 0)、Prettier はグローバル版があればそちらの
# バージョンで整形してプロジェクトの固定版を無視する。ESLint 設定ファイル探索
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

# hook_git_changed_files <repo_root>
#
# 「このターンで変わったファイル」= 作業ツリーの変更 + 未追跡ファイルを、
# <repo_root> 相対のパスとして NUL 区切りで標準出力に書く。常に 0 を返す
# (git リポジトリでない・git が失敗した場合は何も書かない)。
#
# Stop の payload にはファイルパスが無いので、Stop フック (stop-audit.sh、
# Codex の auto-format.sh、lint.sh の git 差分フォールバック) は対象を git から
# 決める。その選び方を 1 箇所にまとめるための関数。以前は各フックが
# `git diff --name-only -z HEAD` を個別に持ち、unborn branch への対応が
# stop-audit.sh にだけ入って、Codex の 2 つには入らないまま残っていた。
#
# 最初のコミット前 (`git init` 直後の unborn branch) は HEAD が解決できず、
# `diff --name-only -z HEAD` が "ambiguous argument 'HEAD'" で失敗する。しかも
# 最初のコミット用にステージした内容は `ls-files --others` (未追跡用) の対象外
# なので、どちらのコマンドにも現れずに黙って対象から漏れる (見逃すゲートは、
# うるさいゲートより質が悪い)。`diff --cached` は HEAD が無くても index を
# 空ツリーとの差分として扱えるので、unborn のときだけこちらに切り替える。born
# 側は --cached だとステージ済みしか見えないため、ステージ済み・未ステージの
# 両方を拾える HEAD 相手の diff のままにする。
#
# 順序は「HEAD との差分 → 未追跡」。両者は排他なので重複しない。stop-audit.sh の
# 報告 (max_files で切る) がこの順に依存している。
#
# -z が必須: 既定の core.quotePath が有効だと、引用符や非 ASCII を含むパスを
# `"evil\".ts"` のようにクォートして返し、そのままでは開けず黙って漏れる。出力も
# NUL 区切りなので、改行を含むファイル名でも壊れない。呼び出し側は
# `done < <(hook_git_changed_files "$root")` のようにプロセス置換か直接のパイプで
# 読むこと。コマンド置換 `$(...)` で受けると bash が NUL を捨てて壊れる。
#
# 出力は <repo_root> 相対のまま。絶対パスにするか、-f で実体を確かめるかは
# 呼び出し側の関心事 (削除されたファイルも diff には出る)。
#
# 変数は全て local。source 時に副作用を持たない契約 (ヘッダ参照) に加えて、
# 呼び出し元の変数を汚さない。
hook_git_changed_files() {
  local repo_root="$1"
  [ -n "$repo_root" ] || return 0

  if git -C "$repo_root" rev-parse -q --verify HEAD >/dev/null 2>&1; then
    git -C "$repo_root" diff --name-only -z HEAD 2>/dev/null
  else
    git -C "$repo_root" diff --name-only -z --cached 2>/dev/null
  fi
  git -C "$repo_root" ls-files --others --exclude-standard -z 2>/dev/null
  return 0
}

# hook_find_nearest_file <start_dir> <root_dir> <name>...
#
# <start_dir> から <root_dir> まで遡り、最初に見つかった (= 最も近い) ディレクトリ
# にある <name> のうち、引数で先に挙げたものの絶対パスを標準出力に書いて 0 を返す。
# 無ければ何も書かず 1 を返す。1 つのディレクトリ内の優先順位は引数の並び順。
#
# hook_find_nearest_bin の node_modules/.bin 版ではなく設定ファイル版。整形側
# (.prettierrc / .clang-format / .editorconfig の有無の判定) と lint 側 (tsconfig.json /
# .hadolint.yaml の探索) が「近い方が勝つ」規則を共有する。<root_dir> を越えては
# 遡らない: 無関係な親ディレクトリの設定を拾うと、他人のリポジトリの外にある設定で
# 整形・解析することになる。
#
# 物理パスで歩く理由と、呼び出しを `X=$(...)` の形にする理由は hook_find_nearest_bin
# を参照 (サブシェルなので変数が衝突しない)。
hook_find_nearest_file() {
  local dir root name candidate
  dir=$(cd "$1" 2>/dev/null && pwd -P) || return 1
  root=$(cd "$2" 2>/dev/null && pwd -P) || return 1
  shift 2
  while [ -n "$dir" ]; do
    for name in "$@"; do
      candidate="$dir/$name"
      if [ -f "$candidate" ]; then
        printf '%s' "$candidate"
        return 0
      fi
    done
    [ "$dir" = "$root" ] && return 1
    [ "$dir" = "/" ] && return 1
    dir=$(dirname "$dir")
  done
  return 1
}

# hook_git_added_lines <repo_root> <repo_relative_path>
#
# <path> で「作業ツリーが HEAD に対して追加した行」だけを `行番号:本文` (grep -n と
# 同じ形) で標準出力に書く。常に 0 を返す。Stop の監査 (stop-audit.sh) が使う。
#
# ファイル全体を走査すると、既にコミットされている正当な `console.log` (CLI の出力、
# ロガー) があるだけで、そのファイルが dirty の間は毎ターン block する。監査したい
# のは「この作業が残していったもの」なので、差分の追加側に限る。
#
#   - HEAD に存在するファイル: `git diff -U0 HEAD` の追加行。ステージ済みと未ステージ
#     の編集は HEAD との差分として一つにまとまる。
#   - HEAD に存在しないがインデックスにはあり、`git mv` の移動先として移動元と対に
#     なるファイル (_hook_git_rename_source): 移動元との差分の追加行。全行を追加と
#     数えると、もとからある正当な `console.log` が移動しただけで block される。
#   - それ以外の HEAD に存在しないファイル (未追跡、新規ステージ、最初のコミット前の
#     unborn branch の全ファイル): 全行が追加なので全体を走査する。移動元と似ていない
#     (類似度が git の既定の 50% 未満) ほど書き換えた移動、およびステージしていない
#     `mv` (移動先は未追跡) もここに入る。
#
# 差分の読み方は利用者の git 設定に左右されないようにしてある。color.diff=always を
# 設定していると既定の出力に ESC 列が混ざって `@@` も `+` も読めなくなるため、
# 色・外部 diff・textconv を明示的に切る。rename 検出は、普通のファイルでは
# `--no-renames` で切り (diff.renames の設定に左右されない)、移動先のときだけ
# `--find-renames` で入れる。--literal-pathspecs は `[id].tsx` のようなパス
# (Next.js の動的ルート) をグロブとして読まないため。
# 最初の `@@` より前のヘッダ (`+++ b/...`) は読み飛ばし、`@@` より後の `+` で始まる
# 行は (内容が `+++` で始まっていても) 追加行として数える。
hook_git_added_lines() {
  local repo_root="$1" rel="$2" old
  [ -n "$repo_root" ] && [ -n "$rel" ] || return 0

  if git -C "$repo_root" cat-file -e "HEAD:$rel" 2>/dev/null; then
    _hook_git_diff_added "$repo_root" --no-renames "$rel"
  elif [ -f "$repo_root/$rel" ]; then
    old=$(_hook_git_rename_source "$repo_root" "$rel")
    if [ -n "$old" ]; then
      _hook_git_diff_added "$repo_root" --find-renames "$old" "$rel"
    else
      LC_ALL=C grep -n '' "$repo_root/$rel" 2>/dev/null
    fi
  fi
  return 0
}

# _hook_git_diff_added <repo_root> <--no-renames|--find-renames> <path>...
#
# HEAD と作業ツリーの差分のうち、<path>... について追加された行を `行番号:本文`
# で書く。<path> を 2 つ (移動元と移動先) 渡して --find-renames にすると、対として
# 検出された移動の差分 (内容が変わった行だけ) になる。
_hook_git_diff_added() {
  local repo_root="$1" renames="$2"
  shift 2
  git --literal-pathspecs -C "$repo_root" -c color.diff=false -c color.ui=false \
    diff -U0 --no-color --no-ext-diff --no-textconv "$renames" HEAD -- "$@" 2>/dev/null |
    LC_ALL=C awk '
      /^@@/ {
        inh = 1
        s = $0
        sub(/^@@ -[0-9,]+ \+/, "", s)
        sub(/[ ,].*/, "", s)
        n = s + 0
        next
      }
      inh && /^\+/ { print n ":" substr($0, 2); n++ }
    '
}

# _hook_git_rename_source <repo_root> <repo_relative_path>
#
# <path> が HEAD に対する移動 (`git mv`) の移動先なら、その移動元のパスを書く。
# そうでなければ何も書かない。常に 0 を返す。
#
# 対にできるのはインデックスにあるパスだけ: 未追跡のファイルは `git diff HEAD` に
# 現れないので、(ステージしていない `mv` も含め) 対にならず、呼び出し側が全体を走査
# する。インデックスに無いパスでは git を 2 回目に呼ばずに戻る。
#
# `--name-status -z` は `R<類似度> NUL 移動元 NUL 移動先 NUL` (コピーの `C` も同じ形)、
# それ以外は `<状態> NUL パス NUL`。-z なので改行や非 ASCII を含むパスも壊れない。
_hook_git_rename_source() {
  local repo_root="$1" rel="$2" status src dst
  git --literal-pathspecs -C "$repo_root" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1 || return 0
  while IFS= read -r -d '' status; do
    IFS= read -r -d '' src || break
    case "$status" in
    R* | C*)
      IFS= read -r -d '' dst || break
      if [ "$dst" = "$rel" ]; then
        printf '%s' "$src"
        return 0
      fi
      ;;
    esac
  done < <(git --literal-pathspecs -C "$repo_root" -c color.diff=false -c color.ui=false diff --name-status -z --no-color --find-renames HEAD 2>/dev/null)
  return 0
}

# hook_positive_int_or <value> <default>
#
# <value> が正の整数 (`5`、`010` は十進の 10) ならそれを、そうでなければ (空、数字
# 以外を含む、0、10 桁以上) <default> を標準出力に書く。常に 0 を返す。
# HOOK_LINT_MAX_* のような利用者が環境変数で渡す上限を検証するために使う。数でない
# 値をそのまま `[ -le ]` や `head -n` に渡すと、比較が失敗して本文が全て捨てられる。
# 先頭の 0 は落として返す: `$(( ))` は `010` を 8 進に読む。10 桁以上は `[ -le ]` の
# 整数の範囲を越えうるので受け付けない。
hook_positive_int_or() {
  local v="$1"
  case "$v" in
  '' | *[!0-9]*)
    printf '%s' "$2"
    return 0
    ;;
  esac
  v="${v#"${v%%[!0]*}"}"
  if [ -z "$v" ] || [ "${#v}" -gt 9 ]; then
    printf '%s' "$2"
  else
    printf '%s' "$v"
  fi
  return 0
}

# hook_cap_output [max_lines] [max_bytes]
#
# 標準入力を読み、先頭から max_lines 行 / max_bytes バイト (既定 200 行 / 16384 バイト、
# 環境変数 HOOK_LINT_MAX_LINES / HOOK_LINT_MAX_BYTES で上書き) までを標準出力に書く。
# 超えた分は捨て、末尾に「… truncated N lines」を 1 行足す。上限以内の入力はそのまま
# 通す。常に 0 を返す。
#
# 上限が正の整数でなければ (引数も環境変数も) 既定値に戻す (hook_positive_int_or)。
# 数として読めない値で比較が失敗すると、本文が全て消えて「… truncated」の 1 行だけが
# 返り、それが exit 2 の指摘になる。
#
# lint の出力を全量モデルに返すと、大きなプロジェクトでは実測 375KB になり、1 回の
# 編集のたびにコンテキストを食う (しかも大半は直せない既存の指摘)。先頭だけで
# 「何が起きているか」は伝わる。
#
# 切り方は head の線形パイプで行う。bash の文字列スライスは bash 3.2 では長さの
# 2 乗時間になる (stop-audit.sh が minified JS で分単位止まった件と同じ)。バイトで
# 切ると多バイト文字の途中で切れうるので、切り口に残った不完全な UTF-8 列は sed で
# 落とす (stop-audit.sh の cut_hit と同じ理由)。
#
# 残した部分は変数に取らず一時ファイルに書く。`$(...)` は末尾の改行を全て落とすので、
# 切り口が空行だと「残した行数」が空行の数だけ少なく数えられ、「… truncated N lines」
# が N を多く言う (しかも残したはずの空行も消える)。件数は書き出したファイルの行数
# から数える。
hook_cap_output() {
  local max_lines max_bytes src out total size kept dropped partial_utf8_tail
  max_lines=$(hook_positive_int_or "${1:-${HOOK_LINT_MAX_LINES:-}}" 200)
  max_bytes=$(hook_positive_int_or "${2:-${HOOK_LINT_MAX_BYTES:-}}" 16384)
  src=$(mktemp "${TMPDIR:-/tmp}/hook-cap.XXXXXX" 2>/dev/null) || {
    # 一時ファイルが作れない環境では上限を諦めて素通しする。ここで失敗して
    # 指摘を消すより、長いままでも返す方が安全。
    cat
    return 0
  }
  out="$src.cut"
  cat >"$src"
  total=$(grep -c '' "$src" 2>/dev/null)
  size=$(wc -c <"$src" | tr -d ' ')
  if [ "${total:-0}" -le "$max_lines" ] && [ "${size:-0}" -le "$max_bytes" ]; then
    cat "$src"
    rm -f "$src"
    return 0
  fi
  partial_utf8_tail=$'s/([\xC2-\xDF]|[\xE0-\xEF][\x80-\xBF]?|[\xF0-\xF4][\x80-\xBF]{0,2})$//'
  head -n "$max_lines" "$src" | LC_ALL=C head -c "$max_bytes" | LC_ALL=C sed -E "$partial_utf8_tail" >"$out" 2>/dev/null
  kept=$(grep -c '' "$out" 2>/dev/null)
  cat "$out"
  # バイトで切った切り口が行の途中だと末尾の改行が無い。次の行 (注記) を同じ行に
  # 続けない。`$(...)` が空になるのは最後のバイトが改行のとき。
  [ -n "$(tail -c 1 "$out" 2>/dev/null)" ] && echo
  rm -f "$src" "$out"
  dropped=$((total - ${kept:-0}))
  if [ "$dropped" -gt 0 ]; then
    printf '… truncated %d lines (output capped at %s lines / %s bytes)\n' \
      "$dropped" "$max_lines" "$max_bytes"
  else
    printf '… truncated (a long line was cut; output capped at %s lines / %s bytes)\n' \
      "$max_lines" "$max_bytes"
  fi
  return 0
}

# _hook_positive_number <value>
#
# <value> が 0 より大きい十進数 (`5`、`0.5`、`007`) のときだけ 0 を返す。
# `^[0-9]+(\.[0-9]+)?$` に合い、かつ 0 でない数字を含むこと。`00` `0.0` `.` `1.2.3`
# `abc` と空は拒否する。秒数として渡すと、GNU / BSD の timeout は `.` と `1.2.3` に
# 125 を返し (呼び出し側には指摘に見える)、`00` を「制限なし」と読む。フォールバック
# の sleep は不正な値で即座に失敗し、watchdog がコマンドを開始直後に殺して 124 を返す。
# 正規表現ではなく case で判定する: bash 3.2 の `[[ =~ ]]` は引用の扱いが 4 以降と違う。
_hook_positive_number() {
  case "$1" in
  '' | *[!0-9.]* | . | .* | *. | *.*.*) return 1 ;;
  esac
  case "$1" in
  *[1-9]*) return 0 ;;
  esac
  return 1
}

# hook_run_timeout <seconds> <command> [args...]
#
# <command> を <seconds> 秒で打ち切って実行する。終了コードはコマンドのものをそのまま
# 返し、時間切れのときだけ 124 (GNU timeout と同じ) を返す。stdout / stderr は
# 呼び出し元のものにそのまま流れるので、`OUT=$(hook_run_timeout 20 tool 2>&1)` の
# 形で使える。外部コマンド専用 (シェル関数・組み込みは timeout バイナリに渡せない)。
#
# 優先順位は timeout → gtimeout → 純 bash のフォールバック。timeout が無い環境も多い
# (macOS は coreutils を入れれば gtimeout として入るが、入れていなければ無かった。
# 新しい macOS は BSD 版の /usr/bin/timeout を同梱し、`-k` も同じ形で受け付ける)。
# Git for Windows の C:\Windows\System32\timeout.exe は別物 (秒を待つだけのコマンド)
# なので除外する。
#
# 時間切れは TERM から始め、TERM を無視するコマンドは 2 秒後に KILL する (`-k 2`)。
# KILL が要ったときの終了コードは 137 なので、それも 124 に直す: 呼び出し元 (_lint_exec)
# は 124 を「時間切れ」、それ以外の非 0 を「指摘」と読む。timeout は自分の process
# group を作ってコマンドを入れる (pgid が timeout の pid)。コマンド本体が TERM で
# 死んでも、TERM を無視して pipe を握る孫は group に残り、`-k` はコマンド本体が
# 死んだ後は何も送らない。`$(...)` はその孫が閉じるまで返らないので、時間切れのときは
# group 全体に KILL を送る (実測: -k だけでは孫の sleep 12 を 12 秒待った)。
#
# timeout は背景で起動して `wait` する (pid が要るため)。背景ジョブは既定で標準入力が
# /dev/null になるので、`<&0` で呼び出し元のものを引き継ぐ。
#
# 秒が正の数 (_hook_positive_number) でなければ打ち切らずに実行する: 不正な値を
# timeout や `sleep` に渡すと、125 を返すか、全コマンドを開始直後に殺してしまう。この
# 関数には替わりの既定値が無いので、既定値を持つ呼び出し元 (_lint_timeout) が先に検証
# して置き換える。
hook_run_timeout() {
  local secs="$1" tbin tpid rc
  shift
  if ! _hook_positive_number "$secs"; then
    "$@"
    return $?
  fi
  for tbin in timeout gtimeout; do
    command -v "$tbin" >/dev/null 2>&1 || continue
    case "$(command -v "$tbin")" in
    */[Ss]ystem32/*) continue ;;
    esac
    "$tbin" -k 2 "$secs" "$@" <&0 &
    tpid=$!
    wait "$tpid"
    rc=$?
    case "$rc" in
    124 | 137)
      kill -KILL -- "-$tpid" 2>/dev/null
      rc=124
      ;;
    esac
    return "$rc"
  done
  _hook_timeout_fallback "$secs" "$@"
}

# _hook_kill_tree <pid>
#
# <pid> とその子孫に SIGTERM を送る (子から先に: 親を先に殺すと子が init に付け替え
# られて pgrep -P で辿れなくなる)。直接の子だけ殺すと、孫 (cargo の下の rustc、
# スクリプトの下の sleep) がパイプを握ったままになり、`$(...)` がそれの終了まで
# 返ってこない — 打ち切った意味が無くなる。
_hook_kill_tree() {
  local pid="$1" child
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r child; do
      [ -n "$child" ] && _hook_kill_tree "$child"
    done < <(pgrep -P "$pid" 2>/dev/null)
  fi
  kill -TERM "$pid" 2>/dev/null
  return 0
}

# _hook_tree_pids <pid>
#
# <pid> の子孫と <pid> 自身を、子から先の順 (_hook_kill_tree と同じ) に 1 行 1 pid で
# 書く。TERM を送る前に取っておくための関数: TERM で親が死ぬと、TERM を無視する孫は
# init に付け替えられ、`pgrep -P` ではもう辿れない。pgrep が無いときは <pid> だけ。
_hook_tree_pids() {
  local pid="$1" child
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r child; do
      [ -n "$child" ] && _hook_tree_pids "$child"
    done < <(pgrep -P "$pid" 2>/dev/null)
  fi
  printf '%s\n' "$pid"
}

# _hook_terminate_tree <pid>
#
# <pid> とその子孫を止める。先に子孫の pid を取り、全員に TERM を送り、最大 2 秒
# (`timeout -k 2` と同じ) 待って、まだ生きている者に KILL を送る。TERM で全員が
# 終われば待たない。親を KILL しただけでは、TERM を無視して pipe を握る孫が残る。
_hook_terminate_tree() {
  local IFS=$' \t\n'
  local pids p n=0 alive
  pids=$(_hook_tree_pids "$1")
  for p in $pids; do
    kill -TERM "$p" 2>/dev/null
  done
  while [ "$n" -lt 20 ]; do
    alive=false
    for p in $pids; do
      kill -0 "$p" 2>/dev/null && alive=true
    done
    $alive || break
    sleep 0.1
    n=$((n + 1))
  done
  for p in $pids; do
    kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null
  done
  return 0
}

# _hook_timeout_fallback <seconds> <command> [args...]
#
# 純 bash 版 (bash 3.2 でも動く)。コマンドをバックグラウンドで走らせ、別の監視
# サブシェルが時間切れで子孫ごと止める (_hook_terminate_tree)。時間切れは一時ファイルの
# 印で判定する (143 は別経路でも出うるため)。時間切れのときは監視が終わる (KILL まで
# 済ませる) のを `wait` で待つ: コマンド本体が TERM で死ぬとこちらの `wait` が先に
# 返り、そのまま監視を殺すと、TERM を無視する孫への KILL が送られない。コマンドが先に
# 終わったときだけ監視を殺す。監視の stdout/stderr は /dev/null: 監視が呼び出し元の
# パイプを握ると `$(...)` が秒数分待たされる。`wait` の stderr も捨てる: シグナルで死んだ
# ジョブの "Terminated" 通知は bash 自身が出すもので、lint.sh の stderr (= モデルへの
# 指摘の経路) に混ざる。
_hook_timeout_fallback() {
  local secs="$1" cmd_pid watch_pid rc marker
  shift
  marker=$(mktemp "${TMPDIR:-/tmp}/hook-timeout.XXXXXX" 2>/dev/null) || marker=""
  "$@" &
  cmd_pid=$!
  (
    sleep "$secs"
    if kill -0 "$cmd_pid" 2>/dev/null; then
      [ -n "$marker" ] && echo 1 >"$marker"
      _hook_terminate_tree "$cmd_pid"
    fi
  ) >/dev/null 2>&1 &
  watch_pid=$!
  wait "$cmd_pid" 2>/dev/null
  rc=$?
  if [ -n "$marker" ] && [ -s "$marker" ]; then
    wait "$watch_pid" 2>/dev/null
    rc=124
  else
    _hook_kill_tree "$watch_pid"
    wait "$watch_pid" 2>/dev/null
  fi
  [ -n "$marker" ] && rm -f "$marker"
  return "$rc"
}
