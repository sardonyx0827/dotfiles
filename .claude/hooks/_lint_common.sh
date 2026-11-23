#!/bin/bash
# _lint_common.sh
# lint.sh が共有する言語別 静的解析マトリクス。
#
# このファイルが唯一の実体。.codex/hooks/ 側には複製もリンクも無く、あちらの
# lint.sh が cd -P で ../../.claude/hooks を解決して直接読む。編集はここだけ。
# 契約(source 時に副作用を持たない / exit せず return する / hook_ 名前空間)は
# _hook_common.sh のヘッダを参照。hook_log を使うので、source する側は先に
# _hook_common.sh を読み込んでいること。
#
# ■ なぜ wrapper と分けるか
#
# 対象の決め方とプロトコルが .claude 版と .codex 版で本質的に違う:
#   - Claude: PostToolUse で .tool_input.file_path の 1 ファイル
#   - Codex : PostToolUse だが apply_patch のマーカーから複数ファイル。stdout は
#             構造化出力として解釈されるため exec 1>/dev/null で捨てる
# 一方「1 ファイルをどう解析するか」は完全に同一なので、共有するのはこの解析部分
# だけ。対象の集約・通知・終了コードの決定は wrapper に残す。
#
# ■ 失敗の判定方法が linter ごとに違う
#
# 大半は終了コードを見るが、clippy / checkstyle / cppcheck は「終了コード 0 の
# まま stdout に指摘を書く」ため出力を grep している。ここを取り違えると
# 「問題を見つけたのに通す」という最悪の壊れ方をするので、
# tests/test_lint_and_format.py::TestLintLanguageMatrix が両者を固定している。
#
# その grep 側に落とし穴が 2 つある (clippy / checkstyle / cppcheck)。
#
#   1. severity の綴り。clippy の lint は warn 既定、checkstyle の
#      google_checks.xml も severity=warning で、どちらも既定設定では "error"
#      を一度も出さない。error だけを探すと恒久的に緑になる。cppcheck は
#      --enable で要求した 4 カテゴリ全てを見ること。マッチさせるのは「ツールが
#      既定で出す綴り」であって、名前から連想する綴りではない。
#   2. 出力形式そのもの。cppcheck 1.x の `(severity)` を前提にした grep は、
#      既定が `severity:` に変わった 2.x に対して何一つマッチせず、(error) の
#      指摘ごと素通しする。既定形式に委ねると、ツール側の変更でゲートが
#      黙って死ぬ。grep する形式は --template で自分で固定する。
#
# スタブはツールの実出力から起こすこと。error 形状の出力しかスタブしないと、
# コードが既に正しく扱えるケースだけを固定することになり、カバレッジは
# 何の防波堤にもならない。
#
# Dockerfile / Containerfile (hadolint) と compose ファイル (`docker compose config`)
# も扱う。どちらもファイル名で振り分けるので、拡張子の分岐より前に basename で決める。
# tests/test_lint_hardening.py が、起動できない linter の扱い、tsc の tsconfig 解決、
# mypy / clippy の編集ファイルへの絞り込み、時間切れ、出力の上限、フォーマッタの設定の
# 尊重、symlink のスキップ、Docker の各契約を固定している。

# _go_dir_has_analyzable_package <dir>
#
# <dir> に「go ツールチェインが実際に解析できる Go パッケージ」があるかを、
# ツールチェイン自身に 1 つの肯定的な質問で尋ねる。解析できない理由は
# 「コードが間違っている」ではないので、区別せずに指摘として報告してはいけない。
#
# ツールの出力を grep して判定してはならない。診断メッセージ本文にその語が
# 含まれる本物のエラー — 例えば
#     var x int = "matched no packages"
#   → vet: ./main.go:4:14: cannot use "matched no packages" ... as int value
# — まで「検査できなかった」と誤分類し、コンパイルの通らないファイルに対して
# lint が緑を返す。
#
# `go env GOMOD` も使わない。GOPATH モード (GO111MODULE=off) では空を返すのに
# `go vet .` は正常に動くため、「空なら skip」にすると *全ファイルの検査が
# 黙って消える*。モジュール判定は、本当に知りたいこと (解析できるか) の
# 代理として不正確。
#
# 実測した go1.27 の応答 (files = GoFiles/TestGoFiles/XTestGoFiles の個数):
#   通常のパッケージ          files=1/0/0  rc=0  vet rc=0  → 解析する
#   複数ファイルのパッケージ  files=2/0/0  rc=0  vet rc=0  → 解析する
#   GOPATH モード             files=1/0/0  rc=0  vet rc=0  → 解析する
#   build 制約で全除外        files=0/0/0  rc=0  vet rc=1  → skip
#   名前に空白を含むディレクトリ files=0/0/0 rc=0 vet rc=1 → skip (malformed import path)
#   モジュール外              files=(空)   rc=1  vet rc=1  → skip
# 下 3 つはいずれも「vet は失敗するが、それは指摘ではない」ケース。ここを取り違えると
# `//go:build tools` や `//go:build integration` のような普通の書き方に対して
# 「存在しないエラーを直せ」とエージェントに指示することになる。
#
# cd -P で物理解決する: bash の論理 cd だと symlink 経由のパスのまま go に渡り、
# 判定と実行で見ているディレクトリがずれる。
_go_dir_has_analyzable_package() {
  command -v go >/dev/null 2>&1 || return 1
  local counts
  counts=$(cd -P "$1" 2>/dev/null &&
    go list -e -f '{{len .GoFiles}}{{len .TestGoFiles}}{{len .XTestGoFiles}}' . 2>/dev/null) ||
    return 1
  case "$counts" in
  "" | 000) return 1 ;;
  *) return 0 ;;
  esac
}

# ■ 「起動できなかった」と「指摘があった」を分ける (_lint_exec)
#
# 全ての linter 呼び出しは _lint_exec を通り、結果は 4 つに分かれる:
#   0  成功                 (終了コード 0)
#   1  指摘あり             (終了コード非 0。呼び出し側が出力を指摘として積む)
#   10 起動できなかった      (126 / 127、または pyenv / rustup 等ランチャーの定型文)
#   11 時間切れ             (hook_run_timeout が 124 を返した)
# 10 / 11 は「notice」としてログに残すだけで、指摘にはしない。pyenv shim の
# `command not found` や rustup の「toolchain 未選択」を exit 2 でエージェントに返すと、
# 受け取った側には直せない内容 (環境の問題) で編集がブロックされる。
#
# 起動失敗の判定は意図的に狭い: 終了コード 126/127 と、行頭から始まるランチャーの
# 定型文だけ。それ以外の非 0 (checkstyle の「設定が読めない」255 など) は従来どおり
# 指摘として扱う — 判定を広げると「ツールが動かなかった = 緑」で、このファイル冒頭が
# 戒めている壊れ方になる (test_grep_linter_invocation_failure_blocks /
# test_tsc_launch_failure_is_not_a_pass がそれを固定している)。定型文を行頭に限るのは、
# linter が引用したソース中の文言に誤反応しないため。
#
# hook_lint_file は呼び出しごとに、実際に走った linter の数を HOOK_LINT_RAN に、最後
# に走ったコマンドの終了コードを HOOK_LINT_LAST_RC に残す (どちらも大域変数)。
# 呼び出し元は HOOK_LINT_RAN が 0 のとき (対象外拡張子・未導入・notice だけ) に
# 「Lint Passed」を通知してはならない: 何も検査していないのに緑と告げることになる。

# _lint_tool_unavailable <exit_status> <output>
#
# ツールが「起動できなかった」ことを示すとき 0 を返す。判定の狭さの理由は上記。
_lint_tool_unavailable() {
  local rc="$1" out="$2"
  case "$rc" in
  126 | 127) return 0 ;;
  esac
  printf '%s\n' "$out" | grep -qE \
    -e '^(pyenv|rbenv|nodenv|jenv|goenv|phpenv): .*(command not found|is not installed)' \
    -e '^(asdf: No (preset )?version|No version is set for command |mise .*No version is set)' \
    -e '^error: (rustup could not choose a version|no override and no default toolchain|no such command: .clippy)' \
    -e "^error: .*is not installed for the toolchain" \
    -e '^(The operation couldn.t be completed\. )?Unable to locate a Java Runtime' \
    -e '^xcrun: error: invalid active developer path' \
    -e "^docker: .*is not a docker command"
}

# _lint_timeout <NAME> <default_seconds>
#
# HOOK_LINT_TIMEOUT_<NAME> が 0 より大きい数 (_hook_positive_number) ならそれを、
# 無い・数でない (`00` `.` `1.2.3` `abc` `0` `0.0`) なら既定の秒数を標準出力に書く。
# 不正な値を timeout に渡すと 125 が返って指摘になり、純 bash のフォールバックでは
# ツールが開始直後に殺される。かといって打ち切りなしで走らせると、止まらないツールが
# 編集のたびにフックを待たせる。だから「そのツールの既定の秒数」に戻す。
# `0` は「制限なし」にはならない (_lint_exec の第 1 引数の 0 は呼び出し側が直接
# 書くもので、環境変数からは来ない)。
_lint_timeout() {
  local var="HOOK_LINT_TIMEOUT_$1"
  if _hook_positive_number "${!var:-}"; then
    printf '%s' "${!var}"
  else
    printf '%s' "$2"
  fi
}

# _lint_exec <timeout_secs|0> <tool> <log_file> <out_var> <cwd|.> <command> [args...]
#
# <command> を (必要なら <cwd> へ移って、<timeout_secs> 秒で打ち切って) 実行し、
# stdout+stderr を <out_var> に入れる。戻り値は上の 0 / 1 / 10 / 11。10 / 11 は
# ここでログに notice を残す。<out_var> は呼び出し元のローカル (OUTPUT) を指すこと:
# printf -v は動的スコープで最も内側の同名変数に書くので、この関数は _l_ 接頭辞の
# ローカルしか持たない。
#
# cwd の変更は呼び出し元のシェルを動かさないよう、サブシェル (または timeout 下の
# bash -c) の中で行う。_lint_exec 自体は `$(...)` の中で呼ばないこと: HOOK_LINT_RAN
# の更新が親シェルに届かなくなる。
_lint_exec() {
  local _l_secs="$1" _l_tool="$2" _l_log="$3" _l_var="$4" _l_cwd="$5" _l_out _l_rc _l_head
  shift 5
  if [ "$_l_cwd" = "." ]; then
    if [ "$_l_secs" != 0 ]; then
      _l_out=$(hook_run_timeout "$_l_secs" "$@" 2>&1)
    else
      _l_out=$("$@" 2>&1)
    fi
  else
    if [ "$_l_secs" != 0 ]; then
      # /bin/bash -p: PATH の bash を使わず、BASH_ENV と環境の関数を読まない
      # (format-then-lint.sh の中の起動と同じ理由)。
      # shellcheck disable=SC2016 # $1 / $@ are for the inner bash, not this shell
      _l_out=$(hook_run_timeout "$_l_secs" /bin/bash -p -c 'cd "$1" || exit 127; shift; exec "$@"' _lint_cd "$_l_cwd" "$@" 2>&1)
    else
      _l_out=$(
        cd "$_l_cwd" 2>/dev/null || exit 127
        "$@" 2>&1
      )
    fi
  fi
  _l_rc=$?
  printf -v "$_l_var" '%s' "$_l_out"
  HOOK_LINT_LAST_RC="$_l_rc"
  if [ "$_l_secs" != 0 ] && [ "$_l_rc" -eq 124 ]; then
    hook_log "$_l_log" "NOTICE: $_l_tool timed out after ${_l_secs}s; skipped"
    return 11
  fi
  if [ "$_l_rc" -ne 0 ] && _lint_tool_unavailable "$_l_rc" "$_l_out"; then
    _l_head=$(printf '%s\n' "$_l_out" | head -n 1 | cut -c1-120)
    hook_log "$_l_log" "NOTICE: $_l_tool unavailable (could not start, exit $_l_rc): $_l_head; skipped"
    return 10
  fi
  HOOK_LINT_RAN=$((HOOK_LINT_RAN + 1))
  [ "$_l_rc" -eq 0 ] && return 0
  return 1
}

# _lint_lines_for_file <file> <cwd>
#
# 標準入力の各行のうち、先頭の `パス:` が <file> と同じファイルを指すものだけを
# 標準出力に書く。mypy (`path:line: error:`) と cargo clippy --message-format=short
# (`path:line:col: warning:`) の出力を編集ファイル分に絞るために使う。
# 比較は綴りではなく -ef (device + inode): ツールが相対パスや symlink 経由の見かけの
# パスで報告しても、同じファイルなら一致する。綴りの比較にすると診断が黙って消え、
# 「見つけたのに通す」になる (tsc の同名ファイル照合と同じ理由)。相対パスは <cwd> 基準。
_lint_lines_for_file() {
  local _l_f="$1" _l_dir="$2" _l_line _l_p
  while IFS= read -r _l_line; do
    _l_p="${_l_line%%:*}"
    [ "$_l_p" != "$_l_line" ] || continue
    case "$_l_p" in
    /*) ;;
    *) _l_p="$_l_dir/$_l_p" ;;
    esac
    if [ -f "$_l_p" ] && [ "$_l_p" -ef "$_l_f" ]; then
      printf '%s\n' "$_l_line"
    fi
  done
  return 0
}

# --- tsconfig の解決 (tsc) ---------------------------------------------------
#
# tsconfig.json は JSONC (コメント・末尾カンマ可) なので jq では読めない。必要な
# 3 つの事実 (solution-style か / references の path / include・exclude・files) だけを
# 1 行に潰したテキストから取り出す。厳密な JSON パーサではなく best effort: 判定が
# 付かなければ呼び出し側が notice にして、pass / fail のどちらも黙って出さない。

# _lint_json_array <flattened_json> <key>
#
# "<key>": [ "a", "b" ] の文字列要素を 1 行ずつ書く。入れ子の配列は扱わない。
_lint_json_array() {
  printf '%s' "$1" | sed -n 's/.*"'"$2"'"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' |
    grep -oE '"[^"]*"' | sed -e 's/^"//' -e 's/"$//'
}

# _lint_normalize_path <path>
#
# `/./` と `x/../` を畳む (ファイルが存在しなくても使えるよう、cd -P ではなく文字列で)。
_lint_normalize_path() {
  local p q n=0
  p=$(printf '%s' "$1" | sed -e 's#/\./#/#g' -e 's#/\.$##')
  while [ "$n" -lt 20 ]; do
    q=$(printf '%s' "$p" | sed 's#/[^/][^/]*/\.\./#/#')
    [ "$q" = "$p" ] && break
    p="$q"
    n=$((n + 1))
  done
  printf '%s' "$p"
}

# _lint_tsconfig_is_solution <tsconfig>
#
# solution-style (`files: []` か `include: []` と `references` だけを持ち、それ自体は
# 何も検査しない) なら 0。Vite の既定の tsconfig.json がこの形で、ルートに対する
# `tsc --noEmit` は何も検査せず常に緑になる。
_lint_tsconfig_is_solution() {
  local flat
  flat=$(tr '\n' ' ' <"$1" 2>/dev/null) || return 1
  case "$flat" in
  *'"references"'*) ;;
  *) return 1 ;;
  esac
  printf '%s' "$flat" | grep -qE '"(files|include)"[[:space:]]*:[[:space:]]*\[[[:space:]]*\]'
}

# _lint_tsconfig_references <tsconfig>
#
# references の各 path を、実在する tsconfig の絶対パスにして 1 行ずつ書く。
# path はファイルでもディレクトリ (= その下の tsconfig.json) でもよい。
_lint_tsconfig_references() {
  local cfg="$1" dir flat refs ref p
  dir=$(cd "$(dirname "$cfg")" 2>/dev/null && pwd -P) || return 0
  flat=$(tr '\n' ' ' <"$cfg" 2>/dev/null) || return 0
  refs=$(printf '%s' "$flat" |
    sed -n 's/.*"references"[[:space:]]*:[[:space:]]*\[\(.*\)\].*/\1/p' |
    grep -oE '"path"[[:space:]]*:[[:space:]]*"[^"]*"' |
    sed -E 's/^"path"[[:space:]]*:[[:space:]]*"//; s/"$//')
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    case "$ref" in
    /*) p="$ref" ;;
    *) p="$dir/$ref" ;;
    esac
    [ -d "$p" ] && p="$p/tsconfig.json"
    [ -f "$p" ] && printf '%s\n' "$(_lint_normalize_path "$p")"
  done <<<"$refs"
  return 0
}

# _lint_glob_matches <absolute_glob> <absolute_file>
#
# tsconfig の include / exclude の glob (`*` は `/` を越えない、`**/` は 0 個以上の
# ディレクトリ、`?` は 1 文字) をファイルの絶対パスに当てる。末尾にパスが続いても
# よい: tsc はディレクトリを指す include (`"src"`) を `src/**/*` として扱う。
_lint_glob_matches() {
  local re
  # 文字クラスの `[` は末尾に置く: `[][.` と書くと `[.` が照合要素の開始と読まれ、BSD sed
  # が "unbalanced brackets" で失敗する (GNU sed では通るので Linux の CI では見つからない)。
  re=$(printf '%s' "$1" | sed -E \
    -e 's/[].+(){}|^$\\[]/\\&/g' \
    -e 's#\*\*/#@DSL@#g' -e 's#\*\*#@DS@#g' -e 's#\*#[^/]*#g' -e 's#\?#[^/]#g' \
    -e 's#@DSL@#(.*/)?#g' -e 's#@DS@#.*#g')
  # 正規表現を作れなかったときは「一致しない」。空の正規表現は何にでも一致する。
  [ -n "$re" ] || return 1
  printf '%s\n' "$2" | grep -qE "^${re}(/.*)?\$"
}

# _lint_tsconfig_includes <tsconfig> <file>
#
# <tsconfig> のプロジェクトに <file> が含まれるとき 0。`files` の完全一致、`include`
# (無指定なら `**/*`) に当たり、かつ `exclude` と node_modules に当たらないこと。
# `extends` 経由の include は辿らない (best effort)。
_lint_tsconfig_includes() {
  local cfg="$1" file="$2" dir flat abs_file files include exclude pat abs matched=false
  dir=$(cd "$(dirname "$cfg")" 2>/dev/null && pwd -P) || return 1
  abs_file=$(cd "$(dirname "$file")" 2>/dev/null && printf '%s/%s' "$(pwd -P)" "$(basename "$file")") || return 1
  flat=$(tr '\n' ' ' <"$cfg" 2>/dev/null) || return 1
  files=$(_lint_json_array "$flat" files)
  include=$(_lint_json_array "$flat" include)
  exclude=$(_lint_json_array "$flat" exclude)

  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    abs=$(_lint_normalize_path "$dir/$pat")
    [ "$abs" = "$abs_file" ] && return 0
  done <<<"$files"

  case "$abs_file" in
  */node_modules/*) return 1 ;;
  esac
  if [ -z "$include" ] && [ -z "$files" ]; then
    include='**/*'
  fi
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    abs=$(_lint_normalize_path "$dir/$pat")
    if _lint_glob_matches "$abs" "$abs_file"; then
      matched=true
      break
    fi
  done <<<"$include"
  $matched || return 1
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    abs=$(_lint_normalize_path "$dir/$pat")
    _lint_glob_matches "$abs" "$abs_file" && return 1
  done <<<"$exclude"
  return 0
}

# _lint_pick_tsconfig_project <solution_tsconfig> <file>
#
# solution-style の references のうち、<file> を含む最初のプロジェクトの tsconfig を
# 標準出力に書いて 0 を返す。どれも含まなければ何も書かず 1 を返す。
_lint_pick_tsconfig_project() {
  local ref
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    if _lint_tsconfig_includes "$ref" "$2"; then
      printf '%s' "$ref"
      return 0
    fi
  done < <(_lint_tsconfig_references "$1")
  return 1
}

# --- compose の判定 ----------------------------------------------------------

# _lint_compose_environment_error <output>
#
# `docker compose config` の失敗が「compose ファイルの指摘」ではなく環境の問題
# (env_file が無い・daemon に繋がらない・プラグイン未導入・認証ヘルパー・コンテキスト)
# のとき 0。これらはエージェントが編集で直せないので notice にする。
_lint_compose_environment_error() {
  printf '%s\n' "$1" | grep -qiE \
    -e 'env file .* not found' \
    -e 'no such file or directory' \
    -e 'cannot connect to the docker daemon' \
    -e 'error during connect' \
    -e 'permission denied' \
    -e 'is the docker daemon running' \
    -e 'is not a docker command' \
    -e 'unknown (shorthand )?flag' \
    -e 'error getting credentials' \
    -e 'context .* (not found|does not exist)' \
    -e 'executable file not found'
}

# _lint_compose_partial_error <output>
#
# override / 環境別ファイル (compose.override.yml, compose.prod.yml) は base と合成して
# 使う前提の断片で、単体で `config` に掛けると「image も build も無い」「未定義の
# サービスに依存」と言われる。それは編集の指摘ではない。
_lint_compose_partial_error() {
  printf '%s\n' "$1" | grep -qiE \
    -e 'neither an image nor a build context' \
    -e 'depends on undefined service' \
    -e 'refers to undefined (service|volume|network|secret|config)' \
    -e 'undefined (service|volume|network|secret|config)'
}

# hook_lint_file <file> <errors_var_name> <log_file> [before-format]
#
# 1 ファイルを解析する。問題があれば errors_var_name で指定された変数に生の
# エラー文字列を入れて 1 を返す。無ければ空文字を入れて 0 を返す。
# 表示用の整形(ファイル名の見出しや区切り線)は呼び出し元の責務。
#
# 指摘が多すぎるときは hook_cap_output (_hook_common.sh) で先頭の 200 行 / 16KB に
# 切ってから返す (ログにも同じ切った内容が残る)。
#
# 戻り値 0 は「指摘なし」であって「検査した」ではない。何かが実際に走ったかは
# HOOK_LINT_RAN (大域) を見ること。上の「起動できなかった」と「指摘があった」を分ける
# 節を参照。
#
# 4 番目に before-format を渡すと、auto-format がまだ走っていないファイルとして
# 扱い、フォーマッターが直す類の指摘 (rubocop の Layout、ruff の import 整列) を
# 対象から外す。Claude は PostToolUse で auto-format → lint の順なので省略し、
# auto-format を Stop に置く Codex だけが渡す。外さないと、Stop で直る指摘が
# exit 2 でエージェントに返り、手で直させる無駄なターンになる。
#
# 注意: bash は動的スコープなので、errors_var_name にこの関数内の local と同じ
# 名前(LINT_ERRORS 等)を渡すと local 側に書き込まれ、呼び出し元には何も届かない。
# 静かに壊れるため下でガードしている。_l_ 接頭辞のローカルは全てガードの対象。

hook_lint_file() {
  local FILE_PATH="$1"
  local hook_out_var="$2"
  local hook_log_file="$3"
  local hook_phase="${4:-}"
  local EXTENSION="${FILE_PATH##*.}"
  local BASENAME
  BASENAME=$(basename "$FILE_PATH")
  local LINT_ERRORS=""
  # 指摘の区切りは実改行で積む (下の出力箇所の注記を参照)。
  local nl=$'\n'
  local PROJECT_ROOT HAS_ESLINT_CONFIG ESLINT_BIN TSC_BIN CONFIG OUTPUT HAS_MYPY_CONFIG RELATED cfg
  local GO_PKG_DIR eslint_dir eslint_root eslint_cwd tsc_line tsc_sep tsc_path
  local ruff_args=() rubocop_args=()
  # _l_ 接頭辞: この関数が追加したローカル。ガードは接頭辞でまとめて弾く。
  local _l_kind _l_cfg _l_sol _l_cwd _l_file _l_root _l_status _l_filtered _l_args=()

  # 出力変数名がこの関数の local と衝突すると、printf -v は local を書き換えて
  # しまい呼び出し元には何も返らない。黙って通るより落とす。
  case "$hook_out_var" in
  FILE_PATH | EXTENSION | BASENAME | LINT_ERRORS | PROJECT_ROOT | OUTPUT | \
    HAS_ESLINT_CONFIG | ESLINT_BIN | TSC_BIN | CONFIG | HAS_MYPY_CONFIG | RELATED | cfg | \
    GO_PKG_DIR | eslint_dir | eslint_root | eslint_cwd | tsc_line | tsc_sep | tsc_path | \
    ruff_args | rubocop_args | _l_* | HOOK_LINT_RAN | HOOK_LINT_LAST_RC | \
    hook_out_var | hook_log_file | hook_phase | nl)
    echo "hook_lint_file: output variable '$hook_out_var' collides with an internal local" >&2
    return 2
    ;;
  esac

  HOOK_LINT_RAN=0
  HOOK_LINT_LAST_RC=0

  hook_log "$hook_log_file" "--- lint start: $FILE_PATH ---"
  echo "Linting: $BASENAME"

  # Dockerfile / compose はファイル名で決まり、拡張子では決まらない (`Dockerfile` に
  # 拡張子は無く、`compose.yaml` の yaml は拡張子で見る意味が無い) ので、拡張子の
  # 分岐より前に basename で振り分ける。`Dockerfile.dockerignore` は BuildKit の
  # ファイルごとの無視リストで Dockerfile ではない。
  _l_kind=""
  case "$BASENAME" in
  *.dockerignore) ;;
  Dockerfile | Dockerfile.* | *.Dockerfile | Containerfile) _l_kind=dockerfile ;;
  compose.yml | compose.yaml | compose.*.yml | compose.*.yaml | \
    docker-compose.yml | docker-compose.yaml | docker-compose.*.yml | docker-compose.*.yaml)
    _l_kind=compose
    ;;
  esac
  [ -n "$_l_kind" ] && EXTENSION="@$_l_kind"

  case "$EXTENSION" in

  # Dockerfile / Containerfile
  @dockerfile)
    if command -v hadolint >/dev/null 2>&1; then
      echo "  Running hadolint..."
      # hadolint が読む設定は「cwd の .hadolint.yaml」と各ユーザ設定だけで、検査対象の
      # ファイルの場所は見ない (README)。cwd が git root でない・サブディレクトリの
      # Dockerfile では、チームが設定した ignore / failure-threshold が黙って無視される。
      # 編集ファイルから git root まで遡って最も近い設定を探し、--config で明示する。
      PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
      _l_root="$PROJECT_ROOT"
      [ -z "$_l_root" ] && _l_root=$(dirname "$FILE_PATH")
      _l_cfg=$(hook_find_nearest_file "$(dirname "$FILE_PATH")" "$_l_root" .hadolint.yaml .hadolint.yml)
      _l_args=(--no-color)
      [ -n "$_l_cfg" ] && _l_args+=(--config "$_l_cfg")
      _lint_exec "$(_lint_timeout HADOLINT 20)" hadolint "$hook_log_file" OUTPUT . \
        hadolint "${_l_args[@]}" "$FILE_PATH"
      case "$?" in
      0) echo "  hadolint passed" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[hadolint]${nl}${OUTPUT}${nl}" ;;
      esac
    else
      echo "  hadolint not found"
    fi
    ;;

  # compose ファイル
  @compose)
    if command -v docker >/dev/null 2>&1; then
      echo "  Running docker compose config..."
      # --no-interpolate: 環境変数が未設定でも失敗させない (変数の値はこの編集の
      # 責任ではない)。-q: 検証だけで展開結果は出さない。-f は絶対パスで渡す
      # (cwd をファイルのディレクトリへ移すため)。
      _l_file="$FILE_PATH"
      case "$_l_file" in
      /*) ;;
      *) _l_file="$PWD/$_l_file" ;;
      esac
      _lint_exec "$(_lint_timeout COMPOSE 15)" "docker compose" "$hook_log_file" OUTPUT \
        "$(dirname "$_l_file")" docker compose -f "$_l_file" config -q --no-interpolate
      _l_status=$?
      case "$_l_status" in
      0) echo "  docker compose config passed" ;;
      1)
        # 環境の問題 (env_file が無い・daemon に繋がらない等) は指摘ではない。
        # override / 環境別ファイルは単体では不完全と言われるのが普通なので、base
        # (compose.yaml / docker-compose.yml) 以外ではその種のエラーも指摘にしない。
        if _lint_compose_environment_error "$OUTPUT"; then
          # 検証は行われていないので「走った」に数えない (Lint Passed を出さない)。
          HOOK_LINT_RAN=$((HOOK_LINT_RAN - 1))
          hook_log "$hook_log_file" "NOTICE: docker compose config failed for an environment reason (not a finding): $(printf '%s\n' "$OUTPUT" | head -n 1 | cut -c1-120)"
        else
          case "$BASENAME" in
          compose.yml | compose.yaml | docker-compose.yml | docker-compose.yaml)
            LINT_ERRORS="${LINT_ERRORS}[docker compose]${nl}${OUTPUT}${nl}"
            ;;
          *)
            if _lint_compose_partial_error "$OUTPUT"; then
              HOOK_LINT_RAN=$((HOOK_LINT_RAN - 1))
              hook_log "$hook_log_file" "NOTICE: $BASENAME is a partial compose file (merged with a base); not judged on its own"
            else
              LINT_ERRORS="${LINT_ERRORS}[docker compose]${nl}${OUTPUT}${nl}"
            fi
            ;;
          esac
        fi
        ;;
      esac
    else
      echo "  docker not found"
    fi
    ;;

  # JavaScript / TypeScript
  js | jsx | ts | tsx)
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)

    # ESLint設定ファイルの存在確認。編集ファイルのディレクトリから PROJECT_ROOT
    # まで遡って探す (monorepo は packages/<pkg>/ に置く)。名前の一覧は ESLint が
    # 実際に読むものを揃える(漏れると、eslint があっても「config not found」で
    # 静かに緑を返す)。
    HAS_ESLINT_CONFIG=false
    if [ -n "$PROJECT_ROOT" ]; then
      eslint_dir=$(cd "$(dirname "$FILE_PATH")" 2>/dev/null && pwd -P)
      eslint_root=$(cd "$PROJECT_ROOT" 2>/dev/null && pwd -P)
      while [ -n "$eslint_dir" ]; do
        for cfg in eslint.config.js eslint.config.mjs eslint.config.cjs eslint.config.ts \
          .eslintrc .eslintrc.js .eslintrc.cjs .eslintrc.json .eslintrc.yml .eslintrc.yaml; do
          [ -f "$eslint_dir/$cfg" ] && HAS_ESLINT_CONFIG=true && break 2
        done
        [ "$eslint_dir" = "$eslint_root" ] && break
        [ "$eslint_dir" = "/" ] && break
        eslint_dir=$(dirname "$eslint_dir")
      done
    fi

    # ローカル解決は monorepo を考慮し、編集ファイルのディレクトリから
    # PROJECT_ROOT まで遡って一番近い node_modules/.bin/eslint を探す
    # (hook_find_nearest_bin, _hook_common.sh)。PROJECT_ROOT 直下しか見ないと、
    # eslint が packages/<pkg>/node_modules にしか無い構成で「ESLint not found」の
    # まま exit 0 を返す。上の設定探索と同じ monorepo 構成に合わせる。
    ESLINT_BIN=""
    if [ -n "$PROJECT_ROOT" ]; then
      ESLINT_BIN=$(hook_find_nearest_bin "$(dirname "$FILE_PATH")" "$PROJECT_ROOT" eslint)
    fi
    if [ -z "$ESLINT_BIN" ] && command -v eslint >/dev/null 2>&1; then
      ESLINT_BIN="eslint"
    fi

    if $HAS_ESLINT_CONFIG && [ -n "$ESLINT_BIN" ]; then
      echo "  Running ESLint ($ESLINT_BIN)..."
      # flat config (eslint.config.*) を ESLint 9 は lint 対象ではなく作業
      # ディレクトリから遡って探す。フックはプロジェクトルートで動くので、
      # packages/<pkg>/ にだけ flat config と eslint がある monorepo では
      # 「eslint.config が見つからない」で失敗し、問題の無いファイルを
      # ブロックする。上のループが見つけた config のディレクトリで実行する。
      # .eslintrc 系はファイル基準で探され、作業ディレクトリは .eslintignore
      # の探索に効くので、従来どおり動かさない。
      # 移動する前に対象を絶対パスにする (下の TSC_BIN と同じ理由): 相対パスの
      # まま渡すと移動先基準で読み直され、存在しないファイルとして eslint が
      # 失敗し、問題の無い編集をブロックする。
      # $(...) の中で case を使わないこと: macOS の bash 3.2 はパターンの `)`
      # をコマンド置換の終わりと誤読し、このファイルの読み込みごと失敗する
      # (shellcheck は通るので CI の静的検査では見つからない)。
      eslint_cwd="$PWD"
      case "$cfg" in eslint.config.*) eslint_cwd="$eslint_dir" ;; esac
      _l_file="$FILE_PATH"
      [ "${_l_file#/}" != "$_l_file" ] || _l_file="$PWD/$_l_file"
      _lint_exec 0 ESLint "$hook_log_file" OUTPUT "$eslint_cwd" "$ESLINT_BIN" "$_l_file"
      case "$?" in
      0) echo "  ESLint passed" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[ESLint]${nl}${OUTPUT}${nl}" ;;
      esac
    elif ! $HAS_ESLINT_CONFIG; then
      echo "  ESLint config not found, skipping"
    else
      echo "  ESLint not found"
    fi

    # TypeScriptの型チェック。
    #
    # 対象は「編集ファイルから git root へ遡って最初に見つかる tsconfig.json」。
    # git root の tsconfig.json しか見ないと (a) monorepo の packages/<pkg>/tsconfig.json
    # を無視し、(b) Vite の既定のような solution-style (`files: []` + `references`) では
    # ルートに対する `tsc --noEmit` が何も検査せず、型エラーがあっても緑になる。
    # solution-style のときは references のうち編集ファイルを含むプロジェクトを選ぶ。
    # `tsc -b` は使わない (ビルド成果物をホストのプロジェクトに書き出す)。決められなけ
    # れば notice にして、pass / fail のどちらも黙って出さない。
    if [[ "$EXTENSION" == "ts" || "$EXTENSION" == "tsx" ]]; then
      _l_cfg=""
      if [ -n "$PROJECT_ROOT" ]; then
        _l_cfg=$(hook_find_nearest_file "$(dirname "$FILE_PATH")" "$PROJECT_ROOT" tsconfig.json)
      fi
      if [ -n "$_l_cfg" ] && _lint_tsconfig_is_solution "$_l_cfg"; then
        _l_sol="$_l_cfg"
        _l_cfg=$(_lint_pick_tsconfig_project "$_l_sol" "$FILE_PATH")
        if [ -z "$_l_cfg" ]; then
          hook_log "$hook_log_file" "NOTICE: tsc skipped: $BASENAME is not included by any project referenced from $_l_sol; type check not run"
        fi
      fi
      if [ -n "$_l_cfg" ]; then
        # typescript は devDependency に入れるのが標準なので、tsc は PATH に
        # 載らない。`command -v tsc` だけで探すと、tsconfig.json のある普通の
        # npm プロジェクトで型チェックが丸ごと消え、型エラーのあるファイルに
        # 対してゲートが緑を返す ——「問題を見つけたのに通す」ではなく
        # 「そもそも見に行かない」版の、このファイル冒頭が戒めている壊れ方。
        # 上の ESLint と同じく node_modules/.bin を先に見る。PATH 側は
        # グローバル導入しかないプロジェクトのためのフォールバックとして残す。
        TSC_BIN=$(hook_find_nearest_bin "$(dirname "$FILE_PATH")" "$PROJECT_ROOT" tsc)
        if [ -z "$TSC_BIN" ] && command -v tsc >/dev/null 2>&1; then
          TSC_BIN="tsc"
        fi
        # ローカル解決した側は絶対パスなので、cwd を tsconfig のディレクトリへ移しても
        # 解決先は変わらない。tsc は診断を cwd 相対で書くので、下の照合は同じ
        # ディレクトリ (_l_cwd) を基準にする。git root を基準にすると、
        # packages/<pkg>/ の tsconfig では全ての -ef が外れ、黙って緑になる。
        if [ -n "$TSC_BIN" ]; then
          echo "  Running tsc (type check: $TSC_BIN -p $_l_cfg)..."
          _l_cwd=$(dirname "$_l_cfg")
          _lint_exec "$(_lint_timeout TSC 45)" tsc "$hook_log_file" OUTPUT "$_l_cwd" \
            "$TSC_BIN" --noEmit -p "$_l_cfg"
          _l_status=$?
          if [ "$_l_status" -eq 1 ]; then
            # 色付き出力 (tsconfig の "pretty": true) は色コードを落としてから
            # 扱う。残すとパスが `ESC[96msrc/a.ts ESC[0m:` と割れ、下の照合も
            # 起動失敗の判定 (`.ts:` の有無) も外れて、他ファイルだけのエラーで
            # tsc の全出力を報告してしまう。
            OUTPUT=$(printf '%s\n' "$OUTPUT" | sed $'s/\x1b\\[[0-9;]*m//g')
            # 変更ファイルに関連するエラーのみ抽出する。tsc は各診断を
            # `<cwd からのパス>(行,列)` か `<パス>:行:列` で始める。basename の
            # 部分一致だけで拾うと "index.ts" が "x.ts" を含むため x.ts の編集が
            # index.ts のエラーでブロックされ、完全一致でも Next.js の
            # app/*/page.tsx のような同名ファイルを区別できない。
            # そこで basename を含む行を候補とし、行頭のパス (最初に現れる
            # `<basename>(` / `<basename>:` までで、basename がパスの最終要素の
            # もの) が編集ファイルと同じファイルかを `-ef` (device + inode) で
            # 確かめる。綴りの比較にしないのは、tsc が include の glob でたどった
            # 見かけのパス (リポジトリ内の symlink `src/shared -> ../packages/shared`
            # なら src/shared/x.ts) で報告し、FILE_PATH も git の実パスもそれと
            # 一致するとは限らないため。綴りがずれると診断が黙って消え、他の行が
            # `.ts(` を含むので下の起動失敗判定も効かない = fail-open になる。
            # パス中の `(` (route group の `app/(marketing)/`) やメッセージ本文の
            # `(1,2)` に引きずられないよう、正規表現で切り出さず最初の出現で切る。
            # basename はクォートしてリテラル照合にする: Next.js の動的ルート
            # `[id].tsx` をパターンとして読むと `[id]` が文字クラスになり、自身の
            # エラー行に一致せず RELATED が空のまま return 0 ——「tsc が弾いた
            # コードでゲートが緑を返す」という、このファイル冒頭が戒めている
            # 最悪の壊れ方をする。
            RELATED=""
            while IFS= read -r tsc_line; do
              # 位置を持たない診断 (TS6059 "not under rootDir" 等) は行頭にパスが
              # 無く、ファイルは本文の '...' でだけ名指しされる。他ファイルの
              # 位置付き診断が 1 件でもあると下の起動失敗判定も効かないので、
              # 引用パスの最終要素が basename なら拾う。同名の別ファイルを拾う
              # 取り違えは報告過多 = 安全側に倒れる。
              case "$tsc_line" in
              "error TS"*)
                case "$tsc_line" in
                *"/$BASENAME'"* | *"'$BASENAME'"*) RELATED="${RELATED}${tsc_line}"$'\n' ;;
                esac
                continue
                ;;
              esac
              for tsc_sep in "(" ":"; do
                tsc_path="${tsc_line%%"$BASENAME$tsc_sep"*}"
                [ "$tsc_path" != "$tsc_line" ] || continue
                case "$tsc_path" in "" | */) ;; *) continue ;; esac
                tsc_path="$tsc_path$BASENAME"
                case "$tsc_path" in /*) ;; *) tsc_path="$_l_cwd/$tsc_path" ;; esac
                if [ "$tsc_path" -ef "$FILE_PATH" ]; then
                  RELATED="${RELATED}${tsc_line}"$'\n'
                  break
                fi
              done
            done < <(printf '%s\n' "$OUTPUT" | grep -F -- "$BASENAME")
            RELATED="${RELATED%$'\n'}"
            # tsc が起動すらできなかった (壊れた tsconfig.json → TS5083 等) 場合、
            # 出力はどのファイルも名指ししない。空の RELATED を「関連エラー無し」
            # と読むと、型エラーのあるファイルでゲートが緑を返す。ファイル名を
            # 1 つも含まない失敗出力は起動失敗として丸ごと報告する (他ファイル
            # だけのエラーは素通し。checkstyle / cppcheck と同じ扱い)。
            if [ -z "$RELATED" ] && ! echo "$OUTPUT" | grep -qE '\.tsx?[(:]'; then
              RELATED="$OUTPUT"
            fi
            if [ -n "$RELATED" ]; then
              LINT_ERRORS="${LINT_ERRORS}[TypeScript]${nl}${RELATED}${nl}"
            fi
          elif [ "$_l_status" -eq 0 ]; then
            echo "  tsc passed"
          fi
        fi
      fi
    fi
    ;;

  # Python
  py)
    # ruff: flake8/isort/pyupgrade互換の高速オールインワンlinter
    if command -v ruff >/dev/null 2>&1; then
      echo "  Running ruff check..."
      # I (import 整列) は auto-format の `ruff check --select I --fix` が直す。
      # 整形前 (before-format) にだけ外す。--ignore は設定側の ignore を消さない。
      [ "$hook_phase" = "before-format" ] && ruff_args=(--ignore I)
      # --force-exclude が必須: ruff は明示的に渡したパスには `exclude` /
      # `extend-exclude` を適用しない (フラグ無しだと `ruff check gen/a.py` は
      # exclude = ["gen"] を無視して解析する)。このフックは常に 1 ファイルを
      # 明示的に渡すので、フラグが無いとプロジェクトが除外したファイルまで
      # exit 2 で返し、`ruff check .` が通るプロジェクトで編集がブロックされる。
      # 除外されたファイルは ruff が何も解析せず終了コード 0 で返すので、ここでは
      # 「やることが無かった = 通過」として下の passed 分岐に落ちる (エラー扱いに
      # しない)。実 ruff での確認は tests の TestRuffForceExcludeRealRuff。
      _lint_exec 0 ruff "$hook_log_file" OUTPUT . ruff check "${ruff_args[@]}" --force-exclude "$FILE_PATH"
      case "$?" in
      0) echo "  ruff passed" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[ruff]${nl}${OUTPUT}${nl}" ;;
      esac
    else
      echo "  ruff not found"
    fi

    # bandit: セキュリティ脆弱性の検出
    if command -v bandit >/dev/null 2>&1; then
      echo "  Running bandit (security)..."
      # -ll: 中程度以上の重大度のみ報告
      _lint_exec 0 bandit "$hook_log_file" OUTPUT . bandit -ll -q "$FILE_PATH"
      case "$?" in
      0) echo "  bandit passed" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[bandit - security]${nl}${OUTPUT}${nl}" ;;
      esac
    else
      echo "  bandit not found"
    fi

    # mypy: 型チェック（mypy.iniかpyproject.tomlがある場合のみ）
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
    HAS_MYPY_CONFIG=false
    if [ -n "$PROJECT_ROOT" ]; then
      [ -f "$PROJECT_ROOT/mypy.ini" ] && HAS_MYPY_CONFIG=true
      [ -f "$PROJECT_ROOT/pyproject.toml" ] && grep -q "\[tool.mypy\]" "$PROJECT_ROOT/pyproject.toml" && HAS_MYPY_CONFIG=true
    fi
    if $HAS_MYPY_CONFIG && command -v mypy >/dev/null 2>&1; then
      echo "  Running mypy (type check)..."
      # mypy は import 先のモジュールも検査し、その既存の型エラーを同じ出力に混ぜる。
      # 全量を指摘にすると、無関係な既存エラーのせいで問題の無いファイルの編集が
      # ブロックされる。編集ファイルについての行だけに絞る。設定 (mypy.ini /
      # pyproject.toml) は cwd から探されるので、上で「設定がある」と判断した
      # プロジェクトルートで実行する。
      # 終了コード 2 は致命的エラー: 構文エラーのようにファイル自身の診断を伴う場合は
      # 指摘になり、どのファイルも名指ししない (設定が読めない・内部エラー) 場合は
      # 起動できなかったのと同じで notice にする。
      _lint_exec "$(_lint_timeout MYPY 30)" mypy "$hook_log_file" OUTPUT "$PROJECT_ROOT" \
        mypy "$FILE_PATH" --ignore-missing-imports
      _l_status=$?
      if [ "$_l_status" -eq 0 ]; then
        echo "  mypy passed"
      elif [ "$_l_status" -eq 1 ]; then
        _l_filtered=$(printf '%s\n' "$OUTPUT" | _lint_lines_for_file "$FILE_PATH" "$PROJECT_ROOT")
        if [ -n "$_l_filtered" ]; then
          LINT_ERRORS="${LINT_ERRORS}[mypy]${nl}${_l_filtered}${nl}"
        elif [ "$HOOK_LINT_LAST_RC" -ge 2 ]; then
          HOOK_LINT_RAN=$((HOOK_LINT_RAN - 1))
          hook_log "$hook_log_file" "NOTICE: mypy failed without naming $BASENAME (exit $HOOK_LINT_LAST_RC): $(printf '%s\n' "$OUTPUT" | head -n 1 | cut -c1-120)"
        else
          echo "  mypy: errors only in other files (ignored)"
        fi
      fi
    fi
    ;;

  # Rust
  rs)
    # clippy: Rustの公式linter（cargoが必要）
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
    if [ -n "$PROJECT_ROOT" ] && [ -f "$PROJECT_ROOT/Cargo.toml" ]; then
      if command -v cargo >/dev/null 2>&1; then
        echo "  Running cargo clippy..."
        # --message-format=short は診断を 1 行 `path:line:col: warning: ...` にする。
        # clippy は未編集ファイルの warning も全て出すので、編集ファイルについての
        # 行だけに絞る (パスは cargo の cwd = プロジェクトルート相対)。
        # clippy の lint は既定で warn レベルで、指摘があっても終了コードは 0。
        # 終了コードではなく出力を見る点は cppcheck 側と同じで、コンパイルエラー
        # (101) の出力も同じ経路で読む。
        # 終了コードが非 0 なのに `path:line:col:` の診断が出力のどこにも無いときは、
        # clippy はこのファイルを見ていない (manifest が読めない・registry に届かない
        # 等)。他ファイルの診断が見えている場合 (= プロジェクトは解析された) とは
        # 別物で、緑にも、実行した数にも入れない。mypy の「どのファイルも名指ししない
        # 失敗」と同じ扱い。診断の形は --message-format=short で自分で固定している。
        _lint_exec "$(_lint_timeout CLIPPY 45)" clippy "$hook_log_file" OUTPUT "$PROJECT_ROOT" \
          cargo clippy --quiet --message-format=short
        _l_status=$?
        if [ "$_l_status" -eq 0 ] || [ "$_l_status" -eq 1 ]; then
          _l_filtered=$(printf '%s\n' "$OUTPUT" | _lint_lines_for_file "$FILE_PATH" "$PROJECT_ROOT")
          if [ -n "$_l_filtered" ]; then
            LINT_ERRORS="${LINT_ERRORS}[clippy]${nl}${_l_filtered}${nl}"
          elif [ "$_l_status" -eq 1 ] && ! printf '%s\n' "$OUTPUT" | grep -qE '^[^:]+:[0-9]+:[0-9]+:'; then
            HOOK_LINT_RAN=$((HOOK_LINT_RAN - 1))
            hook_log "$hook_log_file" "NOTICE: cargo clippy did not complete (exit $HOOK_LINT_LAST_RC, no diagnostics at all): $(printf '%s\n' "$OUTPUT" | grep -m1 '[^[:space:]]' | cut -c1-120); skipped"
          else
            [ "$_l_status" -eq 1 ] && hook_log "$hook_log_file" "NOTICE: cargo clippy exited $HOOK_LINT_LAST_RC with no diagnostics for $BASENAME (diagnostics elsewhere ignored)"
            echo "  cargo clippy passed"
          fi
        fi
      fi
    else
      echo "  Cargo.toml not found, skipping clippy"
    fi
    ;;

  # Go
  #
  # 検査対象はファイルではなく「そのファイルが属するパッケージ」= 親ディレクトリ。
  # Go は識別子をパッケージ単位でしか解決できないため、単一ファイルを渡すと
  # 同一パッケージの兄弟ファイルで定義された識別子が軒並み `undefined` になる。
  # 実測: main.go が helper.go の helper() を呼ぶ 2 ファイル構成で
  #   go vet main.go -> exit 1 "undefined: helper"
  #   go vet -C <dir> . -> exit 0
  # つまり正しいコードに対して exit 2 を返し、エージェントに存在しないエラーの
  # 修正を指示することになる。さらに、コンパイル失敗が先に立つので vet 本来の
  # 指摘 (Printf の型不一致など) は表に出ない。
  # 1 ファイルだけのパッケージ以外、事実上あらゆる Go プロジェクトで発症する。
  #
  # 副作用として、main.go を編集すると helper.go 由来の既存の指摘も出る。
  # パッケージ単位の解析としては正しい挙動で、意図したもの。
  go)
    GO_PKG_DIR=$(dirname "$FILE_PATH")
    # 解析可能かの判定は両ツールより先に、かつ一度だけ。「ツールチェインが
    # このディレクトリを見られない」と「見た結果 指摘が出た」を、出力の文言では
    # なくこの分岐で切り分ける。staticcheck も go ツールチェインを必要とする
    # (go 不在では `err: go command required, not found` を出すだけ) ので、
    # 両方まとめてここで止める。
    if ! command -v go >/dev/null 2>&1; then
      echo "  go vet / staticcheck skipped (go not found)"
    elif ! _go_dir_has_analyzable_package "$GO_PKG_DIR"; then
      echo "  go vet / staticcheck skipped (no Go package here that the toolchain can analyse)"
    else
      echo "  Running go vet..."
      # -C はツール自身に chdir させるので、このフックの cwd を汚さない。
      _lint_exec 0 "go vet" "$hook_log_file" OUTPUT . go vet -C "$GO_PKG_DIR" .
      case "$?" in
      0) echo "  go vet passed" ;;
      1)
        # -C 配下の出力はパッケージ相対 (main.go:6:14) になるため、
        # 受け取ったエージェントがファイルへ辿れるようディレクトリを添える。
        LINT_ERRORS="${LINT_ERRORS}[go vet] (in ${GO_PKG_DIR})${nl}${OUTPUT}${nl}"
        ;;
      esac

      # staticcheck: go vet より高度な解析
      # staticcheck に -C は無いので cwd を移して実行する (親シェルの cwd は不変)。
      # 物理パスへ移るのは判定側と揃えるため必須: 論理 cd だと symlink 経由のディレクトリで
      # staticcheck が `warning: "." matched no packages` を出して rc=0 で返り、
      # 何も解析していないのに緑になる。
      if command -v staticcheck >/dev/null 2>&1; then
        echo "  Running staticcheck..."
        _l_cwd=$(cd -P "$GO_PKG_DIR" 2>/dev/null && pwd -P)
        [ -n "$_l_cwd" ] || _l_cwd="$GO_PKG_DIR"
        _lint_exec 0 staticcheck "$hook_log_file" OUTPUT "$_l_cwd" staticcheck .
        case "$?" in
        0) echo "  staticcheck passed" ;;
        1) LINT_ERRORS="${LINT_ERRORS}[staticcheck] (in ${GO_PKG_DIR})${nl}${OUTPUT}${nl}" ;;
        esac
      else
        echo "  staticcheck not found (optional)"
      fi
    fi
    ;;

  # Java
  java)
    # checkstyle: コーディング規約チェック
    if command -v checkstyle >/dev/null 2>&1; then
      echo "  Running checkstyle..."
      # プロジェクトにcheckstyle.xmlがあればそれを使用、なければGoogle規約。
      # 既定値は "google" ではなく "/google_checks.xml"。前者は解決できない
      # (`Could not find config XML file 'google'.` / exit 255)。jar 同梱の
      # 設定は classpath リソースなので先頭スラッシュ付きで指定する。
      # test_checkstyle_default_config_resolves。
      PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
      CONFIG="/google_checks.xml"
      [ -f "$PROJECT_ROOT/checkstyle.xml" ] && CONFIG="$PROJECT_ROOT/checkstyle.xml"
      # 終了コード非 0 は「起動できなかった」(設定が読めない・不正な引数)。
      # その旨のメッセージに [ERROR]/[WARN] は乗らないので、下の grep だけでは
      # 指摘ゼロと区別が付かず緑を返してしまう。ゲートが黙って無効化される
      # 経路なので、出力を見る前に潰す。ただし 126/127 と pyenv 等の定型文は
      # 環境の問題 (ツールが無い) なので _lint_exec が notice にする。
      _lint_exec 0 checkstyle "$hook_log_file" OUTPUT . checkstyle -c "$CONFIG" "$FILE_PATH"
      _l_status=$?
      if [ "$_l_status" -eq 1 ]; then
        LINT_ERRORS="${LINT_ERRORS}[checkstyle]${nl}${OUTPUT}${nl}"
      elif [ "$_l_status" -eq 0 ]; then
        # 既定の google_checks.xml は severity=warning なので、指摘は [WARN] で
        # 出力され [ERROR] は現れない。[ERROR] だけを見ると、既定設定で拾えた
        # 指摘を 1 件残らず素通しすることになる。[INFO] は指摘ではないので除く。
        if echo "$OUTPUT" | grep -qE "\[ERROR\]|\[WARN\]"; then
          LINT_ERRORS="${LINT_ERRORS}[checkstyle]${nl}${OUTPUT}${nl}"
        else
          echo "  checkstyle passed"
        fi
      fi
    else
      echo "  checkstyle not found"
    fi
    ;;

  # C / C++
  c | cpp | cc | cxx | h | hpp)
    if command -v cppcheck >/dev/null 2>&1; then
      echo "  Running cppcheck..."
      # --template で出力形式を固定する。cppcheck 1.x の既定は
      # `[file:line]: (severity) message`、2.x は `file:line:col: severity: message`
      # で、既定に委ねると括弧付きの綴りを探す下の grep が何もマッチせず、
      # (error) の指摘ごと素通しして常に緑になる。grep する形式はこちらで決める。
      # tests の test_cppcheck_template_and_matcher_agree がこの固定を守る。
      # --template-location も必須。--template だけ渡すと、指摘に付く補足
      # (nullPointer なら「Assignment 'p=0', assigned value is 0」等、修正に
      # 一番効く情報) が丸ごと落ちる。matcher には掛からない綴りなので誤検知
      # にはならず、LINT_ERRORS の文脈だけが増える。
      #
      # 終了コード非 0 は「起動できなかった」(不正な引数・ファイルが開けない)。
      # cppcheck は指摘があっても 0 で終わるので、非 0 は指摘ではなく事故。
      # そのメッセージ (`cppcheck: error: unrecognized command line option ...`)
      # は下の grep にかからないため、潰さないと「起動失敗 = 緑」になる。
      # 上の --template を打ち間違えた瞬間にゲートが黙って死ぬ経路でもある。
      _lint_exec 0 cppcheck "$hook_log_file" OUTPUT . cppcheck --enable=warning,style,performance,portability \
        --suppress=missingInclude \
        --template='{file}:{line}:{column}: ({severity}) {message} [{id}]' \
        --template-location='{file}:{line}:{column}: note: {info}' \
        "$FILE_PATH"
      _l_status=$?
      if [ "$_l_status" -eq 1 ]; then
        LINT_ERRORS="${LINT_ERRORS}[cppcheck]${nl}${OUTPUT}${nl}"
      elif [ "$_l_status" -eq 0 ]; then
        # --enable で有効化した 4 カテゴリと error を漏れなく拾う。note 行は上の
        # --template-location 側の綴りで出るため、ここには掛からない
        # (指摘本体だけが判定に効く)。
        if echo "$OUTPUT" | grep -qE "\((error|warning|style|performance|portability)\)"; then
          LINT_ERRORS="${LINT_ERRORS}[cppcheck]${nl}${OUTPUT}${nl}"
        else
          echo "  cppcheck passed"
        fi
      fi
    else
      echo "  cppcheck not found"
    fi
    ;;

  # Ruby
  rb)
    # rubocop: フォーマットとlintを兼ねる。Claude では auto-format.sh の
    # --auto-correct が先に走っているので、ここに残るのは直せなかった指摘だけ。
    # Codex (before-format) ではまだ整形されていないので、Stop の --auto-correct
    # が直す Layout を外し、それ以外をフィードバックする。
    if command -v rubocop >/dev/null 2>&1; then
      echo "  Running rubocop (lint only)..."
      [ "$hook_phase" = "before-format" ] && rubocop_args=(--except Layout)
      _lint_exec 0 rubocop "$hook_log_file" OUTPUT . rubocop --no-color "${rubocop_args[@]}" "$FILE_PATH"
      case "$?" in
      0) echo "  rubocop passed" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[rubocop]${nl}${OUTPUT}${nl}" ;;
      esac
    else
      echo "  rubocop not found"
    fi
    ;;

  # PHP
  php)
    # phpstan: 型推論ベースの高精度静的解析
    if command -v phpstan >/dev/null 2>&1; then
      echo "  Running phpstan..."
      _lint_exec 0 phpstan "$hook_log_file" OUTPUT . phpstan analyse "$FILE_PATH" --no-progress
      case "$?" in
      0) echo "  phpstan passed" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[phpstan]${nl}${OUTPUT}${nl}" ;;
      esac
    # php -l: 構文チェックのみ（フォールバック）
    elif command -v php >/dev/null 2>&1; then
      echo "  Running php -l (syntax check)..."
      _lint_exec 0 php "$hook_log_file" OUTPUT . php -l "$FILE_PATH"
      case "$?" in
      0) echo "  php syntax OK" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[php syntax]${nl}${OUTPUT}${nl}" ;;
      esac
    else
      echo "  phpstan / php not found"
    fi
    ;;

  # Shell scripts
  sh | bash)
    if command -v shellcheck >/dev/null 2>&1; then
      echo "  Running shellcheck..."
      _lint_exec 0 shellcheck "$hook_log_file" OUTPUT . shellcheck -x -P SCRIPTDIR "$FILE_PATH"
      case "$?" in
      0) echo "  shellcheck passed" ;;
      1) LINT_ERRORS="${LINT_ERRORS}[shellcheck]${nl}${OUTPUT}${nl}" ;;
      esac
    else
      echo "  shellcheck not found"
    fi
    ;;

  *)
    echo "  No linter configured for .$EXTENSION files"
    ;;
  esac

  if [ -n "$LINT_ERRORS" ]; then
    # 全量をそのまま返すと大きなプロジェクトでは数百 KB になり、1 回の編集のたびに
    # モデルのコンテキストを食う。先頭の 200 行 / 16KB に切る (hook_cap_output)。
    # $(...) は末尾の改行を落とすので、1 つだけ付け直す (出力は常に改行で終わる)。
    LINT_ERRORS=$(printf '%s' "$LINT_ERRORS" | hook_cap_output)
    LINT_ERRORS="${LINT_ERRORS}${nl}"
    hook_log "$hook_log_file" "FAILED: $BASENAME"
    # 指摘は最初から実改行 ($nl) で積み、そのまま書き出す。区切りをリテラルの `\n` で
    # 積んで最後に一括で改行へ戻す形は、linter が引用したソース中の `\n`
    # (`print("x\ny")` や `C:\new`) まで改行に変えて断片を壊していた。`%b` も
    # 使わない: `\c` を「以降の出力を打ち切る」と解釈し、後続の指摘が消える。
    printf '%s' "$LINT_ERRORS" >>"$hook_log_file"
    printf -v "$hook_out_var" '%s' "$LINT_ERRORS"
    return 1
  fi

  hook_log "$hook_log_file" "PASSED: $BASENAME"
  if [ "$HOOK_LINT_RAN" -gt 0 ]; then
    echo "All lint checks passed for $BASENAME"
  else
    echo "No lint check ran for $BASENAME"
  fi
  printf -v "$hook_out_var" '%s' ""
  return 0
}
