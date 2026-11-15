#!/bin/bash
# Claude Code PreToolUse hook (matcher: Bash): git push を検知したら
# push 対象コミットのサマリを添えて必ずユーザー確認を要求する。
# git push 以外のコマンドは即 exit 0(判定は bash-review.py 等に委ねる)。
#
# 意図的に `set -e` は使わない: このフックは概ね fail-open 設計であり、サマリ
# 生成等に失敗しても本体のコマンド実行を止めてはならない。各コマンドの失敗は
# `|| exit 0` / `2>/dev/null` で個別に握りつぶし、最悪でも exit 0 で抜ける。
#
# 唯一の例外が jq の不在 (下記)。これは「サマリを作れない」ではなく「push か
# どうかを判定できない」であり、fail-open にするとゲート自体が無言で消える。
#
# 判定と確認サマリの組み立ては同じディレクトリの _push_review_common.sh にあり、
# .codex 版と共有する。このファイルは入力の取り出しと出力の形 (JSON の ask) だけを
# 持つ。そのライブラリを読めなかったときの縮退は下の読み込み部を参照。

input=$(cat)

# jq が無いとコマンド文字列を取り出せず、下の push 判定は空文字を検査して
# 必ず「該当なし」になる。そのまま exit 0 すると push 手前の唯一のゲートが
# 無言で消えるため、ここだけは fail-open にしない。生の stdin を粗く検査し、
# push らしき記述があれば (サマリは作れないので) 確認だけを要求する。
# 判定材料が JSON エスケープ済みの生文字列なので、クォート除去による誤検知
# 抑制は効かない = コミットメッセージ中の "git push" でも確認が出る。jq が
# 無い環境限定の縮退動作としては、取りこぼすより過検知の方が望ましい。
if ! command -v jq >/dev/null 2>&1; then
  if printf '%s' "$input" | grep -qE 'git.*push'; then
    # 理由文は固定文字列なので jq 無しで組み立ててよい (差し込み無し =
    # エスケープ不要)。
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"git push detected, but jq is unavailable so this hook could not parse the command or build the usual commit summary. Review manually before pushing (see ~/.claude/rules/git-workflow.md)."}}'
  fi
  exit 0
fi

cmd=$(echo "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)

# 非 push コマンドをここで捨てる。settings.json に `if: Bash(git push*)` は
# 付けない (best-effort な前方一致フィルタは `git -C <dir> push` / `eval "git push"`
# を取りこぼし、このスクリプトの検知に到達できなくなる) ので、このフックは全
# Bash 呼び出しで走る。_push_review_common.sh の strip_quoted_ranges は 1 文字ずつの
# 状態機械 = O(n^2) で、20KB のコマンドに 4.5 秒掛かる。
#
# 打ち切って安全な理由 (`push` / `send-pack` の判定が cmd_for_match への過剰近似に
# なっていること、必ず jq 復号後の $cmd を見ること、grep を -i で引くこと) は、
# _push_review_common.sh の hook_push_review_run の上のコメントに移してある。この
# 3 行は .codex 版と同一で、tests/test_hook_sync.py が一致を確かめる。
if ! printf '%s' "$cmd" | tr -d '\\"'"'"'\n' | grep -qiE 'push|send-pack'; then
  exit 0
fi

# ここまで来たのは push / send-pack を含むコマンドだけ。判定の本体はこの先で初めて読む
# (普通の Bash 呼び出しは上のガードで抜けるので、ライブラリをパースすらしない)。
#
# 読めない場合 (無い / 空 / 途中で切れている / 構文エラー / 関数の欠け) は fail-open
# にしない。jq の不在と同じ理由で、判定できないことは「push ではない」ことの証拠に
# ならず、ここで通すと push 手前の唯一のゲートが無言で消える。固定文の確認要求に縮退する。
# hook_push_review_run は _push_review_common.sh の最後の関数なので、それが定義されて
# いればファイルを最後まで読めている。`.` の終了コードも見るのは、全関数が定義済みでも
# 末尾が壊れているファイルを弾くため。
#
# 縮退時の stderr 1 行は人間向けの診断で、harness が読むのは stdout の JSON。
push_review_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
push_review_loaded=0
if [ -n "$push_review_dir" ]; then
  # shellcheck source=_push_review_common.sh
  . "$push_review_dir/_push_review_common.sh" 2>/dev/null && push_review_loaded=1
fi
if [ "$push_review_loaded" -eq 1 ]; then
  for push_review_fn in strip_span_counts hd_read_delim hd_read_body strip_quoted_ranges git_c_dir_from_words cd_target_from_words push_show build_push_report hook_push_review_run; do
    declare -F "$push_review_fn" >/dev/null 2>&1 || push_review_loaded=0
  done
fi

# 戻り値: 0 = 本文を push_review_body に入れた / 1 = push ではない / それ以外 = 想定外。
push_review_rc=unloaded
if [ "$push_review_loaded" -eq 1 ]; then
  hook_push_review_run "$input" "$cmd"
  push_review_rc=$?
fi

case "$push_review_rc" in
0) ;;
1) exit 0 ;;
*)
  # 理由文は固定文字列なので jq 無しで組み立ててよい (差し込み無し = エスケープ不要)。
  printf '%s\n' 'git-push-review.sh: could not load _push_review_common.sh from '"${push_review_dir:-<unresolved>}" >&2
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"git push detected, but the push-review library (_push_review_common.sh) could not be loaded, so this hook could not build the usual push summary. Review the push manually before pushing (see ~/.claude/rules/git-workflow.md)."}}'
  exit 0
  ;;
esac

reason="git push detected. Review before pushing (see ~/.claude/rules/git-workflow.md):
${push_review_body}"

jq -cn --arg reason "$reason" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $reason}}'
exit 0
