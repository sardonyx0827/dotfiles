#!/bin/bash
# Codex Stop hook: 変更ファイルにデバッグ文が残っていないか最終監査する。
# 残留があれば exit 2 + stderr でエージェントに修正を促す(ブロック)。
# stop_hook_active が true の場合(block からの継続)は無限ループ防止のため即終了。
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、監査自体が
# 失敗しても Stop を止めてはならない。各コマンドの失敗は `|| exit 0` /
# `2>/dev/null` で個別に握りつぶし、最悪でも exit 0 で抜ける。

input=$(cat)

# jq が無いと stop_hook_active を読めない。読めないまま監査を続けると、block
# からの継続でもフラグが空 = false 扱いで再び block し、Stop が終わらなくなる。
# lint.sh / auto-format.sh と同じく jq 不在は監査しない。
command -v jq >/dev/null 2>&1 || {
  echo "stop-audit: jq not found on PATH; debug-statement audit skipped" >&2
  exit 0
}

stop_active=$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$stop_active" = "true" ] && exit 0

# エディタ (vim / nvim) の AI 機能から起動された一発呼び出し(コミットメッセージ
# 生成のような *生成専用* の呼び出し)は監査しない。理由と、変数を立てる側は
# .claude/hooks/stop-audit.sh を参照。
[ -n "$EDITOR_AI_ONESHOT" ] && exit 0

# git リポジトリ外なら何もしない
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

# cwd がサブディレクトリでもリポジトリ全体をリポジトリルート相対で取得できるよう、
# ルートを解決して `-C` で明示的に指定する(.claude/hooks/stop-audit.sh と同じ)。
repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$repo_root" ] && exit 0

# HEAD が無い(= unborn branch)場合は `diff --name-only -z HEAD` が失敗して
# ステージ済みの内容が監査から漏れるため、unborn 時だけ `diff --cached` に切り替える
# (詳細は .claude/hooks/stop-audit.sh)。
if git -C "$repo_root" rev-parse -q --verify HEAD >/dev/null 2>&1; then
  diff_cmd=(git -C "$repo_root" diff --name-only -z HEAD)
else
  diff_cmd=(git -C "$repo_root" diff --name-only -z --cached)
fi

# 作業ツリーの変更ファイル + 未追跡ファイル(両者は排他なので重複しない)。
# -z とプロセス置換が必須な理由は .claude/hooks/stop-audit.sh を参照。
# 報告は量を限る (一致行は 1 行 200 バイト、ファイルは max_files 件まで) うえで、最初
# から実改行で組む。理由は .claude/hooks/stop-audit.sh を参照 (巨大な 1 行で Stop が
# 分単位で止まり、検出行に書かれた `\n` まで改行に変えていた)。
max_files=20
partial_utf8_tail=$'s/([\xC2-\xDF]|[\xE0-\xEF][\x80-\xBF]?|[\xF0-\xF4][\x80-\xBF]{0,2})$//'
cut_hit() { LC_ALL=C cut -c1-200 | LC_ALL=C sed -E "$partial_utf8_tail"; }
nl=$'\n'
findings=""
found=0
while IFS= read -r -d '' f; do
  path="$repo_root/$f"
  [ -f "$path" ] || continue
  case "$f" in
  *.js | *.jsx | *.ts | *.tsx)
    # console.log / debugger の境界条件の理由は .claude/hooks/stop-audit.sh を参照。
    hits=$(grep -nE '(^|[^[:alnum:]])console\.log\(|(^|[^[:alnum:]_$])debugger([^[:alnum:]_$]|$)' "$path" 2>/dev/null | head -5 | cut_hit)
    ;;
  *.py)
    hits=$(grep -nE '(^|[^[:alnum:]])breakpoint\(\)|pdb\.set_trace\(\)' "$path" 2>/dev/null | head -5 | cut_hit)
    ;;
  *)
    hits=""
    ;;
  esac
  if [ -n "$hits" ]; then
    found=$((found + 1))
    [ "$found" -le "$max_files" ] && findings="${findings}${f}:${nl}${hits}${nl}"
  fi
done < <(
  "${diff_cmd[@]}" 2>/dev/null
  git -C "$repo_root" ls-files --others --exclude-standard -z 2>/dev/null
)

[ "$found" -eq 0 ] && exit 0

if [ "$found" -gt "$max_files" ]; then
  findings="${findings}(and $((found - max_files)) more files with debug statements)${nl}"
fi
# Codex の Stop フックでは exit 2 + stderr でブロックし、内容をエージェントに伝える。
# 検出行はエスケープ解釈を挟まずそのまま出す (`printf %b` は検出行の `\c` で以降の
# 出力を打ち切る。.claude/hooks/stop-audit.sh も同じ)。
printf '%s' "Debug statements remain in modified files. Remove console.log / debugger / breakpoint() before finishing:${nl}${findings}" >&2
exit 2
