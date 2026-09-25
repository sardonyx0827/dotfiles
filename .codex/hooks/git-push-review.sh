#!/bin/bash
# Codex PreToolUse hook (matcher: Bash): git push を検知したら
# push 対象コミットのサマリを添えてブロックし、ユーザー確認を促す。
# Codex は permissionDecision の ask を未サポート (返すと fail-open) のため、
# ブロックは exit 2 + stderr で表明する (allow/deny は解釈するが ask は扱えない)。
# git push 以外のコマンドは即 exit 0(判定は bash-review.py 等に委ねる)。
#
# 意図的に `set -e` は使わない: このフックは概ね fail-open 設計であり、サマリ
# 生成等に失敗しても本体のコマンド実行を止めてはならない。各コマンドの失敗は
# `|| exit 0` / `2>/dev/null` で個別に握りつぶし、最悪でも exit 0 で抜ける。
#
# 唯一の例外が jq の不在 (下記)。これは「サマリを作れない」ではなく「push か
# どうかを判定できない」であり、fail-open にするとゲート自体が無言で消える。

input=$(cat)

# jq が無いとコマンド文字列を取り出せず、下の push 判定は空文字を検査して
# 必ず「該当なし」になる。そのまま exit 0 すると push 手前の唯一のゲートが
# 無言で消えるため、ここだけは fail-open にしない。生の stdin を粗く検査し、
# push らしき記述があれば (サマリは作れないので) ブロックだけを行う。
# 判定材料が JSON エスケープ済みの生文字列なので、クォート除去による誤検知
# 抑制は効かない = コミットメッセージ中の "git push" でもブロックされる。jq が
# 無い環境限定の縮退動作としては、取りこぼすより過検知の方が望ましい。
if ! command -v jq >/dev/null 2>&1; then
  if printf '%s' "$input" | grep -qE 'git.*push'; then
    cat >&2 <<'EOF'
git push detected, but jq is unavailable so this hook could not parse the
command or build the usual commit summary. Review manually before pushing
(see ~/.codex/AGENTS.md "## 3. Git Workflow").
EOF
    exit 2
  fi
  exit 0
fi

cmd=$(echo "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)

# 非 push コマンドをここで捨てる。hooks.json.template はこのフックを matcher
# "Bash" で無条件に配線しているため、全 Bash 呼び出しで下の strip_quoted_ranges
# (1 文字ずつの状態機械 = O(n^2)) が走っていた。20KB のコマンドで実測 4.5 秒。
# .claude 側は 982c9be でこのガードを入れたが、こちらへは伝播していなかった
# (同コミットの本文いわく「.codex 側は元から無条件配線」= より重い露出)。
#
# 打ち切って安全な理由: 判定対象 cmd_for_match は $cmd から「文字を削除」して
# 作られる (strip_quoted_ranges はクォート区間と行継続ごと、インタプリタ経路の
# コピーはクォート文字だけを削除する) ため、そこにリテラル `push` が現れるには
# $cmd 中に p,u,s,h がこの順で並んでいなければならない。$cmd からクォート・
# バックスラッシュ・改行を落とした文字列はそれらの削除の過剰近似なので、そこに
# `push` が無ければ下の判定は必ず「該当なし」になる。
#
# 必ず jq 復号後の $cmd を見ること。生 stdin に対して同じ tr を掛けると、JSON が
# 改行を符号化した `\n` の backslash だけが消えて復号後には存在しない `n` が残り、
# `git pu\<改行>sh` (シェルが行継続で git push に結合する) が `git punsh` に化けて
# 取りこぼす。8f2d386 が塞いだ行継続バイパスの再発。
#
# grep は -i で引く。case-insensitive な FS では `GIT Push` も git を走らせるので、
# 下の検知が -i である以上ここだけ大小を区別すると、打ち切りガードの方が厳しく
# なって検知に到達できない。上の過剰近似の議論は大小を問わず成り立つ。
if ! printf '%s' "$cmd" | tr -d '\\"'"'"'\n' | grep -qi push; then
  exit 0
fi

# strip_quoted_ranges: クォート区間を 1 文字ずつの状態機械で除去する設計根拠は
# .claude/hooks/git-push-review.sh の同名関数のコメントを参照(ロジックは同一)。
strip_quoted_ranges() {
  local str="$1" out="" c state=0 i=0 len depth=0 sub=""
  len=${#str}
  while [ "$i" -lt "$len" ]; do
    c="${str:i:1}"
    case "$state" in
    0)
      # クォート外: バックスラッシュは次の1文字を素通しでエスケープする
      # (`\"` / `\'` はクォートを開始しない)。
      case "$c" in
      "\\")
        # 行継続 (バックスラッシュ+改行) はシェルが両方とも取り除き、
        # 前後の行を1つの論理行に結合する (`git \` + 改行 + `push` は
        # 実行時 `git push` になる)。それ以外の `\X` は X をエスケープされた
        # リテラルとして残す (`\"` / `\$` 等) ので、次の1文字だけを素通しする。
        if [ "${str:i+1:1}" != $'\n' ]; then
          out+="${str:i+1:1}"
        fi
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
      # ダブルクォート内: 基本は閉じクォートまで破棄するが、$(...) と
      # バッククォートのコマンド置換は bash がダブルクォート内でも実際に
      # 実行するため、丸ごと破棄すると `echo "log: $(git push)"` の push が
      # 検知を素通りする。置換部分だけ state 3/4 で out に残す。
      # バックスラッシュは次の1文字ごと消費するので、\$( / \` のように
      # エスケープされた(実行されない)置換は残らない。
      case "$c" in
      "\\")
        i=$((i + 2))
        ;;
      "\"")
        state=0
        i=$((i + 1))
        ;;
      '$')
        if [ "${str:i+1:1}" = "(" ]; then
          sub=""
          depth=1
          state=3
          i=$((i + 2))
        else
          i=$((i + 1))
        fi
        ;;
      '`')
        sub=""
        state=4
        i=$((i + 1))
        ;;
      *)
        i=$((i + 1))
        ;;
      esac
      ;;
    3)
      # ダブルクォート内の $(...): 対応する閉じ括弧まで生のまま sub に集め
      # (括弧の深さのみ追跡)、閉じたところで再帰的にクォート除去して out に
      # 残す。置換の中身は bash が独立したコマンドとして再パースするため、
      # 中のクォート区間 (`git -C "/a b" push` の "/a b" 等) も外側と同じ
      # 規則で除去しないと push 検知の正規表現がトークンを追えない。
      case "$c" in
      "\\")
        sub+="${str:i:2}"
        i=$((i + 2))
        ;;
      "(")
        depth=$((depth + 1))
        sub+="$c"
        i=$((i + 1))
        ;;
      ")")
        depth=$((depth - 1))
        i=$((i + 1))
        if [ "$depth" -eq 0 ]; then
          out+="\$("
          out+="$(strip_quoted_ranges "$sub")"
          out+=")"
          state=2
        else
          sub+="$c"
        fi
        ;;
      *)
        sub+="$c"
        i=$((i + 1))
        ;;
      esac
      ;;
    4)
      # ダブルクォート内の `...`: 同じく実行されるので、閉じバッククォート
      # まで sub に集めて再帰的にクォート除去し、out に残す。
      case "$c" in
      "\\")
        sub+="${str:i:2}"
        i=$((i + 2))
        ;;
      '`')
        out+='`'
        out+="$(strip_quoted_ranges "$sub")"
        out+='`'
        state=2
        i=$((i + 1))
        ;;
      *)
        sub+="$c"
        i=$((i + 1))
        ;;
      esac
      ;;
    esac
  done
  printf '%s' "$out"
}

# `${IFS}` / `$IFS` は bash が空白へ展開してから語分割するため、`git${IFS}push`
# はシェルにとって本当に `git push` である。以降の検知はどれも「生バイトとしての
# 空白」を要求するので、ここで一度だけ空白へ畳んでから全ての検査に回す。
# インタプリタ検知より前で畳むのが要点で、後ろでやると `sh${IFS}-c "git push"`
# がインタプリタとして認識されず、クォート区間ごと落ちて push が消える。
#
# 展開一般 (`${x}` や `$(...)` で語を組み立てる形) の解決は範囲外 — 実行前に
# 確定するにはシェルの評価が要る。そちらは _bash_review_common.py の
# _UNRESOLVABLE_EXPANSION が「実行体を確定できない」として high-risk (二重モデル
# AND ゲート) へ倒す方で受ける。ここで畳むのは IFS だけ = 空白そのものを隠す
# 定型手口で、かつ副作用なく正規化できるため。
cmd_norm="${cmd//\$\{IFS\}/ }"
cmd_norm="${cmd_norm//\$IFS/ }"

# クォートもバックスラッシュも無いなら strip_quoted_ranges は恒等変換なので、
# 1 文字ずつの走査を丸ごと省く。上の打ち切りを通過した大きな push コマンド
# (`git push origin main # <長いコメント>` 等) はこちらで受ける。
case "$cmd_norm" in
*[\'\"\\]*)
  # 状態機械は O(n^2)。上の 2 つのガード (push 無し / クォート無し) は、長い
  # ヒアドキュメントやコミットメッセージに push をチェーンした形 (実際に多い)
  # をどちらも通してしまい、40KB で 17 秒掛かっていた。上限を超えたらクォート
  # 文字だけを落とした過剰近似へ倒す: 検知が緩む方向ではなく厳しくなる方向
  # (余分に ask へ倒れる) なので、ゲートとしては安全側。
  if [ "${#cmd_norm}" -le 4000 ]; then
    cmd_for_match=$(strip_quoted_ranges "$cmd_norm")
  else
    cmd_for_match=$(printf '%s' "$cmd_norm" | tr -d "\"'")
  fi
  ;;
*) cmd_for_match="$cmd_norm" ;;
esac

# `eval` / `sh -c` / `bash -c` は文字列引数を「データ」ではなく「コード」として
# 実行する。つまり strip_quoted_ranges が「単なるメッセージ」として捨てたクォート
# 区間こそが実行される本体であり、`eval "git push origin main"` は本当に push する。
# 行継続すり抜け (8f2d386) と同じく「不活性なはずのテキストがシェル機構で実行される」
# クラスの穴。
#
# 引数を入れ子のコマンドラインとして再パースするのは大掛かりなので、そうした
# インタプリタがコマンド中に現れる場合に限り、クォート「文字」だけを除去して
# 中身を残した 2 本目のコピーを検査対象に足す。検査対象を増やすだけの一方向の
# 拡張なので、インタプリタが無い既存の否定ケース (`echo "git push"` /
# `git commit -m "... git push ..."`) は従来どおり静かなまま。push 確認は
# 余分に ask へ倒れる方が安全側なので、この粒度で十分とする。
# 境界に `/` を含めるのは、パス指定のインタプリタ (`/bin/bash -c` / `/bin/sh -c`)
# も同じコマンドだから。`/` が境界でないと、裸の `bash -c` は捕まるのにパス付き
# だけ素通りするという不整合な穴が残る。
# shellcheck disable=SC2016  # 正規表現中の $ とバッククォートはリテラル
executes_string_arg='(^|[;&|[:space:](`/])(eval|(bash|sh|zsh|dash|ksh)[[:space:]]+(-[^[:space:]]+[[:space:]]+)*-[A-Za-z]*c)([[:space:]]|$)'
# 照合を -i にするのは、既定の macOS (APFS) / Windows のファイルシステムが
# 大文字小文字を区別せず `BASH -c "git push"` が本当に bash を走らせるから。
# bash 自身の `-c` は大小を区別する (`-C` は別物) ので -i は過検知側に振れるが、
# 上記のとおり push 確認は余分にブロックへ倒れる方が安全側。
if printf '%s' "$cmd_norm" | grep -qiE "$executes_string_arg"; then
  cmd_for_match="${cmd_for_match}
$(printf '%s' "$cmd_norm" | tr -d "\"'")"
fi

# コマンド文字列のどこかに git ... push が含まれるか(チェーン・サブシェル・
# コマンド置換含む。バッククォートも $(...) と同様コマンド開始境界になる)。
# フラグは「値が = で連結される形式 (--git-dir=/x)」と「スペースで区切られる
# 形式 (git -C /repo push)」の両方を許容する (値はフラグと誤読しないよう
# 先頭が - 以外のトークンに限定)。push の直後は空白・行末だけでなく、
# `;` `&` `|` `)` と閉じバッククォートも文の終端になり得る
# (`git push;true` / `(git push)` / `$(git push)` を見逃さない)。リダイレクト
# 演算子も同じ (`git push>/dev/null` / `git push</dev/null`): `<` `>` が終端
# クラスに無かったため、`push` の直後にリダイレクトを密着させた形だけが両変種を
# 素通りしていた。
#
# 境界に `/` を含める理由は上の executes_string_arg と同じ: パス指定の
# `/usr/bin/git push` も同じコマンドで、`/` が境界でないと裸の `git push` は
# 捕まるのにパス付きだけ素通りするという不整合な穴が残る。
#
# git とサブコマンドの間で `$` / バッククォートを含む語 (評価できない展開) を
# オプション (とその値) になりうる語として読み飛ばす理由は .claude 側の同じ
# 箇所を参照 (`D=--git-d; git ${D}ir=<B>/.git push` の検知漏れ = ゲート喪失)。
# shellcheck disable=SC2016  # 正規表現中の $ とバッククォートはリテラル(展開させない)
git_opt_unit='[[:space:]]+(-[^[:space:]]+|[^[:space:]]*[$`][^[:space:]]*)([[:space:]]+[^-[:space:]][^[:space:]]*)?'
# shellcheck disable=SC2016  # 同上
git_exp_unit='[[:space:]]+[^[:space:]]*[$`][^[:space:]]*([[:space:]]+[^-[:space:]][^[:space:]]*)?'
# shellcheck disable=SC2016  # 同上
push_head='(^|[;&|[:space:](`/])git'
# shellcheck disable=SC2016  # 同上
push_tail='[[:space:]]+push([[:space:];&|)<>`]|$)'
push_exp_re="${push_head}(${git_opt_unit})*${git_exp_unit}(${git_opt_unit})*${push_tail}"
# クォート文字だけを落としたコピーに push_exp_re を追加で照合する理由
# (`git "${OPT}" <dir> push` の検知漏れ、`echo "git $x push"` の過検知を許容)
# は .claude 側の同じ箇所を参照。
cmd_quotes_stripped=$(printf '%s' "$cmd_norm" | tr -d "\"'")
if ! echo "$cmd_for_match" | grep -qiE "${push_head}(${git_opt_unit})*${push_tail}"; then
  printf '%s\n' "$cmd_quotes_stripped" | grep -qiE "$push_exp_re" || exit 0
fi

# git_c_dir_from_words: `-C <dir>` を語単位で解決する設計根拠は
# .claude/hooks/git-push-review.sh の同名関数のコメントを参照(ロジックは同一)。
# バックスラッシュは POSIX 規則 ("..." 内で `\` がエスケープするのは $ ` " \ と
# 改行だけ) で扱い、生の制御文字を含む入力では呼ばない前提も同じ。
git_c_dir_from_words() {
  local s="$1"
  local n=${#s} i=0 ch nx word="" in_word=0 in_s=0 in_d=0
  local -a toks=()
  local sep=$'\x01'
  while [ "$i" -lt "$n" ]; do
    ch=${s:i:1}
    if [ "$in_s" -eq 1 ]; then
      if [ "$ch" = "'" ]; then in_s=0; else word+=$ch; fi
      i=$((i + 1))
      continue
    fi
    if [ "$ch" = "\\" ]; then
      nx=${s:i+1:1}
      if [ "$i" -ge $((n - 1)) ]; then
        word+=$ch
        in_word=1
        i=$((i + 1))
        continue
      fi
      if [ "$nx" = $'\n' ]; then
        i=$((i + 2))
        continue
      fi
      if [ "$in_d" -eq 1 ]; then
        case "$nx" in
        '$' | '`' | '"' | "\\")
          word+=$nx
          i=$((i + 2))
          ;;
        *)
          word+=$ch
          i=$((i + 1))
          ;;
        esac
        continue
      fi
      word+=$nx
      in_word=1
      i=$((i + 2))
      continue
    fi
    if [ "$in_d" -eq 1 ]; then
      if [ "$ch" = '"' ]; then
        in_d=0
      elif [ "$ch" = '$' ] && [ "${s:i+1:1}" = '(' ]; then
        if [ "$in_word" -eq 1 ]; then
          toks+=("$word")
          word=""
          in_word=0
        fi
        toks+=("$sep")
        in_d=0
        i=$((i + 2))
        continue
      else
        word+=$ch
      fi
      i=$((i + 1))
      continue
    fi
    case "$ch" in
    "'")
      in_s=1
      in_word=1
      ;;
    '"')
      in_d=1
      in_word=1
      ;;
    ' ' | $'\t')
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      ;;
    ';' | '|' | '&' | '(' | ')' | '`' | $'\n')
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      toks+=("$sep")
      ;;
    *)
      word+=$ch
      in_word=1
      ;;
    esac
    i=$((i + 1))
  done
  if [ "$in_word" -eq 1 ]; then toks+=("$word"); fi
  toks+=("$sep")

  local tok first_hit="" push_hit="" c_val=""
  local seg_start=1 seg_git=0 seg_push=0 flags_ok=1 expect_val=0
  for tok in "${toks[@]}"; do
    if [ "$tok" = "$sep" ]; then
      if [ "$seg_git" -eq 1 ] && [ -n "$c_val" ]; then
        [ -z "$first_hit" ] && first_hit=$c_val
        if [ "$seg_push" -eq 1 ] && [ -z "$push_hit" ]; then push_hit=$c_val; fi
      fi
      seg_start=1
      seg_git=0
      seg_push=0
      flags_ok=1
      expect_val=0
      c_val=""
      continue
    fi
    if [ "$seg_start" -eq 1 ]; then
      seg_start=0
      case "${tok##*/}" in [Gg][Ii][Tt]) seg_git=1 ;; esac
      continue
    fi
    [ "$seg_git" -eq 1 ] || continue
    if [ "$expect_val" -eq 1 ]; then
      c_val=$tok
      expect_val=0
      flags_ok=0
      continue
    fi
    [ "$tok" = push ] && seg_push=1
    if [ "$flags_ok" -eq 1 ]; then
      case "$tok" in
      -C) expect_val=1 ;;
      -*) ;;
      *) flags_ok=0 ;;
      esac
    fi
  done
  printf '%s' "${push_hit:-$first_hit}"
}

# `cd <dir>` (シェルビルトイン) が push の手前にある場合、それ以降の全コマンド
# の実際の cwd を書き換える。`-C` の値解決とは別枠で扱う: path-qualified な
# `/bin/cd` は別プロセスの cwd しか変えず親シェルには効かないので対象外とし
# (同じ理由で `pushd`/`popd` も対象外 = 常に「解決不能」扱いにする)、bare な
# 語 `cd` だけを見る。
#
# 確信が持てる場合だけ辿り、それ以外はフックの cwd の要約へフォールバックせず
# 要約そのものを諦める(-C の「誤った要約は空の要約より悪い」と同じ理由)。
# 判定材料が同じコマンド文字列である以上、strip_quoted_ranges/-C と同じ 4000
# 文字上限を呼び出し側で共有する(この関数単体には上限を持たせない)。
#
# 出力形式・解決/解決不能のルール (行き先の制御文字、チルダの展開/リテラル/
# 混在の区別、複数の被演算子・`-` で始まる語・`..` 成分・CDPATH 下の相対の
# 行き先を解決不能とすること、
# 呼び出し側が生の制御文字を弾いておく前提を含む) は
# .claude/hooks/git-push-review.sh の同名関数のコメントを参照(ロジックは同一)。
cd_target_from_words() {
  local s="$1" cdpath_live="${2:-0}"
  local n=${#s} i=0 ch nx word="" in_word=0 in_s=0 in_d=0
  local -a toks=()
  local sep=$'\x02' mark=$'\x03' tmix=$'\x0e'
  # `;`/改行、`&`/`&&`、`|`/`||`/`|&` はどれも同じ「区切り」ではない: 直後の
  # コマンドが確実に実行されるか (`;`)、失敗しても実行されるか (`&`, バック
  # グラウンド化するだけで cd の効果は現在のシェルに残らない)、条件付きか
  # (`&&`/`||`) で cd が push の時点で「必ず効いている」と言えるかが変わる。
  # git_c_dir_from_words とは異なりここでは区別する必要があるため、境界ごとに
  # 種別マーカーを1つ追加で積む。
  local op_seq=$'\x04' op_and=$'\x05' op_or=$'\x06' op_bg=$'\x07' op_pipe=$'\x08'
  local saw_subshell=0 saw_special=0

  while [ "$i" -lt "$n" ]; do
    ch=${s:i:1}
    if [ "$in_s" -eq 1 ]; then
      if [ "$ch" = "'" ]; then
        in_s=0
      elif [ -z "$word" ] && [ "$ch" = "~" ]; then
        word="./~"
      else
        word+=$ch
      fi
      i=$((i + 1))
      continue
    fi
    if [ "$ch" = "\\" ]; then
      nx=${s:i+1:1}
      if [ "$i" -ge $((n - 1)) ]; then
        word+=$ch
        in_word=1
        i=$((i + 1))
        continue
      fi
      if [ "$nx" = $'\n' ]; then
        i=$((i + 2))
        continue
      fi
      if [ "$in_d" -eq 1 ]; then
        case "$nx" in
        '$' | '`' | '"' | "\\")
          word+=$nx
          i=$((i + 2))
          ;;
        *)
          word+=$ch
          i=$((i + 1))
          ;;
        esac
        continue
      fi
      case "$word" in '~'*/*) ;; '~'*) word=$tmix$word ;; esac
      if [ -z "$word" ] && [ "$nx" = "~" ]; then word="./~"; else word+=$nx; fi
      in_word=1
      i=$((i + 2))
      continue
    fi
    if [ "$in_d" -eq 1 ]; then
      if [ "$ch" = '"' ]; then
        in_d=0
      elif [ "$ch" = '$' ] && [ "${s:i+1:1}" = '(' ]; then
        if [ "$in_word" -eq 1 ]; then
          toks+=("$word")
          word=""
          in_word=0
        fi
        toks+=("$sep")
        toks+=("$mark")
        saw_subshell=1
        in_d=0
        i=$((i + 2))
        continue
      elif [ -z "$word" ] && [ "$ch" = "~" ]; then
        word="./~"
      else
        word+=$ch
      fi
      i=$((i + 1))
      continue
    fi
    case "$ch" in
    "'")
      case "$word" in '~'*/*) ;; '~'*) word=$tmix$word ;; esac
      in_s=1
      in_word=1
      ;;
    '"')
      case "$word" in '~'*/*) ;; '~'*) word=$tmix$word ;; esac
      in_d=1
      in_word=1
      ;;
    ' ' | $'\t')
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      ;;
    ';')
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      toks+=("$sep")
      toks+=("$op_seq")
      ;;
    $'\n')
      # bash の文法上 `&&` / `||` / `|` の直後の改行はただの linebreak で
      # あり、リストを区切らない (`a &&\nb` は `a && b` と同じ)。直前に
      # 積んだトークンがこれらの演算子マーカーなら、この改行自体を
      # 読み飛ばして区切りを作らない (空セグメントを挟まない)。
      if [ "$in_word" -eq 0 ] && [ "${#toks[@]}" -gt 0 ]; then
        local last_tok=${toks[${#toks[@]} - 1]}
        if [ "$last_tok" = "$op_and" ] || [ "$last_tok" = "$op_or" ] || [ "$last_tok" = "$op_pipe" ]; then
          i=$((i + 1))
          continue
        fi
      fi
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      toks+=("$sep")
      toks+=("$op_seq")
      ;;
    '&')
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      toks+=("$sep")
      # `&&` は前段が成功した場合だけ次を実行する条件付き。単独の `&` は
      # 前段をバックグラウンド化するだけで、cd がそこにあっても効果は
      # フォークされた子プロセス側にしか残らず、現在のシェル (push が実行
      # される側) には反映されない。
      if [ "${s:i+1:1}" = '&' ]; then
        toks+=("$op_and")
        i=$((i + 1))
      else
        toks+=("$op_bg")
      fi
      ;;
    '|')
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      toks+=("$sep")
      # `||` は前段が失敗した場合だけ次を実行する条件付き。単独の `|`
      # (`|&` も同様) はパイプライン: bash はパイプの両側をサブシェルで
      # 実行するので (lastpipe 未設定時)、cd がどちら側にあっても現在の
      # シェルには効かない。zsh は最後の要素をカレントシェルで実行するが、
      # それでも「未知」として安全側 (解決不能) に倒す。
      if [ "${s:i+1:1}" = '|' ]; then
        toks+=("$op_or")
        i=$((i + 1))
      else
        toks+=("$op_pipe")
        [ "${s:i+1:1}" = '&' ] && i=$((i + 1))
      fi
      ;;
    '(' | ')' | '`')
      if [ "$in_word" -eq 1 ]; then
        toks+=("$word")
        word=""
        in_word=0
      fi
      toks+=("$sep")
      toks+=("$mark")
      saw_subshell=1
      ;;
    '<')
      # ヒアドキュメント (`<<` / `<<-`)。本文は実行されないので語自体は通常
      # どおり扱い、フラグだけ立てる(下の cd 判定と組み合わせて解決不能に倒す)。
      [ "${s:i+1:1}" = '<' ] && saw_special=1
      word+=$ch
      in_word=1
      ;;
    '#')
      # 語頭 (直前が空白・区切り) のみコメント開始として扱う。語の途中の `#`
      # (`http://x#frag` 等) はコメントではない。
      [ "$in_word" -eq 0 ] && saw_special=1
      word+=$ch
      in_word=1
      ;;
    *)
      # 空クォートの直後の `~` (`''~/x`): bash はリテラル、zsh は展開 = 混在。
      if [ -z "$word" ] && [ "$ch" = "~" ] && [ "$in_word" -eq 1 ]; then
        word=$tmix$ch
      else
        word+=$ch
      fi
      in_word=1
      ;;
    esac
    i=$((i + 1))
  done
  if [ "$in_word" -eq 1 ]; then toks+=("$word"); fi
  toks+=("$sep")

  local tok
  local seg_start=1 seg_git=0 seg_push=0 seg_cd=0
  local flags_ok=1 expect_val=0 c_val=""
  local cd_arg_taken=0 cd_arg="" cd_bare=1 cd_odd=0
  local seg_index=0 push_seg_index=-1 push_seg_c_val=""
  local -a cd_idx=() cd_dir=() cd_bad=() hidden_idx=() boundary_op=()
  local pushdpopd_seen=0
  # boundary_op[N] = セグメント N と N+1 を繋ぐ演算子 (SEQ/AND/OR/BG/PIPE/
  # OTHER)。last_boundary は「直前に閉じたセグメントの番号」= 次に見る演算子
  # マーカーが説明する境界の番号。サブシェル境界 (mark) は saw_subshell の
  # 既存の一律解決不能ルールに任せるので OTHER を入れておくだけで十分。
  local last_boundary=-1

  for tok in "${toks[@]}"; do
    if [ "$tok" = "$mark" ]; then
      [ "$last_boundary" -ge 0 ] && boundary_op[last_boundary]="OTHER"
      continue
    fi
    if [ "$tok" = "$op_seq" ]; then
      boundary_op[last_boundary]="SEQ"
      continue
    fi
    if [ "$tok" = "$op_and" ]; then
      boundary_op[last_boundary]="AND"
      continue
    fi
    if [ "$tok" = "$op_or" ]; then
      boundary_op[last_boundary]="OR"
      continue
    fi
    if [ "$tok" = "$op_bg" ]; then
      boundary_op[last_boundary]="BG"
      continue
    fi
    if [ "$tok" = "$op_pipe" ]; then
      boundary_op[last_boundary]="PIPE"
      continue
    fi
    if [ "$tok" = "$sep" ]; then
      if [ "$seg_cd" -eq 1 ]; then
        local resolved="" bad=$cd_odd
        case "$cd_arg" in *[[:cntrl:]]*) bad=1 ;; esac
        if [ "$bad" -eq 1 ]; then
          :
        elif [ "$cd_bare" -eq 1 ]; then
          if [ -n "$HOME" ]; then resolved="$HOME"; else bad=1; fi
        elif [ "$cd_arg" = "-" ]; then
          bad=1
        elif [ "$cd_arg" = "~" ]; then
          if [ -n "$HOME" ]; then resolved="$HOME"; else bad=1; fi
        elif [ "${cd_arg#\~/}" != "$cd_arg" ]; then
          if [ -n "$HOME" ]; then resolved="${HOME}/${cd_arg#\~/}"; else bad=1; fi
        elif [ "${cd_arg#\~}" != "$cd_arg" ]; then
          bad=1
        elif [ -z "$cd_arg" ]; then
          bad=1
        else
          case "$cd_arg" in
          *'$'* | *'`'*) bad=1 ;;
          *) resolved="$cd_arg" ;;
          esac
        fi
        # `..` を成分に含む行き先 (`link/..`, `../x`, `a/../b`, `~/..`) も解決
        # 不能。シェルの既定の cd は論理的に `..` を畳む (link/.. は link を
        # 含むディレクトリ) が、git -C は物理的に解決する (リンク先の親)。
        # フックの cwd 自体がリンク経由で到達したパスである可能性もある。
        case "/$resolved/" in */../*) bad=1 ;; esac
        # CDPATH が効きうるなら (呼び出し側が第 2 引数 cdpath_live=1 で渡す:
        # フック自身の環境に CDPATH がある、またはコマンド中に CDPATH/cdpath が
        # 現れる)、`/`・`./`・`../` で始まらない相対の行き先 (`cd sub`) はシェルが
        # まず CDPATH から探すので、git -C sub (cwd 基準) とは別の場所になりうる
        # -> 解決不能。ユーザーのシェルの rc ファイルの中でだけ設定された
        # CDPATH (zsh の cdpath を含む) はここからは分からない (未対応)。
        # `./~…` はクォートされたチルダ (実際の被演算子は `~…`) なので対象に
        # 含める。
        if [ "$bad" -eq 0 ] && [ "$cdpath_live" = "1" ]; then
          case "$resolved" in
          /*) ;;
          './~'*) bad=1 ;;
          . | ./*) ;;
          *) bad=1 ;;
          esac
        fi
        cd_idx+=("$seg_index")
        cd_dir+=("$resolved")
        cd_bad+=("$bad")
      fi
      if [ "$seg_git" -eq 1 ] && [ "$seg_push" -eq 1 ] && [ "$push_seg_index" -eq -1 ]; then
        push_seg_index=$seg_index
        push_seg_c_val=$c_val
      fi
      last_boundary=$seg_index
      seg_index=$((seg_index + 1))
      seg_start=1
      seg_git=0
      seg_push=0
      seg_cd=0
      flags_ok=1
      expect_val=0
      c_val=""
      cd_arg_taken=0
      cd_arg=""
      cd_bare=1
      cd_odd=0
      continue
    fi
    if [ "$seg_start" -eq 1 ]; then
      seg_start=0
      case "$tok" in
      cd) seg_cd=1 ;;
      pushd | popd) pushdpopd_seen=1 ;;
      esac
      case "${tok##*/}" in [Gg][Ii][Tt]) seg_git=1 ;; esac
      continue
    fi
    # `command cd T` / `eval cd T` / `FOO=1 cd T` / `{ cd T; }` のように
    # cd/pushd/popd がセグメントの先頭語ではない位置に隠れている場合。上の
    # seg_start 判定は先頭語しか見ていないため、これらは cd として一切
    # 認識されず push を隠さないまま素通りしていた (フックの cwd の要約が
    # 出てしまう)。このトークナイザは自前で解決しようとせず、push より手前に
    # 現れた位置だけ記録して後で一律「解決不能」に倒す。
    case "$tok" in
    cd | pushd | popd) hidden_idx+=("$seg_index") ;;
    esac
    # cd の語は最初の 1 つだけを被演算子として受け取る。2 つ目以降の語
    # (`cd <dir> extra`: bash 3.2 は先頭を使い、bash 5 は "too many arguments"
    # で失敗し、zsh は $PWD 内の置換と解釈する) と、`-` で始まる最初の語
    # (`-x` 等の不正オプションは cd を失敗させ、`-L`/`-P`/`-e`/`-@`/`--` は
    # 被演算子の解決方法を変える) は、どちらも解決不能 (cd_odd) に倒す。
    # リダイレクト (`2>/dev/null`) も語として数えるので note に倒れるが、安全側。
    if [ "$seg_cd" -eq 1 ]; then
      if [ "$cd_arg_taken" -eq 1 ]; then
        cd_odd=1
      else
        cd_arg=$tok
        cd_arg_taken=1
        cd_bare=0
        case "$tok" in -*) cd_odd=1 ;; esac
      fi
    fi
    if [ "$seg_git" -eq 1 ]; then
      # 判定順は git_c_dir_from_words と揃える: `-C` の値トークンを push 判定
      # より先に消費する。逆順だと `git -C push status` のように -C の値が
      # たまたま文字列 "push" のときそのセグメントを push だと誤認する
      # (test_a_directory_named_push_in_cwd_cannot_hijack_the_summary と同種の
      # ハイジャック)。
      if [ "$expect_val" -eq 1 ]; then
        c_val=$tok
        expect_val=0
        flags_ok=0
        continue
      fi
      [ "$tok" = push ] && seg_push=1
      if [ "$flags_ok" -eq 1 ]; then
        case "$tok" in
        -C) expect_val=1 ;;
        -*) ;;
        *) flags_ok=0 ;;
        esac
      fi
    fi
  done

  # macOS 標準の bash 3.2 は IFS=$'\x01' を read で分割できない (別の制御文字
  # なら分割できる、この環境固有のクセ)。区切りは \x1c (File Separator) を使う。
  local out_d=$'\x1c'
  local push_found=0
  [ "$push_seg_index" -ge 0 ] && push_found=1

  if [ "$push_found" -eq 0 ]; then
    printf '0%s%s%s' "$out_d" "" "$out_d"
    return
  fi

  # 「push より手前のセグメントにある」だけでは cd が確実に効いたとは言え
  # ない。push が実行された時点でその cd が必ず実行済みと言えるのは:
  #   ルールA: cd から push まで `&&` だけで繋がっている (push 自身の
  #            `&&` チェーン) かつ、cd の直前が `||` ではない (`||` の
  #            右側は左側が成功すると一切実行されない — その場合でも
  #            後続の `&&` チェーンは左側の成功ステータスを引き継いで
  #            進んでしまうため、cd 抜きで push まで到達しうる)。
  #   ルールB: cd がその `;`/改行 区切りグループの先頭コマンド (直前が
  #            `;`/改行、またはコマンド全体の先頭) で、かつ直後が
  #            バックグラウンド化 (`&`) でもパイプ (`|`/`|&`) でもない
  #            (どちらも cd の効果を現在のシェルから切り離す)。
  # どちらも満たさない cd (`cd X & git push` / `false && cd X; git push` /
  # `true || cd X && git push` / `cd X | cat; git push` 等) は「効いたか
  # 分からない」ので、位置だけで relevant 扱いにはしない — 決定不能
  # (uncertain_cd_seen) として一律解決不能に倒す。
  local relevant_count=0 relevant_dir="" relevant_bad=0 idx si
  local uncertain_cd_seen=0
  for idx in "${!cd_idx[@]}"; do
    si=${cd_idx[$idx]}
    if [ "$si" -lt "$push_seg_index" ]; then
      local certain=0 path_and=1 k before_op="" before_ok=0
      k=$si
      while [ "$k" -lt "$push_seg_index" ]; do
        if [ "${boundary_op[$k]}" != "AND" ]; then
          path_and=0
          break
        fi
        k=$((k + 1))
      done
      [ "$si" -gt 0 ] && before_op=${boundary_op[$((si - 1))]}
      # cd の直前が `||` だと左側成功時に cd は実行されず、その「成功」
      # ステータスだけが後続の `&&` チェーンへ引き継がれて push まで到達
      # しうる。直前が `|` (パイプ) も同様の理由で不可: `true | cd X` の
      # cd はパイプの右側としてサブシェルで実行され、cd の成否に関わらず
      # 「パイプ全体の (cd の) 終了ステータス」だけが左側の実行有無と無関係に
      # 外側のシェルへ返る (= cd 自体が外側シェルの cwd を変えたかどうかは
      # 終了ステータスからは分からない)。どちらも "&&" チェーンの起点として
      # 信用できない。
      if [ "$path_and" -eq 1 ] && [ "$before_op" != "OR" ] && [ "$before_op" != "PIPE" ]; then
        certain=1
      fi
      # ルールB: cd が「その and-or リストの先頭」であることに加え、`&`
      # は `&&`/`||` より結合順位が低い (`cd X && true & push` は
      # `(cd X && true) & push` になる) ため、cd を含む and-or リスト全体を
      # バックグラウンド化しうる。cd 直後の演算子 1 つだけを見ても分からない
      # ので、AND/OR を辿ってそのリストの本当の終端 (最初の非 AND/OR 境界、
      # または push に到達) まで前進する。終端が `;`/改行 (SEQ) なら無条件に
      # 次へ進む=確定。終端が `&` (BG) やパイプ/サブシェル境界なら、リスト
      # ごと現在のシェルから切り離されている可能性があるので不確定。
      if [ "$certain" -eq 0 ]; then
        if [ "$si" -eq 0 ] || [ "$before_op" = "SEQ" ] || [ "$before_op" = "BG" ]; then
          before_ok=1
        fi
        if [ "$before_ok" -eq 1 ]; then
          local j=$si terminator="" jop
          while [ "$j" -lt "$push_seg_index" ]; do
            jop=${boundary_op[$j]}
            if [ "$jop" = "AND" ] || [ "$jop" = "OR" ]; then
              j=$((j + 1))
              continue
            fi
            terminator=$jop
            break
          done
          if [ -z "$terminator" ] || [ "$terminator" = "SEQ" ]; then
            certain=1
          fi
        fi
      fi
      if [ "$certain" -eq 1 ]; then
        relevant_count=$((relevant_count + 1))
        relevant_dir=${cd_dir[$idx]}
        [ "${cd_bad[$idx]}" -eq 1 ] && relevant_bad=1
      else
        uncertain_cd_seen=1
      fi
    fi
  done

  local hidden_before=0 hi
  for hi in "${hidden_idx[@]}"; do
    [ "$hi" -lt "$push_seg_index" ] && hidden_before=1
  done

  local involved=0
  [ "$relevant_count" -ge 1 ] && involved=1
  [ "$pushdpopd_seen" -eq 1 ] && involved=1
  [ "$hidden_before" -eq 1 ] && involved=1
  [ "$uncertain_cd_seen" -eq 1 ] && involved=1

  if [ "$involved" -eq 0 ]; then
    printf '1%s%s%s' "$out_d" "" "$out_d"
    return
  fi

  if [ "$pushdpopd_seen" -eq 1 ] || [ "$relevant_count" -gt 1 ] ||
    [ "$relevant_bad" -eq 1 ] || [ "$saw_subshell" -eq 1 ] || [ "$saw_special" -eq 1 ] ||
    [ "$hidden_before" -eq 1 ] || [ "$uncertain_cd_seen" -eq 1 ]; then
    printf '1%s1%s' "$out_d" "$out_d"
    return
  fi

  local final_dir="$relevant_dir"
  if [ -n "$push_seg_c_val" ]; then
    case "$push_seg_c_val" in
    /*) final_dir="$push_seg_c_val" ;;
    *) final_dir="${relevant_dir}/${push_seg_c_val}" ;;
    esac
  fi
  # `~/` 展開後や -C 値との合成後の最終形も見る (cd 引数だけの判定では
  # `cd /a && git -C "sub<改行>x" push` の -C 側を取りこぼす)。
  case "$final_dir" in
  *[[:cntrl:]]*)
    printf '1%s1%s' "$out_d" "$out_d"
    return
    ;;
  esac
  printf '1%s0%s%s' "$out_d" "$out_d" "$final_dir"
}

# `git -C <dir> push` のように push 対象リポジトリが明示されている場合、
# サマリもフック自身の cwd ではなく同じ <dir> を対象に生成する。誤った要約は
# 空の要約より悪い (別リポジトリの中身に対する承認を誘発する) ので、
# 「表示バグ」ではなくゲートの欠陥として扱う。
#
# strip_quoted_ranges と同じ長さ上限を超える入力では -C を解決しない (cwd の
# 要約を出す)。理由は 2 つ: 語分割も 1 文字ずつの走査で入力長に比例して遅い
# こと、そして上限超えのフォールバックはクォート文字だけを落とすため、引用
# されたメッセージの中身が語として見えており、そこから -C を拾うとコミット
# メッセージで要約先を差し替えられること。
#
# `cd <dir>` はこの -C とは独立に cd_target_from_words で解決する。cd が
# 一切関与しない場合だけ、従来通り git_c_dir_from_words の -C 解決に
# フォールバックする。判定の詳細は呼び出し元 (.claude 側の同ブロック) の
# コメントを参照(ロジックは同一)。
#
# 生の制御文字 (タブ・改行以外。\r を含む) を含むコマンドでは行き先を解決しない
# (トークナイザが帯域内で使う区切りバイトと衝突するため)、決まった行き先が実在
# するディレクトリでなければ note にする、の 2 点も .claude 側と同一。
# `--git-dir` / `--work-tree` / `GIT_DIR` / `GIT_WORK_TREE` の出現 (-C も cd も
# 使わずに push 対象を差し替える) で note にするのも同一。
git_c_opt=()
unresolved_why=""
cd_uncertain_why="the command changes directory in a way this hook cannot resolve with certainty"
cd_push_found=0
cd_status=""
cd_dir_val=""
use_legacy_c_lookup=0
ctrl_count=$(printf '%s' "$input" | jq -r '[(.tool_input.command // "") | explode[] | select((. < 32 and . != 9 and . != 10) or . == 127)] | length' 2>/dev/null)
cmd_probe=$(printf '%s' "$cmd_norm" | tr -d '\\"'"'"'\n')
git_dir_named=0
case "$cmd_probe" in
*--git-dir* | *--work-tree* | *GIT_DIR* | *GIT_WORK_TREE*) git_dir_named=1 ;;
esac
# CDPATH (フック自身の環境、またはコマンド中の CDPATH/cdpath) と、push の
# グローバルオプション位置の評価できない展開の扱いは .claude 側と同一。
cdpath_live=0
[ -n "$CDPATH" ] && cdpath_live=1
case "$cmd_probe" in
*CDPATH* | *cdpath*) cdpath_live=1 ;;
esac
# 展開の照合はクォート文字だけを落としたコピー (上の検知で作成) にも掛ける
# (ダブルクォート内の展開を見落とさないため。理由は .claude 側参照)。
push_via_expansion=0
if echo "$cmd_for_match" | grep -qiE "$push_exp_re" ||
  printf '%s\n' "$cmd_quotes_stripped" | grep -qiE "$push_exp_re"; then
  push_via_expansion=1
fi
if [ "$ctrl_count" != "0" ]; then
  unresolved_why="the command contains raw control characters, which this hook does not parse"
elif [ "$git_dir_named" -eq 1 ]; then
  unresolved_why="the command sets --git-dir, --work-tree, GIT_DIR or GIT_WORK_TREE, which this hook does not follow"
elif [ "$push_via_expansion" -eq 1 ]; then
  unresolved_why="a word among git's options before push is an expansion this hook cannot evaluate"
elif [ "${#cmd_norm}" -le 4000 ]; then
  cd_result=$(cd_target_from_words "$cmd_norm" "$cdpath_live")
  IFS=$'\x1c' read -r cd_push_found cd_status cd_dir_val <<<"$cd_result"
fi

if [ -n "$unresolved_why" ]; then
  :
elif [ "$cd_push_found" != "1" ]; then
  # cmd_for_match ではなくクォート「文字」だけを落とした文字列を見る理由は
  # 呼び出し元 (.claude 側の同ブロック) のコメントを参照(ロジックは同一)。
  if printf '%s\n' "$cmd_quotes_stripped" | grep -qE '(^|[^[:alnum:]_])(cd|pushd|popd)([^[:alnum:]_]|$)'; then
    unresolved_why=$cd_uncertain_why
  else
    use_legacy_c_lookup=1
  fi
else
  case "$cd_status" in
  1) unresolved_why=$cd_uncertain_why ;;
  0) git_c_opt=(-C "$cd_dir_val") ;;
  *) use_legacy_c_lookup=1 ;;
  esac
fi

if [ "$use_legacy_c_lookup" -eq 1 ] && [ "${#cmd_norm}" -le 4000 ]; then
  # 番兵 x で $(...) による末尾改行の削除を防ぐ (理由は .claude 側参照)。
  git_c_dir=$(
    git_c_dir_from_words "$cmd_norm"
    printf x
  )
  git_c_dir=${git_c_dir%x}
  case "$git_c_dir" in
  *[[:cntrl:]]*) unresolved_why="the -C directory contains control characters" ;;
  ?*) git_c_opt=(-C "$git_c_dir") ;;
  esac
fi

if [ -z "$unresolved_why" ] && [ "${#git_c_opt[@]}" -gt 0 ] && [ ! -d "${git_c_opt[1]}" ]; then
  unresolved_why="the directory this hook read as the push target does not exist"
fi

summary=""
if [ -n "$unresolved_why" ]; then
  summary="(target repository could not be determined: ${unresolved_why} -- review the target manually before approving)"
elif git "${git_c_opt[@]}" rev-parse --is-inside-work-tree &>/dev/null; then
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
git push detected. Review before pushing (see ~/.codex/AGENTS.md "## 3. Git Workflow"):
${summary}
EOF
exit 2
