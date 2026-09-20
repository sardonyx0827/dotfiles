#!/bin/bash
# PostToolUse (Write|Edit|MultiEdit) の整形 → 静的解析を「この順で」走らせる。
#
# 以前は settings.json の同一 matcher の hooks 配列に auto-format.sh と lint.sh
# を並べていた。これは順序を保証しない: Claude Code は同一イベントにマッチした
# ハンドラを並列に走らせ、公式ドキュメントも "Since hooks run in parallel, the
# order is non-deterministic" と明記している。一方 lint.sh は冒頭で「auto-format.sh
# 実行後を想定」と宣言し、_lint_common.sh も整形済みを前提に ruff の import 順
# (I001) や rubocop の Layout を指摘する。順序が崩れると、フォーマッタが今まさに
# 直している最中の指摘が exit 2 でエージェントに返り、手で直させる無駄なターンを
#生む。1 本のハンドラへ畳んで逐次性を取り戻す。
#
# 素朴に `auto-format.sh && lint.sh` と 1 行に並べられないのは、両者とも jq で
# stdin を直接読むため。1 つ目がペイロードを食い切り、2 つ目は空を受け取って
# 「file path 無し」で素通りする = 静的解析が事実上消える。一時ファイルへ落として
# 両方へ配る。
#
# 意図的に `set -e` は使わない: 中身の 2 本と同じ fail-open 方針で、ここで
# 異常終了すると編集そのものがブロックされる。

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# mktemp が失敗する環境 (TMPDIR が無い等) では何もせず通す。ペイロードを
# 配れない以上どちらのフックも仕事ができないので、黙って fail-open する。
payload=$(mktemp "${TMPDIR:-/tmp}/claude-post-edit.XXXXXX") || exit 0
trap 'rm -f "$payload"' EXIT
cat >"$payload"

# auto-format の失敗で lint を飛ばさない。lint はゲート (exit 2 でエージェントに
# 差し戻す側) なので、整形が転んだことを理由にゲートごと素通りさせる方が危険。
# auto-format.sh 自身は fail-open で常に 0 を返す設計なので、ここに入るのは
# 想定外の異常だけ。その事実は stderr に残す。
if ! bash "$HOOK_DIR/auto-format.sh" <"$payload"; then
  echo "format-then-lint.sh: auto-format.sh exited $? (continuing to lint)" >&2
fi

# 最後に実行されるコマンドなので、lint.sh の終了コード (0 か 2) が
# そのままこのフックの終了コードになる。ゲートの判定を握り潰さない。
bash "$HOOK_DIR/lint.sh" <"$payload"
