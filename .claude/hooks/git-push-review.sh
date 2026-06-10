#!/bin/bash
# Claude Code PreToolUse hook (matcher: Bash): git push を検知したら
# push 対象コミットのサマリを添えて必ずユーザー確認を要求する。
# git push 以外のコマンドは即 exit 0(判定は bash-review.py 等に委ねる)。

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)

# コマンド文字列のどこかに git ... push が含まれるか(チェーン・サブシェル含む)
echo "$cmd" | grep -qE '(^|[;&|[:space:](])git([[:space:]]+-[-[:alnum:]=]+)*[[:space:]]+push([[:space:]]|$)' || exit 0

summary=""
if git rev-parse --is-inside-work-tree &>/dev/null; then
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if git rev-parse --abbrev-ref '@{upstream}' &>/dev/null; then
    commits=$(git log --oneline '@{upstream}..HEAD' 2>/dev/null | head -10)
    stat=$(git diff --stat '@{upstream}..HEAD' 2>/dev/null | tail -1)
  else
    commits=$(git log --oneline -5 2>/dev/null)
    stat="(no upstream: new branch push)"
  fi
  summary="branch: ${branch}
commits to push:
${commits:-"(none)"}
${stat}"
fi

reason="git push detected. Review before pushing (see ~/.claude/rules/git-workflow.md):
${summary}"

jq -cn --arg reason "$reason" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $reason}}'
exit 0
