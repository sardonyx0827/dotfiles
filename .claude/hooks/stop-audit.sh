#!/bin/bash
# Claude Code Stop hook: 変更ファイルにデバッグ文が残っていないか最終監査する。
# 残留があれば decision:block で Claude に修正を促す。
#
# 見るのは「作業ツリーが HEAD に対して追加した行」だけ (hook_git_added_lines,
# _hook_common.sh)。未追跡・新規ファイルは全行が追加なので全体を見る。ファイル全体を
# 見ると、コミット済みの正当な console.log (CLI の出力、ロガー) があるだけで、その
# ファイルが dirty の間は毎ターン block してしまう。対象は .js/.jsx/.ts/.tsx に加えて
# .mjs/.cjs/.mts/.cts/.vue と、Python の .py。
# stop_hook_active が true の場合(block からの継続)は無限ループ防止のため即終了。
#
# 意図的に `set -e` は使わない: このフックは fail-open 設計であり、監査自体が
# 失敗しても Stop を止めてはならない。各コマンドの失敗は `|| exit 0` /
# `2>/dev/null` で個別に握りつぶし、最悪でも exit 0 で抜ける。

input=$(cat)

# 共有ヘルパー (hook_git_changed_files)。lint.sh / auto-format.sh と同じ読み込み方
# (実体と単一コピー方針は _hook_common.sh のヘッダを参照)。変更ファイルの選び方を
# 各フックが個別に持っていた頃は、unborn branch への対応が stop-audit.sh にだけ入り
# Codex の auto-format.sh / lint.sh に入らないままだった。
# 読めなければ、下の jq 不在と同じく fail-open: 監査を諦めて抜ける。ここで
# 「command not found」のまま進むと、対象一覧が空になって何も検出せず exit 0 に
# なるが、それは「問題なし」と区別が付かない。理由を stderr に残して抜ける。
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_common.sh
. "$HOOK_DIR/_hook_common.sh"
if ! declare -F hook_git_changed_files >/dev/null 2>&1 ||
  ! declare -F hook_git_added_lines >/dev/null 2>&1; then
  echo "stop-audit: could not load _hook_common.sh from ${HOOK_DIR:-<unresolved>}; debug-statement audit skipped" >&2
  exit 0
fi

# jq が無いと stop_hook_active を読めない。読めないまま監査を続けると、block
# からの継続でもフラグが空 = false 扱いで再び block し、Stop が終わらなくなる。
# lint.sh / auto-format.sh と同じく jq 不在は監査しない。
command -v jq >/dev/null 2>&1 || {
  echo "stop-audit: jq not found on PATH; debug-statement audit skipped" >&2
  exit 0
}

stop_active=$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$stop_active" = "true" ] && exit 0

# エディタ (vim / nvim) の AI 機能から起動された一発呼び出しは監査しない。
# あれはコミットメッセージ生成やバッファ校正のような *生成専用* の呼び出しで、
# 作業ツリーを直す権限も意図もない。ここでブロックすると受け手はそれを指示と
# 解釈し、実際にユーザの未コミットの変更を書き換えたうえで、生成物の代わりに
# 「デバッグ文を削除しました」という文章を返す — 出力が壊れ、かつ誰も頼んで
# いない変更がリポジトリに入る。
#
# 対話セッションでこの監査が正しいのは「エージェント自身が書いたコードを、
# 終わる前に見直させる」からで、一発呼び出しにその前提はない。監査対象は
# ユーザ自身の作業ツリーであり、それはまさにコミットメッセージを作る対象。
# 変数は呼び出し側 (ai/backend.lua の ONESHOT_ENV、.vim/rc/70-ai.vim の
# s:ai_oneshot_env) がコマンド文字列で立てる。
[ -n "$EDITOR_AI_ONESHOT" ] && exit 0

# git リポジトリ外なら何もしない
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

# git diff/ls-files はフックの cwd を基点にスキャン範囲を決める(サブディレ
# クトリからだとそこ以下しか見ない)うえ、返すパスも cwd 相対になる。cwd が
# リポジトリのサブディレクトリでもリポジトリ全体を対象にリポジトリルート
# 相対パスで取得できるよう、ルートを解決して `-C` で明示的に指定する。
repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$repo_root" ] && exit 0

# 作業ツリーの変更ファイル + 未追跡ファイル(両者は排他なので重複しない)は
# hook_git_changed_files (_hook_common.sh) が選ぶ。最初のコミット前 (HEAD が無い
# unborn branch) でもステージ済みの内容を拾う理由、-z で NUL 区切りにする理由
# (core.quotePath でクォートされたパスは開けず黙って漏れる。見逃すゲートは、
# うるさいゲートより質が悪い) はそちらを参照。
#
# NUL 区切りの一覧はコマンド置換では受け取れない(bash が NUL を捨てる)ため、
# プロセス置換でループへ直接流し込む。パイプにすると findings がサブシェルに
# 閉じ込められて失われるので使えない。
#
# 報告は量を限る: 一致行は 1 行 200 バイトまで (cut_hit)、ファイルは max_files 件まで。
# 一致行を丸ごと引用していた頃は、minify 済み JS の 1 行 (数百 KB) が理由文に入り、
# 改行の後処理が macOS の bash 3.2 では文字列長の 2 乗時間になって Stop が分単位で
# 止まった。1 MB を超えると `jq --arg` が E2BIG で失敗し、何も出力せず exit 0 —
# つまり監査が黙って素通りした。何が残っているかを伝えるのに行の全文は要らない。
# cut を C ロケールで走らせるのは、BSD cut が不正なバイト列で入力を捨てるため。
# バイトで切ると多バイト文字の途中で切れうるので、行末に残った不完全な UTF-8 列
# (先行バイトだけ / 続きバイトが足りない並び) を sed で落とす。残すと報告が不正な
# UTF-8 になり、読む側 (Codex は stderr をそのまま読む) で復号に失敗する。完結した
# 文字で終わる行には一致しない。整えるのは切り口だけで、元のファイルが不正な
# バイトを含む場合はそのまま出る (こちらは jq が U+FFFD に直す。Codex 側は生のまま)。
# 行頭から切るので、長い 1 行では一致箇所が 200 バイトより後ろにあると抜粋に
# 現れない。その場合もファイル名と行番号は出る。
max_files=20
partial_utf8_tail=$'s/([\xC2-\xDF]|[\xE0-\xEF][\x80-\xBF]?|[\xF0-\xF4][\x80-\xBF]{0,2})$//'
cut_hit() { LC_ALL=C cut -c1-200 | LC_ALL=C sed -E "$partial_utf8_tail"; }
# 報告は最初から実改行で組む。`\n` の 2 文字で繋いで最後に置換する形は、検出行に
# 書かれていた `\n` (文字列リテラルの中など) まで改行に変えてしまう。
nl=$'\n'
findings=""
found=0
while IFS= read -r -d '' f; do
  path="$repo_root/$f"
  [ -f "$path" ] || continue
  case "$f" in
  *.js | *.jsx | *.ts | *.tsx | *.mjs | *.cjs | *.mts | *.cts | *.vue)
    # 直前除外は識別子文字 (英数字) のみとし `.` は含めない: `window.console.log(`
    # は window.console === console (ブラウザのグローバル) を指す実行可能な
    # デバッグ文なので検出対象にする。`myconsole.log(` のように console が
    # 別の識別子に融合しているケースは、直前が英数字のままなので引き続き除外される。
    #
    # debugger 側の直後条件は `(;|$)` ではなく「識別子文字以外 or 行末」で判定する。
    # debugger は文ではなくキーワードなので `;` も行末も必須ではなく、
    # `if (x) { debugger }` や `debugger // remove me` のように同一行に他の
    # トークンが続く形が実際に最も多く、`(;|$)` だとそれらを取りこぼす。
    # 融合判定の文字クラスに `_` と `$` を足すのは、JS の識別子文字だから
    # (`debugger_x` / `$debugger` は別の識別子であってデバッグ文ではない)。
    #
    # grep は LC_ALL=C で走らせる (cut_hit と同じ)。利用者の UTF-8 ロケールのままだと、
    # macOS の BSD grep は UTF-8 として不正なバイト (Shift_JIS の文字列、バイナリ片) を
    # 検出語より前に含む行を黙って読み飛ばし、監査が素通りする。副作用として、C
    # ロケールでは非 ASCII の文字が [:alnum:] に入らないので、`日本語console.log(` の
    # ように非 ASCII の文字が密着した形も境界ありとして拾う (過検出の方向だけ。JS では
    # 別の識別子だが、実在のコードではまれなので許容する)。
    hits=$(hook_git_added_lines "$repo_root" "$f" | LC_ALL=C grep -E '(^|[^[:alnum:]])console\.log\(|(^|[^[:alnum:]_$])debugger([^[:alnum:]_$]|$)' 2>/dev/null | head -5 | cut_hit)
    ;;
  *.py)
    hits=$(hook_git_added_lines "$repo_root" "$f" | LC_ALL=C grep -E '(^|[^[:alnum:]])breakpoint\(\)|pdb\.set_trace\(\)' 2>/dev/null | head -5 | cut_hit)
    ;;
  *)
    hits=""
    ;;
  esac
  if [ -n "$hits" ]; then
    found=$((found + 1))
    [ "$found" -le "$max_files" ] && findings="${findings}${f}:${nl}${hits}${nl}"
  fi
done < <(hook_git_changed_files "$repo_root")

[ "$found" -eq 0 ] && exit 0

if [ "$found" -gt "$max_files" ]; then
  findings="${findings}(and $((found - max_files)) more files with debug statements)${nl}"
fi
# 検出行はそのまま渡す (`printf %b` のようなエスケープ解釈を挟まない: `\c` は
# 「以降の出力を打ち切る」で、検出行に `\c` があると後続のファイルが消える)。
# 末尾の改行は落とす (findings は必ず改行で終わる。残すと reason の末尾に空行が増える)。
reason="Debug statements remain in modified files. Remove console.log / debugger / breakpoint() before finishing:${nl}${findings%"$nl"}"
jq -cn --arg reason "$reason" '{decision: "block", reason: $reason}'
exit 0
