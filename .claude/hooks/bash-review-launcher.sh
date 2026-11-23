#!/bin/bash
# Claude Code PreToolUse hook (matcher: Bash) の起動ラッパー:
# bash-review.py を信頼できるシステムの python3 で起動し、「レビューを実行できなかった」を
# fail-open ではなく ask に倒す。
#
# 背景: settings.json から `python3 .../bash-review.py` を直接呼ぶ配線では、
# python3 の不在・フックファイルの欠落・レビュー本体のクラッシュ (0/2 以外の
# 終了コード) を Claude Code は non-blocking error として扱い、対象コマンドは
# そのまま実行される — 多層ガード全体が無言で消える fail-open。
# git-push-review.sh の jq 縮退 (同ファイル冒頭コメント) と同じ判断で、
# 「ゲート自体が動かない」ことだけは黙って通さない。
#
# インタプリタは PATH から引かない。下の trusted-python の固定の候補のうち、root 所有で
# 書き換えられない最初のものを -I -S で起動する。どれも満たさなければ ask (PATH の
# python3 には落ちない)。settings.json はこのファイルを /bin/bash で起動する。
#
# 意図的に set -e は使わない: 本体の非ゼロ終了はこのラッパーが分岐すべき
# 対象データであって、ラッパー自身の異常ではない。
#
# ラッパー自身が起動できないケース (bash の不在等) はここでは救えない。
# その最終境界は permissions.deny (.claude/hooks/README.md の脅威モデル参照)。

# 起動の環境から外す (_sandbox_gate.LAUNCH_ENV_DROP と同じ一覧。テストで固定)。
# framework 版の python は __PYVENV_LAUNCHER__ / PYTHONEXECUTABLE で sys.executable と
# pyvenv.cfg の探し先を変え (-I でも止まらない)、xcrun の shim は DEVELOPER_DIR などで
# 別の実体を選ぶ。
unset DEVELOPER_DIR TOOLCHAINS SDKROOT __PYVENV_LAUNCHER__ PYTHONEXECUTABLE PYTHONHOME PYTHONPATH

# 理由文は固定文字列 + 数値の差し込みのみなので jq 無しで組み立ててよい
# (エスケープすべき動的データを含めない)。
ask_and_exit() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$1"
  exit 0
}

# >>> trusted-python (both launchers carry this block byte for byte; see
# tests/test_bash_review_launcher.py)
# フックは root 所有のシステムの python3 でだけ動かす。PATH・pyenv の shim・
# .python-version には従わない (shim は cwd の .python-version に従い、~/.pyenv の下に
# 作った名前も選ぶので、偽のインタプリタでフック自身を乗っ取れた)。
# 規則は .claude/hooks/_sandbox_gate.py の trusted_path / trusted_python と同じで、同じ
# fixture で食い違わないことをテストで確かめる。信頼する前のインタプリタは起動しない
# ので、判定はシェルで持つ。外のコマンドは root 所有の /usr/bin/stat と
# /usr/bin/readlink だけを絶対パスで呼ぶ。/bin/bash 3.2 (macOS) で動く書き方にする。

# 固定の候補 (この順に見る)。_sandbox_gate.trusted_python_candidates と同じ。
_trusted_python_candidates() {
  case "$OSTYPE" in
    darwin*) _TRUSTED_CANDIDATES=(/Library/Developer/CommandLineTools/usr/bin/python3 /usr/bin/python3) ;;
    linux*) _TRUSTED_CANDIDATES=(/usr/bin/python3) ;;
    *) _TRUSTED_CANDIDATES=() ;;
  esac
}

# 文字列を / で分けて _TP_SPLIT に入れる (Python の str.split("/") と同じ)。
_trusted_split() {
  local s=$1
  _TP_SPLIT=()
  while :; do
    case "$s" in
      */*)
        _TP_SPLIT+=("${s%%/*}")
        s=${s#*/}
        ;;
      *)
        _TP_SPLIT+=("$s")
        return 0
        ;;
    esac
  done
}

# _trusted_parts PATH BASE: BASE の下の要素を _TP_SPLIT に入れる。BASE の外なら 1。
_trusted_parts() {
  if [ "$2" = / ]; then
    _trusted_split "$1"
  elif [ "$1" = "$2" ]; then
    _TP_SPLIT=()
  else
    case "$1" in
      "$2"/*) _trusted_split "${1#"$2"/}" ;;
      *) return 1 ;;
    esac
  fi
}

# _trusted_path PATH OWNERS TOP: 0 なら PATH は信頼できる実行ファイル。OWNERS は空白
# 区切りの uid、TOP は辿ってよい範囲 (本番は 0 と /。ほかの値はテストのため)。
# 辿る途中のディレクトリ (TOP を含む) は本物のディレクトリで、OWNERS の誰かの所有で、
# group / other が書けない。symlink は OWNERS の誰かの所有 (mode は見ない)。行き着いた
# 先は OWNERS の誰かの所有の通常ファイルで、group / other が書けず、実行の bit がある。
_trusted_path() {
  local owners=" $2 " base=$3 prefix part here target hops=0 n i uid mode kind
  local -a queue paths kinds
  case "$1" in /*) ;; *) return 1 ;; esac
  case "$base" in
    /) ;;
    */ | *//* | */./* | */../* | */. | */..) return 1 ;;
    /*) ;;
    *) return 1 ;;
  esac
  _trusted_parts "$1" "$base" || return 1
  queue=("${_TP_SPLIT[@]}")
  [ -L "$base" ] && return 1
  [ -d "$base" ] || return 1
  paths=("$base")
  kinds=(d)
  prefix=$base
  while [ "${#queue[@]}" -gt 0 ]; do
    part=${queue[0]}
    queue=("${queue[@]:1}")
    case "$part" in
      "" | .) continue ;;
      ..)
        [ "$prefix" = "$base" ] && return 1
        prefix=${prefix%/*}
        [ -n "$prefix" ] || prefix=/
        continue
        ;;
    esac
    if [ "$prefix" = / ]; then here=/$part; else here=$prefix/$part; fi
    if [ -L "$here" ]; then
      paths+=("$here")
      kinds+=(l)
      hops=$((hops + 1))
      [ "$hops" -gt 40 ] && return 1
      target=$(/usr/bin/readlink -- "$here") || return 1
      case "$target" in
        /*)
          _trusted_parts "$target" "$base" || return 1
          prefix=$base
          ;;
        *) _trusted_split "$target" ;;
      esac
      queue=("${_TP_SPLIT[@]}" "${queue[@]}")
      continue
    fi
    n=0
    for i in "${queue[@]}"; do
      case "$i" in "" | .) ;; *) n=1 ;; esac
    done
    if [ "$n" = 1 ]; then
      [ -d "$here" ] || return 1
      paths+=("$here")
      kinds+=(d)
      prefix=$here
      continue
    fi
    [ "${#queue[@]}" -eq 0 ] || return 1
    [ -f "$here" ] || return 1
    paths+=("$here")
    kinds+=(f)
    _trusted_modes "$owners" "${#paths[@]}" "${kinds[*]}" "${paths[@]}"
    return
  done
  return 1
}

# _trusted_modes OWNERS COUNT KINDS PATH...: 集めたパスの所有者と mode を 1 回の stat で
# 確かめる。KINDS は d (ディレクトリ) / l (symlink) / f (行き着いた先) の空白区切り。
_trusted_modes() {
  local owners=$1 count=$2 line uid mode kind seen=0 out
  local -a want
  IFS=' ' read -r -a want <<<"$3"
  shift 3
  case "$OSTYPE" in
    darwin* | *bsd*) out=$(/usr/bin/stat -f '%u %Sp' -- "$@") || return 1 ;;
    *) out=$(/usr/bin/stat -c '%u %A' -- "$@") || return 1 ;;
  esac
  while IFS= read -r line; do
    kind=${want[$seen]}
    seen=$((seen + 1))
    uid=${line%% *}
    mode=${line#* }
    case "$owners" in *" $uid "*) ;; *) return 1 ;; esac
    case "$kind" in
      l)
        [ "${mode:0:1}" = l ] || return 1
        continue
        ;;
      d) [ "${mode:0:1}" = d ] || return 1 ;;
      f)
        [ "${mode:0:1}" = - ] || return 1
        case "${mode:3:1}${mode:6:1}${mode:9:1}" in *[xst]*) ;; *) return 1 ;; esac
        ;;
      *) return 1 ;;
    esac
    [ "${mode:5:1}" = w ] && return 1
    [ "${mode:8:1}" = w ] && return 1
  done <<EOF
$out
EOF
  [ "$seen" -eq "$count" ]
}

# _trusted_python OWNERS TOP CANDIDATE...: _trusted_path を満たし、版が 3.9 以上の最初の
# 候補を出す (版は信頼を確かめた後でだけ起動して見る)。無ければ 1。
_trusted_python() {
  local owners=$1 top=$2 c
  shift 2
  for c in "$@"; do
    _trusted_path "$c" "$owners" "$top" || continue
    "$c" -I -S -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
      </dev/null >/dev/null 2>&1 || continue
    printf '%s\n' "$c"
    return 0
  done
  return 1
}
# <<< trusted-python

_launcher_main() {
  local launcher_dir hook py out status
  # フック本体はラッパー自身と同じディレクトリから解決する (インストール後は
  # ~/.claude/hooks がリポジトリ側への symlink になるため、相対解決でリポジトリ
  # 直実行と installed 実行の両方に効く)。dirname は PATH から引くので使わない。
  # 解決に失敗したら hook が見つからず下の -f 検査で ask に倒れる。
  case "${BASH_SOURCE[0]}" in
    */*) launcher_dir=${BASH_SOURCE[0]%/*} ;;
    *) launcher_dir=. ;;
  esac
  launcher_dir=$(cd "$launcher_dir" 2>/dev/null && pwd) || launcher_dir=""
  hook="$launcher_dir/bash-review.py"

  _trusted_python_candidates
  py=$(_trusted_python 0 / "${_TRUSTED_CANDIDATES[@]}" </dev/null) ||
    ask_and_exit "bash-review could not run: 信頼できるシステム python3 が無い (no root-owned, unwritable python3 >= 3.9 among the fixed candidates; PATH is not used), so this command was NOT reviewed. Review it manually before allowing (fail-closed)."

  [ -f "$hook" ] ||
    ask_and_exit "bash-review could not run: bash-review.py not found next to the launcher, so this command was NOT reviewed. Review it manually before allowing (fail-closed)."

  # stdin (フック JSON) はそのまま本体へ。stdout は「本体が判定を出し切れた
  # 場合だけ」素通しし、クラッシュ時は書きかけの出力を捨てて ask 一本に差し
  # 替える (壊れた JSON 断片を Claude Code に渡すと stdout ごと無視され
  # fail-open に戻るため)。stderr は診断用にそのまま流す。
  out=$("$py" -I -S "$hook")
  status=$?

  # 本体 (bash-review.py) が実際に使う正常終了語彙は exit 0 のみで、判定
  # (allow/ask/deny) はすべて stdout の permissionDecision JSON に載せて返す。
  # exit 2 もここで素通ししているのは .codex 変種 (exit 2 = blocking error) と
  # 配線を合わせるための保険で、現行の本体コードから返ることはない。python3 が
  # 本体を読めない場合 (import/構文エラー) は try 節の外で例外になり python3 は
  # exit 1 で落ちる — これは 0/2 のどちらにも一致せず、下のクラッシュ用
  # フォールバック (ask_and_exit) に落ちる。
  if [ "$status" -eq 2 ]; then
    [ -n "$out" ] && printf '%s\n' "$out"
    exit 2
  fi

  # exit 0 でも stdout が空 (空白のみを含む) なら判定が出ていない。Claude Code は
  # 「exit 0・出力なし」を決定なしの成功として扱い、コマンドをそのまま実行する
  # (fail-open)。本体は全分岐で判定 JSON を出してから exit 0 するので現行コードからは
  # 到達しないが、本体が途中で黙って終わった場合に、レビュー無しの実行を黙って
  # 通さない (Codex 変種は逆で、exit 0・出力なしが正常な許可の語彙)。
  if [ "$status" -eq 0 ]; then
    [ -z "${out//[[:space:]]/}" ] &&
      ask_and_exit "bash-review exited 0 without emitting a decision, so this command was NOT reviewed. Review it manually before allowing (fail-closed); a silent exit 0 would otherwise let it run unreviewed."
    printf '%s\n' "$out"
    exit 0
  fi

  ask_and_exit "bash-review crashed with exit code ${status} before emitting a decision, so this command was NOT reviewed. Review it manually before allowing (fail-closed); diagnostics are on the hook's stderr."
}

# source したとき (テストと install.sh) は関数の定義だけ。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  _launcher_main
fi
