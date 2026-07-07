#!/bin/bash
# Codex PreToolUse hook (matcher: Bash): git push を検知したら
# push 対象コミットのサマリを添えてブロックし、ユーザー確認を促す。
# Codex は permissionDecision:"ask" を扱わないため exit 2 + stderr で確認を要求する。
# git push 以外のコマンドは即 exit 0(判定は bash-review.py 等に委ねる)。
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、判定に
# 失敗しても本体のコマンド実行を止めてはならない。各コマンドの失敗は
# `|| exit 0` / `2>/dev/null` で個別に握りつぶし、最悪でも exit 0 で抜ける。

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)

# シングル/ダブルクォートで囲まれた区間は実行されるコマンドではなく単なる
# 文字列(コミットメッセージ等)なので、誤検知を避けるため push 判定の前に
# 除去する (例: `git commit -m "please dont git push this yet"` は push
# コマンドではない)。
#
# `s/'[^']*'//g; s/"[^"]*"//g` のような一括置換は左から右への状態遷移を
# 無視するため、`git commit -m "it's fine" && git push && echo 'done'` の
# ようなコマンドで "it's" のアポストロフィが後方の 'done' の開始クォートと
# 誤ってペアリングされ、間にある実行される裸の git push ごと消えてしまう
# (置換順序を入れ替えても鏡像ケースで同じ問題が起きるため直らない)。その
# ため 1 文字ずつシェルの引用規則(シングルクォート内はバックスラッシュが
# 無効、ダブルクォート内・クォート外はバックスラッシュが次の1文字をエスケー
# プ)を状態機械で追ってクォート区間を除去する。
strip_quoted_ranges() {
  local str="$1" out="" c state=0 i=0 len
  len=${#str}
  while [ "$i" -lt "$len" ]; do
    c="${str:i:1}"
    case "$state" in
    0)
      # クォート外: バックスラッシュは次の1文字を素通しでエスケープする
      # (`\"` / `\'` はクォートを開始しない)。
      case "$c" in
      "\\")
        out+="${str:i+1:1}"
        i=$((i + 2))
        ;;
      "'")
        state=1
        i=$((i + 1))
        ;;
      "\"")
        state=2
        i=$((i + 1))
        ;;
      *)
        out+="$c"
        i=$((i + 1))
        ;;
      esac
      ;;
    1)
      # シングルクォート内: バックスラッシュも含め閉じクォートまで全て破棄
      # (POSIX仕様でバックスラッシュに特別な意味はない)。
      [ "$c" = "'" ] && state=0
      i=$((i + 1))
      ;;
    2)
      # ダブルクォート内: 閉じクォートまで全て破棄。バックスラッシュは
      # 次の1文字ごと消費し、エスケープされた `"` で閉じないようにする。
      case "$c" in
      "\\")
        i=$((i + 2))
        ;;
      "\"")
        state=0
        i=$((i + 1))
        ;;
      *)
        i=$((i + 1))
        ;;
      esac
      ;;
    esac
  done
  printf '%s' "$out"
}

cmd_for_match=$(strip_quoted_ranges "$cmd")

# コマンド文字列のどこかに git ... push が含まれるか(チェーン・サブシェル含む)。
# フラグは「値が = で連結される形式 (--git-dir=/x)」と「スペースで区切られる
# 形式 (git -C /repo push)」の両方を許容する (値はフラグと誤読しないよう
# 先頭が - 以外のトークンに限定)。
echo "$cmd_for_match" | grep -qE '(^|[;&|[:space:](])git([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*[[:space:]]+push([[:space:]]|$)' || exit 0

# `git -C <dir> push` のように push 対象リポジトリが明示されている場合、
# サマリもフック自身の cwd ではなく同じ <dir> を対象に生成する。
git_c_opt=()
if [[ "$cmd" =~ (^|[[:space:]])-C[[:space:]]+([^[:space:]]+) ]]; then
  git_c_opt=(-C "${BASH_REMATCH[2]}")
fi

summary=""
if git "${git_c_opt[@]}" rev-parse --is-inside-work-tree &>/dev/null; then
  branch=$(git "${git_c_opt[@]}" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if git "${git_c_opt[@]}" rev-parse --abbrev-ref '@{upstream}' &>/dev/null; then
    commits=$(git "${git_c_opt[@]}" log --oneline '@{upstream}..HEAD' 2>/dev/null | head -10)
    stat=$(git "${git_c_opt[@]}" diff --stat '@{upstream}..HEAD' 2>/dev/null | tail -1)
  else
    commits=$(git "${git_c_opt[@]}" log --oneline -5 2>/dev/null)
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
