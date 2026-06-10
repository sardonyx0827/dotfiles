#!/bin/bash
# Claude Code Stop hook: 変更ファイルにデバッグ文が残っていないか最終監査する。
# 残留があれば decision:block で Claude に修正を促す。
# stop_hook_active が true の場合(block からの継続)は無限ループ防止のため即終了。

input=$(cat)

stop_active=$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$stop_active" = "true" ] && exit 0

# git リポジトリ外なら何もしない
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

# 作業ツリーの変更ファイル + 未追跡ファイル
files=$( (
  git diff --name-only HEAD 2>/dev/null
  git ls-files --others --exclude-standard 2>/dev/null
) | sort -u)
[ -z "$files" ] && exit 0

findings=""
while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in
  *.js | *.jsx | *.ts | *.tsx)
    hits=$(grep -nE '(^|[^.[:alnum:]])console\.log\(|(^|[^[:alnum:]])debugger(;|$)' "$f" 2>/dev/null | head -5)
    ;;
  *.py)
    hits=$(grep -nE '(^|[^[:alnum:]])breakpoint\(\)|pdb\.set_trace\(\)' "$f" 2>/dev/null | head -5)
    ;;
  *)
    hits=""
    ;;
  esac
  [ -n "$hits" ] && findings="${findings}${f}:\n${hits}\n"
done <<<"$files"

[ -z "$findings" ] && exit 0

reason="Debug statements remain in modified files. Remove console.log / debugger / breakpoint() before finishing:\n${findings}"
jq -cn --arg reason "$(printf '%b' "$reason")" '{decision: "block", reason: $reason}'
exit 0
