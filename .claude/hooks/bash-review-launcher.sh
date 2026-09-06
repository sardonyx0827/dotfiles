#!/bin/bash
# Claude Code PreToolUse hook (matcher: Bash) の起動ラッパー:
# bash-review.py を python3 で起動し、「レビューを実行できなかった」を
# fail-open ではなく ask に倒す。
#
# 背景: settings.json から `python3 .../bash-review.py` を直接呼ぶ配線では、
# python3 の不在・フックファイルの欠落・レビュー本体のクラッシュ (0/2 以外の
# 終了コード) を Claude Code は non-blocking error として扱い、対象コマンドは
# そのまま実行される — 多層ガード全体が無言で消える fail-open。
# git-push-review.sh の jq 縮退 (同ファイル冒頭コメント) と同じ判断で、
# 「ゲート自体が動かない」ことだけは黙って通さない。
#
# 意図的に set -e は使わない: 本体の非ゼロ終了はこのラッパーが分岐すべき
# 対象データであって、ラッパー自身の異常ではない。
#
# ラッパー自身が起動できないケース (bash の不在等) はここでは救えない。
# その最終境界は permissions.deny (.claude/hooks/README.md の脅威モデル参照)。

# 理由文は固定文字列 + 数値の差し込みのみなので jq 無しで組み立ててよい
# (エスケープすべき動的データを含めない。git-push-review.sh の jq 縮退
# パスと同じ判断)。
ask_and_exit() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$1"
  exit 0
}

# フック本体はラッパー自身と同じディレクトリから解決する (インストール後は
# ~/.claude/hooks がリポジトリ側への symlink になるため、相対解決でリポジトリ
# 直実行と installed 実行の両方に効く)。解決に失敗したら hook が見つからず
# 下の -f 検査で ask に倒れる。
launcher_dir=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || launcher_dir=""
hook="$launcher_dir/bash-review.py"

command -v python3 >/dev/null 2>&1 ||
  ask_and_exit "bash-review could not run: python3 not found on PATH, so this command was NOT reviewed. Review it manually before allowing (fail-closed)."

[ -f "$hook" ] ||
  ask_and_exit "bash-review could not run: bash-review.py not found next to the launcher, so this command was NOT reviewed. Review it manually before allowing (fail-closed)."

# stdin (フック JSON) はそのまま本体へ。stdout は「本体が判定を出し切れた
# 場合だけ」素通しし、クラッシュ時は書きかけの出力を捨てて ask 一本に差し
# 替える (壊れた JSON 断片を Claude Code に渡すと stdout ごと無視され
# fail-open に戻るため)。stderr は診断用にそのまま流す。
out=$(python3 "$hook")
status=$?

# 本体 (bash-review.py) が実際に使う正常終了語彙は exit 0 のみで、判定
# (allow/ask/deny) はすべて stdout の permissionDecision JSON に載せて返す。
# exit 2 もここで素通ししているのは .codex 変種 (exit 2 = blocking error) と
# 配線を合わせるための保険で、現行の本体コードから返ることはない。python3 が
# 本体を読めない場合 (import/構文エラー) は try 節の外で例外になり python3 は
# exit 1 で落ちる — これは 0/2 のどちらにも一致せず、下のクラッシュ用
# フォールバック (ask_and_exit) に落ちる。
if [ "$status" -eq 0 ] || [ "$status" -eq 2 ]; then
  [ -n "$out" ] && printf '%s\n' "$out"
  exit "$status"
fi

ask_and_exit "bash-review crashed with exit code ${status} before emitting a decision, so this command was NOT reviewed. Review it manually before allowing (fail-closed); diagnostics are on the hook's stderr."
