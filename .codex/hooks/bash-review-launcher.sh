#!/bin/bash
# Codex PreToolUse hook (matcher: Bash) の起動ラッパー (.codex 変種):
# bash-review.py を python3 で起動し、「レビューを実行できなかった」を
# fail-open ではなく exit 2 + stderr のブロックに倒す。
#
# 背景は .claude/hooks/bash-review-launcher.sh (JSON-ask 変種) と同じ:
# hooks.json から `python3 .../bash-review.py` を直接呼ぶ配線では、python3 の
# 不在・フックファイルの欠落・本体のクラッシュ (0/2 以外の終了コード) が
# フックのエラー扱い = fail-open になり、コマンドがレビューなしで実行される。
#
# 変種差分 (README「返却プロトコル」参照): Codex は ask 未サポート (返すと
# fail-open) で、stdout を構造化出力として解釈する (平文を出すとフック自体が
# 失敗扱い)。よって縮退の表明は stderr + exit 2 のみで行い、stdout には何も
# 出さない。ブロック理由には bash-review.py の _AGENT_BLOCK_DIRECTIVE と同じ
# 「回避を試みず人間に報告せよ」の指示を添え、Codex の自律的な回避リトライを
# 抑える。
#
# 意図的に set -e は使わない: 本体の非ゼロ終了はこのラッパーが分岐すべき
# 対象データであって、ラッパー自身の異常ではない。

block_and_exit() {
  echo "$1 — BLOCKED (fail-closed by bash-review-launcher). Report this to the user; do NOT retry with alternative or simplified commands to work around it." >&2
  exit 2
}

# フック本体はラッパー自身と同じディレクトリから解決する (インストール後は
# ~/.codex/hooks がリポジトリ側への symlink になるため、相対解決で両配置に
# 効く)。解決に失敗したら hook が見つからず下の -f 検査でブロックに倒れる。
launcher_dir=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || launcher_dir=""
hook="$launcher_dir/bash-review.py"

command -v python3 >/dev/null 2>&1 ||
  block_and_exit "bash-review could not run: python3 not found on PATH, so this command was NOT reviewed"

[ -f "$hook" ] ||
  block_and_exit "bash-review could not run: bash-review.py not found next to the launcher, so this command was NOT reviewed"

# stdin (フック JSON) はそのまま本体へ。stdout は「本体が正常に終了した場合
# だけ」素通しし (許可 = exit 0 は本来無出力)、クラッシュ時は書きかけの出力を
# 捨てる (Codex は stdout を構造化出力として解釈するため、壊れた断片を渡すと
# フック自体が失敗扱い = fail-open に戻る)。stderr は診断用にそのまま流す。
out=$(python3 "$hook")
status=$?

# exit 0 (許可) と exit 2 (ブロック; stderr が Codex へ届く) は本体の正常な
# 終了語彙なのでそのまま返す。なお python3 が本体を読めない場合も exit 2 に
# なるが、それはブロック = fail-closed なので許容する。
if [ "$status" -eq 0 ] || [ "$status" -eq 2 ]; then
  [ -n "$out" ] && printf '%s\n' "$out"
  exit "$status"
fi

block_and_exit "bash-review crashed with exit code ${status} before emitting a decision, so this command was NOT reviewed"
