#!/bin/bash
# Codex Stop hook: 変更ファイルにデバッグ文が残っていないか最終監査する。
# 残留があれば exit 2 + stderr でエージェントに修正を促す(ブロック)。
# stop_hook_active が true の場合(block からの継続)は無限ループ防止のため即終了。
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、監査自体が
# 失敗しても Stop を止めてはならない。各コマンドの失敗は `|| exit 0` /
# `2>/dev/null` で個別に握りつぶし、最悪でも exit 0 で抜ける。

input=$(cat)

stop_active=$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$stop_active" = "true" ] && exit 0

# git リポジトリ外なら何もしない
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

# git diff/ls-files はフックの cwd を基点にスキャン範囲を決める(サブディレ
# クトリからだとそこ以下しか見ない)うえ、返すパスも cwd 相対になる。cwd が
# リポジトリのサブディレクトリでもリポジトリ全体を対象にリポジトリルート
# 相対パスで取得できるよう、ルートを解決して `-C` で明示的に指定する。
repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$repo_root" ] && exit 0

# 作業ツリーの変更ファイル + 未追跡ファイル(両者は排他なので重複しない)。
#
# -z が必須: 既定の core.quotePath が有効だと、引用符や非 ASCII を含むパスを
# `"evil\".ts"` のようにクォートして返す。そのままでは開けず、監査から黙って
# 漏れてしまう(見逃すゲートは、うるさいゲートより質が悪い)。
#
# NUL 区切りの一覧はコマンド置換では受け取れない(bash が NUL を捨てる)ため、
# プロセス置換でループへ直接流し込む。パイプにすると findings がサブシェルに
# 閉じ込められて失われるので使えない。
findings=""
while IFS= read -r -d '' f; do
  path="$repo_root/$f"
  [ -f "$path" ] || continue
  case "$f" in
  *.js | *.jsx | *.ts | *.tsx)
    # 直前除外は識別子文字 (英数字) のみとし `.` は含めない: `window.console.log(`
    # は window.console === console (ブラウザのグローバル) を指す実行可能な
    # デバッグ文なので検出対象にする。`myconsole.log(` のように console が
    # 別の識別子に融合しているケースは、直前が英数字のままなので引き続き除外される。
    hits=$(grep -nE '(^|[^[:alnum:]])console\.log\(|(^|[^[:alnum:]])debugger(;|$)' "$path" 2>/dev/null | head -5)
    ;;
  *.py)
    hits=$(grep -nE '(^|[^[:alnum:]])breakpoint\(\)|pdb\.set_trace\(\)' "$path" 2>/dev/null | head -5)
    ;;
  *)
    hits=""
    ;;
  esac
  [ -n "$hits" ] && findings="${findings}${f}:\n${hits}\n"
done < <(
  git -C "$repo_root" diff --name-only -z HEAD 2>/dev/null
  git -C "$repo_root" ls-files --others --exclude-standard -z 2>/dev/null
)

[ -z "$findings" ] && exit 0

# Codex の Stop フックでは exit 2 + stderr でブロックし、内容をエージェントに伝える
printf '%b' "Debug statements remain in modified files. Remove console.log / debugger / breakpoint() before finishing:\n${findings}" >&2
exit 2
