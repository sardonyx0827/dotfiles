#!/bin/bash
# Codex PreToolUse hook (matcher: Bash): git push を検知したら
# push 対象コミットのサマリを添えてブロックし、ユーザー確認を促す。
# Codex は permissionDecision:"ask" を扱わないため exit 2 + stderr で確認を要求する。
# git push 以外のコマンドは即 exit 0(判定は bash-review.py 等に委ねる)。

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)

# コマンド文字列のどこかに git ... push が含まれるか(チェーン・サブシェル含む)。
# フラグは「値が = で連結される形式 (--git-dir=/x)」と「スペースで区切られる
# 形式 (git -C /repo push)」の両方を許容する (値はフラグと誤読しないよう
# 先頭が - 以外のトークンに限定)。
echo "$cmd" | grep -qE '(^|[;&|[:space:](])git([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*[[:space:]]+push([[:space:]]|$)' || exit 0

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

# Codex では exit 2 + stderr でブロックし、内容をエージェント/ユーザーに伝える
cat >&2 <<EOF
git push detected. Review before pushing (see ~/.codex/AGENTS.md "Git ワークフロー"):
${summary}
EOF
exit 2
