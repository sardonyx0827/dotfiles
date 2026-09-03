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
(see ~/.codex/AGENTS.md "Git ワークフロー").
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
# shellcheck disable=SC2016  # 正規表現中のバッククォートはリテラル(展開させない)
echo "$cmd_for_match" | grep -qiE '(^|[;&|[:space:](`/])git([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*[[:space:]]+push([[:space:];&|)<>`]|$)' || exit 0

# `git … -C <dir>` の <dir> を、クォート解釈済みの語単位で探す。
#
# 以前は正規表現で文字列を直接見ていた。クォート区間を除去した文字列に対しては
# `-C "/path with space"` の値が消えて次の語 `push` を掴み (要約が空の ask)、
# 逆にクォートを残した文字列に対してはコミットメッセージ内の
# `git -C "/fake" push` を本物の -C として拾い、別リポジトリ (空) の要約で承認を
# 誘発できた — 文字列照合では「引用されたテキスト」と「実行される語」を区別
# できない。シェルと同じ規則で語に割れば、メッセージは `-m` の値 1 語であって
# -C フラグにはならず、`-C "/a b"` の値はそのまま 1 語として得られる。
#
# セグメント (; | & ( ) ` 改行 で区切る) ごとに、先頭語の basename が git
# (大小無視: case-insensitive な FS では `GIT` も git を走らせる) で、ダッシュ語
# だけを挟んで `-C <値>` が続くものを候補にする。push 語を持つセグメントを優先し、
# 無ければ最初の候補。`-c key=val` (小文字) は別のフラグなので対象外。
# ダブルクォート内の `$(` は中身が実行されるのでセグメント境界として扱う
# (閉じ側の対応は取らない近似。要約先の選択にしか使わないので十分)。
# 1 文字ずつ走査するため入力長に比例して遅く、呼び出し側は strip_quoted_ranges
# と同じ長さ上限の内側でだけ使う。
git_c_dir_from_words() {
  local s="$1"
  local n=${#s} i=0 ch word="" in_word=0 in_s=0 in_d=0
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
      if [ "$i" -lt $((n - 1)) ]; then
        word+=${s:i+1:1}
        in_word=1
      fi
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
git_c_opt=()
if [ "${#cmd_norm}" -le 4000 ]; then
  git_c_dir=$(git_c_dir_from_words "$cmd_norm")
  [ -n "$git_c_dir" ] && git_c_opt=(-C "$git_c_dir")
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
