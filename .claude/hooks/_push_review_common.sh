#!/bin/bash
# _push_review_common.sh
# git-push-review.sh (.claude: JSON の ask / .codex: stderr + exit 2) が共有する、
# push の検知と確認サマリの組み立て。2 つのアダプタは入力の取り出しと出力の形だけを
# 持ち、判定の本体はここに一本化してある。
#
# このファイルが唯一の実体。.codex/hooks/ 側には複製もリンクも無く、あちらの
# git-push-review.sh が cd -P で ../../.claude/hooks を解決して直接読む。編集は
# ここだけ。経緯は _hook_common.sh / _bash_review_common.py のヘッダを参照。
#
# ■ source 側との契約 (_hook_common.sh と同じ)
#
#   - このファイルは関数定義だけを持つ。source 時に出力せず、set/shopt/IFS/trap/cwd
#     も変数も触らない (tests/test_hook_sync.py が実際に source して確かめる)。
#   - 関数内では exit せず return する。終了コードと出力の形はアダプタの責務。
#   - hook_push_review_run は最後の関数でなければならない。アダプタは「それが定義
#     されている」を「ファイルを最後まで読めた」の証拠として扱う: 切れたファイルや
#     構文エラーのあるファイルは、シェルがそこで読むのを止めるので、以降の関数が
#     未定義になるだけで済む。末尾に何かを足すとこの推論が崩れる。
#
# ■ 読み込めなかったときの扱い (アダプタ側。ここは何も知らない)
#
#   ファイルが無い / 空 / 途中で切れている / 構文エラー / 必要な関数のどれかが未定義
#   / hook_push_review_run の戻り値が 0 でも 1 でもない、のいずれでも push を黙って
#   通してはならない。他の共有ライブラリ (lint / format) は読めなければ fail-open
#   するが、このフックは push 手前の唯一のゲートなので逆に倒す: 固定文の確認要求
#   (Claude: JSON の ask / Codex: stderr + exit 2) に縮退する。ただし読み込みは
#   「push|send-pack を含まないコマンドを捨てる」安価なガードの後ろにあるので、この
#   経路に入るのは push らしきコマンドだけ。普通の Bash 呼び出しはこのファイルを
#   パースすらしない。
#
# ■ 命名について
#
#   他の共有ライブラリは公開関数に hook_ を付けるが、ここは「非公開ライブラリ」の
#   例外: 公開 API はエントリの hook_push_review_run 1 つで、ヘルパーは元のフックの
#   名前のまま移してある。改名すると「関数をそのまま移しただけ」という差分での検証
#   (元ファイルとの diff) が効かなくなるため。変数も元のまま無プレフィックスで、
#   gp_* / push_report / push_foreign / push_shown / hd_* は関数間で値を渡す意図的な
#   グローバル (command substitution を挟むと値の末尾改行が落ちる)。

# 空白を含まないクォート区間 <span> の中身を「語」として残してよいか (0=残す)。
# 残すのは、区間がセグメント (最後の `; & | ( \`` / 改行より後ろ) の先頭語の一部で
# あるとき (環境変数代入・予約語・time/env/nice 等の前置語は飛ばす。`then "git"
# push`)、または区間より前のセグメント内の語に basename が git のものがあるとき
# (`git "push"` / `git -C "/x" "push"` / `timeout 5 git "push"` / `{ git "push"; }`)。
# git の位置を「先頭語」に限らないのは、ラッパー (timeout 5 / command -p / xargs) の
# 引数の形を列挙しきれないため。照合側 (push_head) も git の前を空白だけで許すので、
# 残して増える過検知は `echo git "push"` のように素の `echo git push` と同じ形だけ。
# `echo "git" "push"` は git が区間なので語として数えず、従来どおり捨てる。
# 第 1 引数は区間の直前までの出力 (strip_quoted_ranges の out)。
#
# 区切りは末尾側から探す (`${o%[...]*}` は末尾から 1 文字ずつ試すので最後の区切り
# までの距離で済む)。`${o##*[...]}` は全前置に対して先頭 `*` の照合をやり直すため
# $o の長さの 2 乗になり、区間ごとに呼ぶと全体で 3 乗に近づく (短いクォート語を
# 並べた 3.6 KB で 50 秒。フックの時間切れは push を素通りさせる)。先頭語の走査は
# セグメントが 256 文字以下のときに限り、それより長ければ区間を残す側 (0) に倒す:
# 文字を残しても検知が増えるだけで、取りこぼしは生まない。
strip_span_counts() {
  local o="$1" sp="$2" seg w pre head=1 nl=$'\n'
  case "$sp" in *[[:space:]]*) return 1 ;; esac
  pre="${o%[;&|(\`"${nl}"]*}"
  if [ "${#pre}" -lt "${#o}" ]; then seg="${o:${#pre}+1}"; else seg="$o"; fi
  [ "${#seg}" -le 256 ] || return 0
  while :; do
    seg="${seg#"${seg%%[![:space:]]*}"}"
    case "$seg" in
    *[[:space:]]*) ;;
    *) return $((1 - head)) ;;
    esac
    w="${seg%%[[:space:]]*}"
    case "${w##*/}" in [Gg][Ii][Tt]) return 0 ;; esac
    case "$w" in
    [A-Za-z_]*=* | time | command | builtin | exec | nohup | noglob | env | nice | \
      if | then | do | else | elif | while | until | '!' | '{' | *')') ;;
    *) head=0 ;;
    esac
    seg="${seg#"$w"}"
  done
}

# ヒアドキュメント (`<<WORD` / `<<-WORD` / `<<'WORD'` / `<<"WORD"` / `<<\WORD`) の
# 読み取り補助。strip_quoted_ranges と git_c_dir_from_words の両方が使う。command
# substitution を使わず、結果はグローバルで返す (サブシェルを挟まない)。
#
# hd_read_delim <文字列> <位置>: 位置から区切り語を読む。引用符とバックスラッシュを
#   外した語を hd_word に、読み終えた次の位置を hd_next に入れる。区切り語は空白と
#   `; & | ( ) < >` で終わる (引用符の中は空白も含められる)。
# hd_read_body <文字列> <位置> <区切り語> <dash>: 位置 (本文の最初の行頭) から区切り行
#   までを本文として読む。本文 (各行末に改行付き、区切り行は含まない) を hd_body に、
#   区切り行の次の位置を hd_next に入れる。dash=1 (`<<-`) なら区切り行の先頭タブを
#   無視する。区切り行が無ければヒアドキュメントとして読まない: hd_found=0 を返し、
#   hd_next は位置のまま (本文を消費しない)。呼び出し側はその `<<` を無視して後続行を
#   コードとして走査し続ける。bash は残り全部を本文にするが、`$((1<<2))` /
#   `let x=1<<2` の算術シフトを開始と誤認すると後続行 (行継続で割った push を含む)
#   を丸ごと本文に飲み込んでしまう。コードとして読む側に倒せば、検知は増えても
#   減らない。
hd_read_delim() {
  local str="$1" j="$2" len d
  len=${#str}
  hd_word=""
  while [ "$j" -lt "$len" ]; do
    d="${str:j:1}"
    case "$d" in
    ' ' | $'\t' | $'\n' | ';' | '&' | '|' | '(' | ')' | '<' | '>') break ;;
    "'")
      j=$((j + 1))
      while [ "$j" -lt "$len" ] && [ "${str:j:1}" != "'" ]; do
        hd_word+="${str:j:1}"
        j=$((j + 1))
      done
      j=$((j + 1))
      ;;
    "\"")
      j=$((j + 1))
      while [ "$j" -lt "$len" ] && [ "${str:j:1}" != "\"" ]; do
        hd_word+="${str:j:1}"
        j=$((j + 1))
      done
      j=$((j + 1))
      ;;
    "\\")
      hd_word+="${str:j+1:1}"
      j=$((j + 2))
      ;;
    *)
      hd_word+="$d"
      j=$((j + 1))
      ;;
    esac
  done
  hd_next=$j
}

hd_read_body() {
  local str="$1" i="$2" delim="$3" dash="$4" len rest line cmp nl=$'\n'
  len=${#str}
  hd_body=""
  hd_found=0
  hd_next=$i
  # 区切り行の有無を先に 1 回のパターン照合で確かめる (dash=1 は先頭タブを見ない分だけ
  # 緩い = 有りと誤る側。最終判定は下の行ごとの照合)。無いと分かれば行ごとの走査を
  # しない: 算術の `<<` が並ぶと、`<<` ごとに残り全体を行単位で読み直すことになる。
  rest="$nl${str:i}$nl"
  if [ "$dash" -eq 1 ]; then
    case "$rest" in *"$delim$nl"*) ;; *) return 0 ;; esac
  else
    case "$rest" in *"$nl$delim$nl"*) ;; *) return 0 ;; esac
  fi
  while [ "$i" -lt "$len" ]; do
    rest="${str:i}"
    line="${rest%%$'\n'*}"
    i=$((i + ${#line} + 1))
    cmp="$line"
    if [ "$dash" -eq 1 ]; then
      while [ "${cmp:0:1}" = $'\t' ]; do
        cmp="${cmp:1}"
      done
    fi
    if [ "$cmp" = "$delim" ]; then
      hd_found=1
      hd_next=$i
      return 0
    fi
    hd_body+="$line"$'\n'
  done
  hd_body=""
}

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
#
# 追うのはクォートだけではない。コメント (語頭の `#` から行末) とヒアドキュメント
# 本文に偶発的なアポストロフィがあると、それが偽のクォートを開いて後続行の本物の
# `git push` ごと飲み込む (`# what's changed` + 改行 + `git push`)。そこで:
#   - コメントは読み飛ばす。`$#` / `${#x}` / `a#b` / `"x"#y` / `\#` は語頭の `#`
#     ではないのでコメントにしない。
#   - ヒアドキュメント (`<<WORD` / `<<-WORD` / `<<'WORD'` / `<<"WORD"`) は区切り語を
#     保留し、次の改行から区切り行までの本文を、クォート文字だけ落として残す。本文を
#     捨てないのは、`bash <<'EOF'` / `sh -s <<EOF` の本文が実行されるコードで、引用
#     なしの `<<EOF` の中の `$(...)` も実行されるため。捨てると、これまで検知できて
#     いた形が素通りになる。(`git commit -F - <<EOF` の本文に `git push` と書くと
#     過検知になるが、これは従来どおり。) `<<<` (here-string) はヒアドキュメントでは
#     ない。区切り行が無ければ残り全部が本文 (bash と同じ)。
#   - `$'...'` は専用の状態で追う (`\'` で閉じない)。`$` はクォートの一部なので
#     出力に残さない (`git $'push'` が `git $push` になって照合できなくなるため)。
# クォート区間は、空白を含まないなら 1 語 (`git "push"` / `"git" push`) なので中身を
# 残し (ただし git がコマンドの位置にあるときだけ: strip_span_counts。`echo "git"
# "push"` は git が echo の引数なので捨てる)、空白を含むなら単なる文字列
# (コミットメッセージ等) として捨てる。閉じない
# クォートや置換で入力が終わったら、状態機械の読みを信用せず、クォート文字を落とした
# コピーを足して検知側へ倒す。
strip_quoted_ranges() {
  local str="$1" out="" c c2 state=0 i=0 len depth=0 sub="" span="" ws=1 bd=0
  local -a hd_delim=() hd_dash=()
  local j dash rest pre k sn
  len=${#str}
  while [ "$i" -lt "$len" ]; do
    c="${str:i:1}"
    case "$state" in
    0)
      # クォート外。ws=1 は「いま語頭」(直前が空白・区切り・行頭) の印で、`#` が
      # コメントになるのはこのときだけ。
      case "$c" in
      "\\")
        # 行継続 (バックスラッシュ+改行) はシェルが両方とも取り除き、
        # 前後の行を1つの論理行に結合する (`git \` + 改行 + `push` は
        # 実行時 `git push` になる)。それ以外の `\X` は X をエスケープされた
        # リテラルとして残す (`\"` / `\$` 等) ので、次の1文字だけを素通しする。
        # ただしエスケープされた空白 (`\ ` / `\<タブ>`) はバックスラッシュごと残す:
        # 落とすと語の区切りに化け、`git -C /tmp/a\ b push` の値が `/tmp/a` で切れて
        # `b` をサブコマンドと読み、push を見失う。残しても $cmd の部分列のまま
        # (上の打ち切りの前提を崩さない)。値の語は git_opt_word が `\.` ごと読む。
        # `\\` (エスケープされたバックスラッシュ) も 2 文字のまま残す: 1 文字に畳むと
        # 直後の空白と合わせて `\ ` に見え、`git -C /tmp/x\\ push` の値が push まで
        # 伸びてしまう。
        case "${str:i+1:1}" in
        $'\n') ;;
        ' ' | $'\t' | "\\")
          out+="${str:i:2}"
          ws=0
          ;;
        *)
          out+="${str:i+1:1}"
          ws=0
          ;;
        esac
        i=$((i + 2))
        ;;
      "'")
        state=1
        span=""
        i=$((i + 1))
        ;;
      "\"")
        state=2
        span=""
        i=$((i + 1))
        ;;
      '$')
        # `$'...'` (ANSI-C クォート) と `$"..."` (ロケール翻訳) の `$` はクォートの
        # 一部で、語としては残らない。落とさないと `git $'push'` が `git $push`
        # になり、どちらの正規表現にも掛からない。
        case "${str:i+1:1}" in
        "'")
          state=5
          span=""
          i=$((i + 2))
          ;;
        "\"")
          state=2
          span=""
          i=$((i + 2))
          ;;
        '{')
          # `${...}` の中の `#` は(空白の後でも)パターンの一部でコメントではない
          # (`${x/ #/y}`)。閉じる `}` まで数える。
          out+="\${"
          bd=$((bd + 1))
          ws=0
          i=$((i + 2))
          ;;
        *)
          out+="$c"
          ws=0
          i=$((i + 1))
          ;;
        esac
        ;;
      '#')
        if [ "$ws" -eq 1 ] && [ "$bd" -eq 0 ]; then
          # コメント: 改行の手前まで読み飛ばす (改行自体は残す)。コメント中の
          # アポストロフィが偽のクォートを開いて、後続行の本物の push ごと
          # 飲み込むのを防ぐ。`$#` / `${#x}` / `a#b` / `"x"#y` は語頭ではない
          # (ws=0) のでコメントにならない。
          rest="${str:i}"
          case "$rest" in
          *$'\n'*)
            pre="${rest%%$'\n'*}"
            i=$((i + ${#pre}))
            ;;
          *) i=$len ;;
          esac
        else
          out+="$c"
          i=$((i + 1))
        fi
        ;;
      '<')
        if [ "${str:i:3}" = '<<<' ]; then
          # here-string (ヒアドキュメントではない)
          out+='<<<'
          i=$((i + 3))
        elif [ "${str:i:2}" = '<<' ]; then
          # ヒアドキュメントの開始。区切り語 (WORD / 'WORD' / "WORD" / \WORD、`<<-`
          # なら先頭タブ除去) を読んで保留し、本文は次の改行で処理する。同じ行の
          # 残りは通常のコードとして走査を続ける (`cat <<EOF && git push`)。
          j=$((i + 2))
          dash=0
          if [ "${str:j:1}" = '-' ]; then
            dash=1
            j=$((j + 1))
          fi
          while [ "${str:j:1}" = ' ' ] || [ "${str:j:1}" = $'\t' ]; do
            j=$((j + 1))
          done
          hd_read_delim "$str" "$j"
          out+='<<'
          if [ -n "$hd_word" ]; then
            hd_delim+=("$hd_word")
            hd_dash+=("$dash")
          fi
          i=$hd_next
        else
          out+="$c"
          i=$((i + 1))
        fi
        ws=1
        ;;
      $'\n')
        out+="$c"
        ws=1
        i=$((i + 1))
        if [ "${#hd_delim[@]}" -gt 0 ]; then
          # 保留中のヒアドキュメント本文 (区切り行まで)。本文は捨てずに、クォート
          # 文字だけを落として残す: `bash <<'EOF'` / `sh -s <<EOF` の本文は
          # 実行されるコードで、`<<EOF` (引用なし) の `$(...)` も実行される。捨てると
          # これらが検知をすり抜ける。クォート文字を落とせば本文中のアポストロフィが
          # 後続行の push を飲み込むことはない。行継続 (`\` + 改行) も畳む: 本文を
          # 実行するシェルは `git \` + 改行 + `push` を git push として走らせる。
          # 区切り行が見つからない `<<` はヒアドキュメントではないとみなし
          # (hd_read_body 参照)、後続行をそのままコードとして走査する。
          k=0
          while [ "$k" -lt "${#hd_delim[@]}" ] && [ "$i" -lt "$len" ]; do
            hd_read_body "$str" "$i" "${hd_delim[k]}" "${hd_dash[k]}"
            [ "$hd_found" -eq 1 ] || break
            hd_body="${hd_body//\'/}"
            hd_body="${hd_body//\"/}"
            hd_body="${hd_body//\\$'\n'/}"
            out+="$hd_body"
            i=$hd_next
            k=$((k + 1))
          done
          hd_delim=()
          hd_dash=()
        fi
        ;;
      ' ' | $'\t' | ';' | '&' | '|' | '(' | '>')
        out+="$c"
        ws=1
        i=$((i + 1))
        ;;
      '}')
        if [ "$bd" -gt 0 ]; then bd=$((bd - 1)); fi
        out+="$c"
        ws=0
        i=$((i + 1))
        ;;
      ')')
        # `$(...)` / `$((...))` の `)` の直後の `#` は同じ語の続きでコメントではない
        # (`echo $(true)#x; git push` は `;` の後ろを実行する: bash 3.2 / zsh で確認)。
        # 素の `(true)# c` のコメントを取り違える側は、状態機械がクォート不整合で
        # 検知側に倒れるだけで安全。
        out+="$c"
        ws=0
        i=$((i + 1))
        ;;
      *)
        out+="$c"
        ws=0
        i=$((i + 1))
        ;;
      esac
      ;;
    1)
      # シングルクォート内: バックスラッシュも含め閉じクォートまで全て
      # (POSIX仕様でバックスラッシュに特別な意味はない)。閉じたとき、区間に空白が
      # 無ければ 1 語なので中身を残す (`git 'push'`)。空白があれば単なる文字列
      # (コミットメッセージ等) として捨てる。
      if [ "$c" = "'" ]; then
        state=0
        ws=0
        if strip_span_counts "$out" "$span"; then out+="$span"; fi
      else
        span+="$c"
      fi
      i=$((i + 1))
      ;;
    2)
      # ダブルクォート内: 基本は閉じクォートまで span に溜める (空白なしなら閉じ
      # たときに残す、上と同じ) が、$(...) と バッククォートのコマンド置換は bash
      # がダブルクォート内でも実際に実行するため、丸ごと破棄すると
      # `echo "log: $(git push)"` の push が検知を素通りする。置換部分だけ state
      # 3/4 で out に残す。バックスラッシュは $ ` " \ と改行だけをエスケープする
      # ので、エスケープされた(実行されない)置換は残らない。
      case "$c" in
      "\\")
        c2="${str:i+1:1}"
        case "$c2" in
        '$' | '`' | '"' | "\\") span+="$c2" ;;
        $'\n') ;;
        *) span+="$c$c2" ;;
        esac
        i=$((i + 2))
        ;;
      "\"")
        state=0
        ws=0
        if strip_span_counts "$out" "$span"; then out+="$span"; fi
        i=$((i + 1))
        ;;
      '$')
        if [ "${str:i+1:1}" = "(" ]; then
          sub=""
          depth=1
          state=3
          i=$((i + 2))
        else
          span+="$c"
          i=$((i + 1))
        fi
        ;;
      '`')
        sub=""
        state=4
        i=$((i + 1))
        ;;
      *)
        span+="$c"
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
    5)
      # `$'...'` (ANSI-C クォート): `\'` と `\\` がエスケープされる。バックスラッ
      # シュを落として次の1文字を残すだけで、`\x73` / `\n` / 8 進のデコードはしない
      # (出力は入力の文字の部分列に保つ = 先頭の push 打ち切りガードの前提)。閉じ
      # たときの扱いは通常のシングルクォートと同じ (空白なしなら 1 語として残す)。
      case "$c" in
      "\\")
        c2="${str:i+1:1}"
        case "$c2" in
        "'" | "\\") span+="$c2" ;;
        *) span+="$c$c2" ;;
        esac
        i=$((i + 2))
        ;;
      "'")
        state=0
        ws=0
        if strip_span_counts "$out" "$span"; then out+="$span"; fi
        i=$((i + 1))
        ;;
      *)
        span+="$c"
        i=$((i + 1))
        ;;
      esac
      ;;
    esac
  done
  # 閉じていないクォート/置換で終わった: 状態機械の読みを信用できない (bash 自身も
  # 構文エラーにする形だが、`it's` のような偶発的なアポストロフィを区間の開始と取り
  # 違えた可能性もある)。後続の本物の push を飲み込んだままにせず、クォート文字を
  # 落としたコピーを足して検知側へ倒す。
  if [ "$state" -ne 0 ]; then
    sn="${str//\$\'/}"
    sn="${sn//\$\"/}"
    out+=$'\n'"$(printf '%s' "$sn" | tr -d "\"'")"
  fi
  printf '%s' "$out"
}

# `git … -C <dir>` の <dir> を、クォート解釈済みの語単位で探す。
#
# 正規表現で文字列を直接見ると、クォート区間を除去した文字列では
# `-C "/path with space"` の値が消えて次の語 `push` を掴み (要約が空の ask)、
# 逆にクォートを残した文字列ではコミットメッセージ内の
# `git -C "/fake" push` を本物の -C として拾い、別リポジトリ (空) の要約で承認を
# 誘発できてしまう — 文字列照合では「引用されたテキスト」と「実行される語」を
# 区別できない。シェルと同じ規則で語に割れば、メッセージは `-m` の値 1 語であって
# -C フラグにはならず、`-C "/a b"` の値はそのまま 1 語として得られる。
#
# セグメント (; | & ( ) ` 改行 で区切る) ごとに、先頭語の basename が git
# (大小無視: case-insensitive な FS では `GIT` も git を走らせる) で、ダッシュ語
# だけを挟んで `-C <値>` が続くものを候補にする。サブコマンド (最初の非オプション
# 語) が push のセグメントがあれば、要約先はその push 自身の -C (無ければ空 = フックの
# cwd)。別の git 呼び出しの -C にフォールバックしてはいけない (`git -C <B> status &&
# git push` の要約が B になる)。push セグメントを識別できないとき (push が文字列の
# 中) は、push を含む文字列を持つ同じセグメントの -C だけを使い、別セグメントの -C
# しか無ければ要約先を決めない (note)。`stash push` / `commit -m push` の `push` はサブコマンドではない。値を取る
# グローバルオプション `-c k=v` の値は読み飛ばす。`-C` と小文字の `-c` は別のフラグ。
# 語頭の `#` はコメント (行末まで読み飛ばす)。
#
# 結果はグローバルで返す: gp_c_dir (-C の値)、gp_push_args (push セグメントごとの
# サブコマンド以降の語を \x1c 区切りで連結した文字列の配列)。command substitution
# を使わないので、値の末尾改行も失われない。
# ダブルクォート内の `$(` は中身が実行されるのでセグメント境界として扱う
# (閉じ側の対応は取らない近似。要約先の選択にしか使わないので十分)。
# 1 文字ずつ走査するため入力長に比例して遅く、呼び出し側は strip_quoted_ranges
# と同じ長さ上限の内側でだけ使う。
#
# バックスラッシュは POSIX (bash/zsh 共通) の規則どおりに扱う:
#   - クォート外の `\X` は X (`\` を落とす)。シングルクォート内は完全にリテラル。
#   - ダブルクォート内で `\` がエスケープするのは $ ` " \ と改行だけ。それ以外の
#     文字の前の `\` はそのまま残る (`"a\b"` は a\b であって ab ではない)。
#   - `\` + 改行は行継続: クォート外でも "..." 内でも両方とも消える。
#   - 入力末尾に孤立した `\` はリテラル。
# 状態に関係なく「`\X` は X」とすると、`-C "/x/a\b"` (git は a\b に入る) を ab と
# 読み、実在する別リポジトリ ab の要約を出せてしまう。
# 前提: 呼び出し側が生の制御文字 (タブ・改行以外) を含むコマンドを弾いてから
# 呼ぶ。区切りに使う \x01 が入力に現れると語の区切りと衝突するため。
git_c_dir_from_words() {
  local s="$1"
  local n=${#s} i=0 ch nx word="" in_word=0 in_s=0 in_d=0 rest pre j dash k last_paren=-1
  local -a toks=() hd_delim=() hd_dash=()
  local sep=$'\x01'
  local dq_resume=0 sub_depth=0
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
        # 置換の中は通常のコマンドとして読み、対応する `)` で外側のダブルクォートに
        # 戻る (戻らないと `-m "$(cat <<EOF ...)"` の閉じ側の `"` が新しい引用の
        # 開始になり、後続の `&& git push` を飲み込む)。入れ子は括弧の深さだけ数える。
        if [ "$sub_depth" -eq 0 ]; then dq_resume=1; fi
        sub_depth=$((sub_depth + 1))
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
    '<')
      # ヒアドキュメント (`<<WORD`): 区切り語を保留し、本文は次の改行で読み飛ばす
      # (本文は引数ではない。本文中のアポストロフィが偽のクォートを開かないように)。
      # here-string (`<<<`) は従来どおり通常の語として扱う。
      if [ "${s:i:2}" = '<<' ] && [ "${s:i:3}" != '<<<' ]; then
        j=$((i + 2))
        dash=0
        if [ "${s:j:1}" = '-' ]; then
          dash=1
          j=$((j + 1))
        fi
        while [ "${s:j:1}" = ' ' ] || [ "${s:j:1}" = $'\t' ]; do
          j=$((j + 1))
        done
        hd_read_delim "$s" "$j"
        if [ "$in_word" -eq 1 ]; then
          toks+=("$word")
          word=""
          in_word=0
        fi
        if [ -n "$hd_word" ]; then
          hd_delim+=("$hd_word")
          hd_dash+=("$dash")
        fi
        i=$hd_next
        continue
      fi
      word+=$ch
      in_word=1
      ;;
    '#')
      # 語頭の `#` はコメント: 改行の手前まで読み飛ばす (コメント中のアポストロフィが
      # 偽のクォートを開いて後続の push セグメントを飲み込まないように)。語の途中の
      # `#` (`a#b`) はただの文字。
      if [ "$in_word" -eq 0 ] && [ "$i" -ne "$last_paren" ]; then
        rest="${s:i}"
        case "$rest" in
        *$'\n'*)
          pre="${rest%%$'\n'*}"
          i=$((i + ${#pre}))
          ;;
        *) i=$n ;;
        esac
        continue
      fi
      word+=$ch
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
      if [ "$ch" = ")" ]; then last_paren=$((i + 1)); fi
      if [ "$ch" = $'\n' ] && [ "${#hd_delim[@]}" -gt 0 ]; then
        # 保留中のヒアドキュメント本文を区切り行まで読み飛ばす。区切り行の無い
        # `<<` (算術シフト等) は読み飛ばさない (hd_read_body 参照)。
        i=$((i + 1))
        k=0
        while [ "$k" -lt "${#hd_delim[@]}" ] && [ "$i" -lt "$n" ]; do
          hd_read_body "$s" "$i" "${hd_delim[k]}" "${hd_dash[k]}"
          [ "$hd_found" -eq 1 ] || break
          i=$hd_next
          k=$((k + 1))
        done
        hd_delim=()
        hd_dash=()
        continue
      fi
      if [ "$sub_depth" -gt 0 ]; then
        if [ "$ch" = "(" ]; then
          sub_depth=$((sub_depth + 1))
        elif [ "$ch" = ")" ]; then
          sub_depth=$((sub_depth - 1))
          if [ "$sub_depth" -eq 0 ] && [ "$dq_resume" -eq 1 ]; then
            dq_resume=0
            in_d=1
            in_word=1
          fi
        fi
      fi
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

  local tok first_hit="" push_hit="" push_found=0 c_val="" seg_args="" str_hit="" seg_pstr=0
  local seg_start=1 seg_git=0 seg_push=0 flags_ok=1 expect_val=0 skip_val=0 sub_seen=0
  local a_sep=$'\x1c'
  gp_push_args=()
  for tok in "${toks[@]}"; do
    if [ "$tok" = "$sep" ]; then
      if [ "$seg_git" -eq 1 ]; then
        if [ -n "$c_val" ] && [ -z "$first_hit" ]; then first_hit=$c_val; fi
        if [ -n "$c_val" ] && [ "$seg_pstr" -eq 1 ] && [ -z "$str_hit" ]; then str_hit=$c_val; fi
        if [ "$seg_push" -eq 1 ]; then
          gp_push_args+=("$seg_args")
          if [ "$push_found" -eq 0 ]; then
            push_found=1
            push_hit=$c_val
          elif [ "$c_val" != "$push_hit" ]; then
            # 2 つ目の push が別の -C を持つ: 要約は 1 つ目の宛先しか表せない
            gp_push_multi_dir=1
          fi
        fi
      fi
      seg_start=1
      seg_git=0
      seg_push=0
      flags_ok=1
      expect_val=0
      skip_val=0
      sub_seen=0
      c_val=""
      seg_args=""
      seg_pstr=0
      continue
    fi
    if [ "$seg_start" -eq 1 ]; then
      # 先頭の環境変数代入 (`FOO=1 git push`) と、シェルの前置語 (`time git push`)
      # は git の手前の飾りなので読み飛ばす。読み飛ばさないと push セグメントを
      # 見失い、別の git 呼び出しの -C にフォールバックしてしまう。
      case "$tok" in
      [A-Za-z_]*=* | time | command | builtin | exec | nohup | noglob) continue ;;
      esac
      seg_start=0
      case "${tok##*/}" in [Gg][Ii][Tt]) seg_git=1 ;; esac
      continue
    fi
    [ "$seg_git" -eq 1 ] || continue
    # `--exec "git push"` のように push を含む文字列 (空白入りの 1 語) を持つセグメント
    case "$tok" in *[[:space:]]*[Pp][Uu][Ss][Hh]*) seg_pstr=1 ;; esac
    if [ "$expect_val" -eq 1 ]; then
      c_val=$tok
      expect_val=0
      flags_ok=0
      continue
    fi
    if [ "$skip_val" -eq 1 ]; then
      skip_val=0
      continue
    fi
    # push セグメントのサブコマンド以降は全部その push の引数。
    if [ "$seg_push" -eq 1 ]; then
      seg_args+="${a_sep}${tok}"
      continue
    fi
    # サブコマンド位置 (最初の非オプション語) が push のときだけ push セグメント。
    # `git stash push` / `git commit -m push` の `push` はサブコマンドではない。
    # 値を取るグローバルオプション (-c k=v 等) の値は読み飛ばす。-C の値は下の
    # expect_val 経路で拾う。
    if [ "$sub_seen" -eq 0 ]; then
      case "$tok" in
      -c | --namespace | --config-env) skip_val=1 ;;
      -*) ;;
      *)
        sub_seen=1
        case "$tok" in [Pp][Uu][Ss][Hh]) seg_push=1 ;; esac
        ;;
      esac
    fi
    if [ "$flags_ok" -eq 1 ]; then
      case "$tok" in
      -C) expect_val=1 ;;
      -*) ;;
      *) flags_ok=0 ;;
      esac
    fi
  done
  # push セグメントが 1 つでも見つかったら、要約先はその push 自身の -C (無ければ
  # 空 = フックの cwd)。別の git 呼び出しの -C にフォールバックしない
  # (`git -C <B> status && git push` の要約が B になる誤り)。push セグメントを
  # 識別できないとき (push が文字列の中) は、push を含む文字列を持つ同じセグメントの
  # -C だけを使う (`git -C <B> rebase --exec 'git push'`)。-C が別のセグメントに
  # しか無ければ要約先を決められない (gp_c_foreign=1)。
  gp_c_foreign=0
  gp_push_found=$push_found
  if [ "$push_found" -eq 1 ]; then
    gp_c_dir=$push_hit
  elif [ -n "$str_hit" ]; then
    gp_c_dir=$str_hit
  else
    gp_c_dir=""
    if [ -n "$first_hit" ]; then gp_c_foreign=1; fi
  fi
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
# command substitution で 1 回だけ呼ぶ設計上、複数の戻り値をグローバル変数の
# サイドチャンネルで返すのはサブシェル越しに消えるため使えない。$'\x01' 区切り
# の1行に3フィールドを詰めて stdout で返す:
#   フィールド1 (push_found): "1" = git ... push セグメントを見つけられた
#                              "0" = 見つけられなかった
#   フィールド2 (status):     ""  = cd/pushd/popd は一切関与しない
#                              "0" = 確信を持って解決できた(フィールド3が値)
#                              "1" = 関与するが解決できない
#   フィールド3 (dir):        status が "0" のときの git -C 値
#
# 呼び出し側は push_found が "0" の場合、このトークナイザ自体が push を
# 見失っている(= このコマンドに対する解析結果を信用できない)とみなし、
# フィールド2/3 を無視して strip_quoted_ranges 由来の cmd_for_match を別途見る
# (詳細は呼び出し側のコメント参照)。
#
# 解決すると決めたケース:
#   - bare `cd` (引数なし): 実際のシェルの挙動どおり $HOME に移動する。
#     $HOME が空/未設定なら解決不能側に倒す。
#   - `~` / `~/rest`: $HOME 基準で展開する(シェルのチルダ展開と同じ規則)。
#   - それ以外の裸のパス(相対・絶対とも): トークンをそのまま git -C の値と
#     して使う。相対パスは git -C 自身がフックの実プロセス cwd を基準に
#     解決するので、ここで cwd 基準の絶対化を自前で行う必要はない。
#
# 解決不能として要約を諦めるケース(フックの cwd へは絶対にフォールバック
# しない):
#   - `cd -` (直前の OLDPWD。フックのプロセスには存在せず再現できない)
#   - `~user` (`~` の直後が `/` でも文字列末尾でもない = パスワードDB 参照が
#     要る。`~` 単体・`~/x` だけを解決対象とする)
#   - 引数に `$` / バッククォートを含む(変数展開・コマンド置換を評価できない)
#   - `pushd` / `popd`(ディレクトリスタック操作。cd 相当の単純な文字列解決が
#     できない)
#   - `(...)` や裸の `` `...` ``/`$(...)` を挟む(対象の git push が同じ
#     サブシェル内かどうかを字句解析だけでは判定できない。
#     `cd X && git push -u origin $(git branch --show-current)` のように
#     実際には push に影響しない形も一律「関与するなら解決不能」に倒す。
#     既知のトレードオフ: このような本来解決可能な形も要約なしになる)
#   - ヒアドキュメント (`<<`) やコメント (`#`) の本文行 (実行されないテキスト
#     だが見た目上コマンドと区別が付かない `cd /decoy` のような行が cd として
#     拾われうる)
#   - 同じコマンド中に (push より手前で) cd が複数回現れる
#   - 行き先 (cd の引数、および -C と合成した最終ディレクトリ) に改行・タブ
#     などの制御文字を含む。出力を IFS=\x1c の read で受けるため、改行があると
#     そこで切れて「手前までのパス」= 別の実在ディレクトリを要約してしまう
#     (`cd "<repo>\n/nonexistent"` が <repo> の要約になる)。見えない文字を含む
#     名前を要約の根拠にもしない。
#   - 先頭の `~` がクォートと混ざる形 (`~"/x"` / `~\/x` / `''~/x`)。bash は
#     リテラル、zsh は展開と、シェルによって行き先が割れる。
#   - 被演算子が 2 つ以上 (`cd <dir> extra`)、または最初の語が `-` で始まる
#     (`-x` / `-L` / `-P` / `-e` / `-@` / `--`)。シェルごとに失敗・解釈が割れる。
#   - 行き先に `..` 成分がある (`link/..`, `../x`)。シェルの cd は論理的、
#     git -C は物理的に `..` を解決するため、シンボリックリンクがあると割れる。
#   - フックの環境で CDPATH が空でないときの、`/`・`./`・`../` で始まらない
#     相対の行き先 (シェルは先に CDPATH を探す)。
#
# 語分割のバックスラッシュ規則は git_c_dir_from_words と同じ (POSIX)。チルダは
# 3 種に分ける (bash 3.2 / zsh で実測):
#   - 展開: クォートされていない `~` で語が始まり、最初のクォートされていない
#     `/` (無ければ語末) までにクォート/エスケープが無い (`~`, `~/x`, `~/"x"`)。
#   - リテラル: 先頭の `~` 自体がクォート/エスケープされている (`"~/x"`,
#     `'~'`, `\~/x`)。両シェルとも展開しないので、同じパスを指す `./~/x` として
#     語に積む (以降の `~` 規則に掛からない)。
#   - 混在: 上記以外で値が `~` で始まるもの。語の先頭に制御文字 \x0e を付けて
#     「解決不能」の印にする (下の制御文字判定で弾かれる)。
# 前提: 呼び出し側が生の制御文字 (タブ・改行以外) を含むコマンドを弾いてから
# 呼ぶ。この関数は \x02〜\x08 を区切り/種別マーカー、\x1c を出力区切り、\x0e を
# 混在チルダの印として帯域内で使うため、入力に同じバイトがあると衝突する
# (`echo x <0x02> cd <repo> && git push` の cd を独立したセグメントと誤認する)。
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
    # seg_start 判定は先頭語しか見ていないので、それだけではこれらは cd として
    # 認識されず素通りし、フックの cwd の要約が出てしまう。そこで
    # このトークナイザは自前で解決しようとせず、push より手前に
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

# push の引数 (git_c_dir_from_words が集めた gp_push_args) から、「この push が何を
# 送るか」の説明 (push_report) を組み立てる。要約の「HEAD と upstream の差分」は
# push の宛先 (remote / refspec / force 系オプション) を反映しないため、承認者が
# 見るべき情報の主役はこちら。
#   - remote と refspec を `push target:` 行に出す。
#   - force / 破壊的オプション (--force/-f/--force-with-lease/--force-if-includes/
#     `+` refspec/--delete/-d/`:dst` refspec/--mirror/--all/--tags/--prune) は
#     `WARNING` 行に並べる。
#   - refspec の source が現在ブランチでない、または --all/--mirror/--tags/--delete
#     等で送る ref が変わる場合は、下のコミット一覧が「実際に送られるもの」では
#     ないと note する (push_foreign=1 でコミット一覧の見出しも変える)。
#   - 引数を信頼できる形で読めない場合 (トークナイザが push を見失った / eval や
#     sh -c の中 / 長さ超過 / 制御文字 / 未知のオプション / 展開を含む refspec) は
#     推測せず note にする。
# push は複数あり得る (`git push a && git push --force b`) ので、1 つずつ報告する。
# 表示する語は制御文字を空白に置き換えて長さを制限し、URL の userinfo
# (`https://user:token@host/…`) は伏せる。
# 呼び出し側: 第 1 引数は現在ブランチ名 (要約を出さないときは空 = source の照合は
# しない)。結果は push_report / push_foreign (グローバル) で返す。
push_show() {
  local t="$1" scheme rest auth after
  t=${t//[[:cntrl:]]/ }
  case "$t" in
  *://*@*)
    scheme=${t%%://*}
    rest=${t#*://}
    auth=${rest%%/*}
    case "$auth" in
    *@*)
      after=${rest#"$auth"}
      t="${scheme}://***@${auth##*@}${after}"
      ;;
    esac
    ;;
  esac
  if [ "${#t}" -gt 120 ]; then t="${t:0:120}..."; fi
  push_shown=$t
}

build_push_report() {
  local branch="$1" a_sep=$'\x1c' nl=$'\n'
  local total=${#gp_push_args[@]} n=0 elem rest tok r src ch cl
  local remote have_remote warn unsure broad delete expansion expect after_dd
  local foreign_why tag refs_text remote_text
  local -a refs
  push_report=""
  push_foreign=0
  if [ "$gp_ran" -ne 1 ]; then
    push_report="note: push arguments were not parsed (the command is longer than 4000 characters or contains control characters) -- review the command itself${nl}"
    return
  fi
  if [ "$total" -eq 0 ]; then
    push_report="note: push arguments could not be parsed reliably (the push is not a plain \`git ... push\` word sequence -- e.g. it is inside eval, sh -c, watch, --exec or foreach -- or its quoting is not followable) -- review the command itself${nl}"
    return
  fi
  for elem in "${gp_push_args[@]}"; do
    n=$((n + 1))
    remote="" have_remote=0 warn="" unsure="" broad="" delete=0
    expansion=0 expect="" after_dd=0 foreign_why="" refs=()
    rest=$elem
    while [ -n "$rest" ]; do
      rest=${rest#"$a_sep"}
      tok=${rest%%"$a_sep"*}
      rest=${rest:${#tok}}
      push_show "$tok"
      tok=$push_shown
      if [ -n "$expect" ]; then
        # 直前のオプションの値、またはリダイレクトの宛先
        if [ "$expect" = repo ]; then
          remote=$tok
          have_remote=1
        fi
        expect=""
        continue
      fi
      case "$tok" in
      *"<"* | *">"*)
        # リダイレクト (`> log` / `2>` / `>/dev/null`)。演算子だけの語は次の語が宛先。
        case "$tok" in *"<" | *">") expect=redir ;; esac
        continue
        ;;
      esac
      if [ "$after_dd" -eq 0 ] && [ "$tok" != "-" ]; then
        case "$tok" in
        --)
          after_dd=1
          continue
          ;;
        --force)
          case ", $warn, " in *", --force, "*) ;; *) warn+="${warn:+, }--force" ;; esac
          continue
          ;;
        --force-with-lease | --force-with-lease=* | --force-if-includes)
          warn+="${warn:+, }$tok"
          continue
          ;;
        --delete)
          case ", $warn, " in *", --delete, "*) ;; *) warn+="${warn:+, }--delete" ;; esac
          delete=1
          continue
          ;;
        --mirror | --all | --branches | --tags | --prune)
          warn+="${warn:+, }$tok"
          broad+="${broad:+, }$tok"
          continue
          ;;
        --repo=*)
          remote=${tok#--repo=}
          have_remote=1
          continue
          ;;
        --repo)
          expect=repo
          continue
          ;;
        --push-option | --receive-pack | --exec | --recurse-submodules)
          expect="val"
          continue
          ;;
        --push-option=* | --receive-pack=* | --exec=* | --recurse-submodules=* | --signed=*)
          continue
          ;;
        --dry-run | --verbose | --quiet | --porcelain | --progress | --thin | --no-thin | --verify | --no-verify | --set-upstream | --follow-tags | --no-follow-tags | --atomic | --no-atomic | --signed | --no-signed | --ipv4 | --ipv6 | --no-recurse-submodules | --no-force-with-lease | --no-force-if-includes | --no-prune)
          continue
          ;;
        --*)
          unsure+=" $tok"
          continue
          ;;
        -?*)
          cl=${tok#-}
          while [ -n "$cl" ]; do
            ch=${cl:0:1}
            cl=${cl:1}
            case "$ch" in
            f) case ", $warn, " in *", --force, "*) ;; *) warn+="${warn:+, }--force" ;; esac ;;
            d)
              case ", $warn, " in *", --delete, "*) ;; *) warn+="${warn:+, }--delete" ;; esac
              delete=1
              ;;
            o)
              # `-o <value>` / `-o<value>`: 値を取る。残りは値なので読まない。
              [ -z "$cl" ] && expect="val"
              cl=""
              ;;
            n | v | q | u | 4 | 6) ;;
            *)
              unsure+=" $tok"
              cl=""
              ;;
            esac
          done
          continue
          ;;
        esac
      fi
      # 位置引数: 最初が remote、残りが refspec (--repo があれば全部 refspec)。
      if [ "$have_remote" -eq 0 ]; then
        remote=$tok
        have_remote=1
      else
        refs+=("$tok")
      fi
    done
    case "$remote" in
    *'$'* | *'`'*) expansion=1 ;;
    esac
    for r in ${refs[@]+"${refs[@]}"}; do
      case "$r" in
      *'$'* | *'`'*)
        expansion=1
        continue
        ;;
      esac
      src=${r#+}
      if [ "$src" != "$r" ]; then warn+="${warn:+, }+refspec $r"; fi
      case "$src" in
      :*)
        warn+="${warn:+, }delete refspec $r"
        continue
        ;;
      esac
      # --delete / -d では位置引数は「消す ref」であって source ではない。
      [ "$delete" -eq 1 ] && continue
      src=${src%%:*}
      [ -n "$branch" ] || continue
      case "$src" in
      HEAD | @ | "$branch" | "refs/heads/$branch") ;;
      *) [ -n "$foreign_why" ] || foreign_why="refspec source '$src' is not the current branch '$branch'" ;;
      esac
    done
    if [ -n "$branch" ] && [ -z "$foreign_why" ]; then
      if [ "$delete" -eq 1 ]; then
        foreign_why="--delete removes refs instead of pushing the current branch"
      elif [ -n "$broad" ]; then
        foreign_why="${broad} push refs other than just the current branch"
      fi
    fi
    tag=""
    [ "$total" -gt 1 ] && tag=" [${n}/${total}]"
    remote_text=${remote:-"(default)"}
    if [ "$have_remote" -eq 0 ]; then remote_text="(default)"; fi
    refs_text="(default)"
    if [ "${#refs[@]}" -gt 0 ]; then refs_text="${refs[*]}"; fi
    push_report+="push target${tag}: remote=${remote_text} refspec=${refs_text}${nl}"
    if [ -n "$warn" ]; then
      push_report+="WARNING${tag}: force/destructive push options: ${warn}${nl}"
    fi
    if [ "$expansion" -eq 1 ]; then
      push_report+="note${tag}: a refspec contains an expansion this hook cannot evaluate; the real push target is unknown${nl}"
    fi
    if [ -n "$unsure" ]; then
      push_report+="note${tag}: unrecognised push option(s)${unsure}; the remote/refspec above may be wrong${nl}"
    fi
    if [ -n "$foreign_why" ]; then
      push_foreign=1
      push_report+="note${tag}: ${foreign_why}; the commit list below describes the current branch only and is NOT necessarily what this push sends${nl}"
    fi
  done
}

# ■ push らしくないコマンドを先に捨てるガード (各アダプタが、このファイルを読む前に
#   実行する。同じ 1 行が両アダプタにあり、tests/test_hook_sync.py が一致を確かめる)
#   の根拠。ガードが安全であることは、ここにある strip_quoted_ranges の出力の性質に
#   依存するので、その関数と同じファイルに置く。
#
# 非 push コマンドをここで捨てる。settings.json に `if: Bash(git push*)` は
# 付けない (best-effort な前方一致フィルタは `git -C <dir> push` / `eval "git push"`
# を取りこぼし、このスクリプトの検知に到達できなくなる) ので、このフックは全
# Bash 呼び出しで走る。下の strip_quoted_ranges は 1 文字ずつの状態機械 = O(n^2)
# で、20KB のコマンドに 4.5 秒掛かる。
#
# 打ち切って安全な理由: 判定対象 cmd_for_match は $cmd から「文字を削除」して
# 作られる (strip_quoted_ranges はクォート区間と行継続ごと、インタプリタ経路の
# コピーはクォート文字の削除、またはクォート文字の空白への置換。どちらも文字は
# 増えない) ため、そこにリテラル `push` が現れるには
# $cmd 中に p,u,s,h がこの順で並んでいなければならない。$cmd からクォート・
# バックスラッシュ・改行を落とした文字列はそれらの削除の過剰近似なので、そこに
# `push` が無ければ下の判定は必ず「該当なし」になる。
#
# 必ず jq 復号後の $cmd を見ること。生 stdin に対して同じ tr を掛けると、JSON が
# 改行を符号化した `\n` の backslash だけが消えて復号後には存在しない `n` が残り、
# `git pu\<改行>sh` (シェルが行継続で git push に結合する) が `git punsh` に化けて
# 取りこぼす。行継続バイパスの再発になる。
#
# grep は -i で引く。case-insensitive な FS では `GIT Push` も git を走らせるので、
# 下の検知が -i である以上ここだけ大小を区別すると、打ち切りガードの方が厳しく
# なって検知に到達できない (`Git Push origin main` を取りこぼす)。
# 上の過剰近似の議論は大小を問わず成り立つ (p,u,s,h の順序だけが条件)。
#
# strip_quoted_ranges の出力は常に $cmd の文字の部分列でなければならない (文字の
# 削除と、`\'` / `\"` のバックスラッシュ除去だけ。`$'\x73'` 等のエスケープの
# デコードや文字の追加はしない)。この打ち切りの前提がそれで、追加を入れるとガードが
# 取りこぼしを作る。空白を含まないクォート区間は中身を残す (`git "pu""sh"` は
# push) ため、`git pus"XX"h` は `git pusXXh` になり、ガードと状態機械のどちらでも
# push ではない (実際のシェルの結果とも一致する)。
#
# `send-pack` も同じ議論で通す語に加える: `git send-pack` は `git push` が内部で
# 走らせる転送そのもので、`push` の 4 文字を含まずに push する (`http-push` は
# `push` を含むので元の条件で通る)。

# hook_push_review_run <生の stdin 全文> <jq 復号後のコマンド文字列>
#
# 戻り値:
#   0     push を検知した。確認に出す本文 (push の宛先の説明 + コミットのサマリ) を
#         グローバル push_review_body に入れた。
#   1     push ではない。何も出さずに通してよい。
#   その他 想定外。呼び出し側は読み込み失敗と同じ縮退 (固定文の確認要求) にする。
#
# 結果を push_review_body で返す理由: `$(...)` で受けると末尾の改行が落ちる。リポジトリ
# の外では要約が空で、本文は push_report 自身の末尾の改行で終わる。その改行が最後の
# 1 バイトなので、落とすと出力が変わる (tests/test_git_push_review.py のスナップショット
# が固定している)。
#
# 以下の本体は、元のフックのトップレベルにあった 2 つのブロック (push 検知と確認サマリ
# の組み立て) をそのまま関数に包んだもの。変えたのは 3 点だけ: 該当なしの `exit 0` を
# `return 1` にしたこと、引数を input / cmd に受けること (local)、結果を
# push_review_body に入れて `return 0` すること。それ以外の変数は元のとおりグローバル。
hook_push_review_run() {
  local input="$1" cmd="$2"

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

  # クォート文字もバックスラッシュも無ければ除去対象が無く、状態機械は入力を
  # そのまま返す。上のガードを通り抜けた長いコマンド (push を含む heredoc 等) で
  # O(n^2) を払わずに済むよう、結果が同一と分かるこの場合は素通しする。
  case "$cmd_norm" in
  *[\'\"\\]*)
    # 状態機械は O(n^2)。上の 2 つのガード (push 無し / クォート無し) は、長い
    # ヒアドキュメントやコミットメッセージに push をチェーンした形 (実際に多い)
    # をどちらも通してしまい、40KB で 17 秒掛かる。上限を超えたらクォート
    # 文字だけを落とした過剰近似へ倒す: 検知が緩む方向ではなく厳しくなる方向
    # (余分に ask へ倒れる) なので、ゲートとしては安全側。
    if [ "${#cmd_norm}" -le 4000 ]; then
      cmd_for_match=$(strip_quoted_ranges "$cmd_norm")
    else
      # クォート文字を落とすだけでは、状態機械なら解いていたバックスラッシュが残る:
      # `git pu\sh` (bash は push と読む) も、行継続で割った `git -C /x \<改行> push` も
      # 照合を外れ、長いコマンドの後ろに置くだけで確認なしに通っていた。行継続を
      # 削除し (bash と同じ: `\<改行>` は跡を残さず消える)、残りのバックスラッシュも
      # 落としたコピーを足す。足すだけなので、これも厳しくなる方向の近似。
      #
      # その畳み込みは `read` に任せる: -r を付けない read は `\<改行>` を行継続として
      # 繋ぎ、それ以外の `\` を落とす — 欲しい変換そのもので、入力長に線形。
      # `${cmd_norm//\\$'\n'/}` は bash 3.2 だと一致数に対して超線形で、行継続の多い
      # 長いコマンドでこのフックの待ち時間を倍にしていた (時間切れは push の素通り)。
      # shellcheck disable=SC2162  # read に `\` を解釈させるのが目的なので -r は付けない
      cmd_for_match="$(printf '%s' "$cmd_norm" | tr -d "\"'")
$(printf '%s\n' "$cmd_norm" | while IFS= read line || [ -n "$line" ]; do printf '%s\n' "$line"; done | tr -d "\"'")"
    fi
    ;;
  *) cmd_for_match="$cmd_norm" ;;
  esac

  # `eval` / `sh -c` / `bash -c` は文字列引数を「データ」ではなく「コード」として
  # 実行する。つまり strip_quoted_ranges が「単なるメッセージ」として捨てたクォート
  # 区間こそが実行される本体であり、`eval "git push origin main"` は本当に push する。
  # 行継続すり抜けと同じく「不活性なはずのテキストがシェル機構で実行される」
  # クラスの穴。
  #
  # 引数を入れ子のコマンドラインとして再パースするのは大掛かりなので、そうした
  # インタプリタがコマンド中に現れる場合に限り、クォート「文字」だけを除去して
  # 中身を残した 2 本目のコピーを検査対象に足す。検査対象を増やすだけの一方向の
  # 拡張なので、インタプリタが無い既存の否定ケース (`echo "git push"` /
  # `git commit -m "... git push ..."`) は静かなまま。push 確認は
  # 余分に ask へ倒れる方が安全側なので、この粒度で十分とする。
  # 境界に `/` を含めるのは、パス指定のインタプリタ (`/bin/bash -c` / `/bin/sh -c`)
  # も同じコマンドだから。`/` が境界でないと、裸の `bash -c` は捕まるのにパス付き
  # だけ素通りするという不整合な穴が残る。
  # 照合を -i にするのは、既定の macOS (APFS) / Windows のファイルシステムが
  # 大文字小文字を区別せず `BASH -c "git push"` が本当に bash を走らせるから。
  # bash 自身の `-c` は大小を区別する (`-C` は別物) ので -i は過検知側に振れるが、
  # 上記のとおり push 確認は余分に ask へ倒れる方が安全側。
  #
  # シェルと `-c` の間には任意の語を許し、`c` は束の途中にあってもよい。値を取る
  # オプション (`bash -euo pipefail -c` / `-O extglob` / `--rcfile X`)、`+e`、
  # `-cl` / `-ce` はどれも文字列を実行する。`-` 始まりの語だけを許して束の末尾の
  # `c` を要求していたため、エージェントが常用する `bash -euo pipefail -c '…'` が
  # 素通りしていた。シェル名の語の後ろに `c` を含む束が来れば足すので過検知に
  # 振れるが、上記のとおり安全側。
  # stdin からコードを読ませる形 (`… | sh` / `bash <<< '…'`) と `$SHELL` も同じ扱い。
  # shellcheck disable=SC2016  # 正規表現中の $ とバッククォートはリテラル
  string_shells='(bash|sh|zsh|dash|ksh|fish|csh|tcsh|ash|mksh)'
  # shellcheck disable=SC2016  # 同上
  executes_string_arg='(^|[;&|[:space:](`/])(eval|watch|'"$string_shells"'([[:space:]]+[^[:space:]]+)*[[:space:]]+-[A-Za-z]*c[A-Za-z]*)([[:space:]]|$)'
  # shellcheck disable=SC2016  # 同上
  executes_string_stdin='\|[[:space:]]*([^[:space:]|]*/)?'"$string_shells"'([[:space:]]|$)|(^|[;&|[:space:](`/])'"$string_shells"'([[:space:]]+[^[:space:]]+)*[[:space:]]*<<<|\$\{?SHELL\}?([^A-Za-z0-9_]|$)'
  # `git submodule foreach '<cmd>'` と `git rebase --exec '<cmd>'` (`-x`) も文字列を
  # シェルで実行する。`watch '<cmd>'` は上の executes_string_arg 側。`-x` / `--exec` は
  # rebase に紐付ける (素の `-x` は `set -x` / `ls -x` にも現れる)。一致しても足すのは
  # クォート文字を落としたコピーだけなので、外れて過検知になる側は安全。
  # shellcheck disable=SC2016  # 正規表現中の $ とバッククォートはリテラル
  executes_string_git='(^|[;&|[:space:](`/])(submodule[[:space:]]+foreach|rebase[[:space:]]+(.*[[:space:]])?(--exec|-[A-Za-z]*x))'
  # 行継続 (バックスラッシュ+改行) で語が割れていても照合できるよう、継続を畳んだ
  # コピーで調べる。足すコピーは 2 本: クォート文字を削除したもの (`"gi""t push"`
  # 形が繋がる) と、空白に置き換えたもの (`--exec='git push'` の `git` が `=` に
  # 密着せず語頭に立つ)。どちらも `$'` / `$"` の `$` を先に落とす (`eval $'git push'`)。
  #
  # 実行される文字列の中の行継続は、内側のシェルが `\<改行>` ごと消して語を繋ぐ
  # (`bash -c 'git\<改行> push'` は git push)。シングルクォートの中では外側の
  # シェルが畳まないので、cmd_norm のままのコピーでは `git` と `push` が別の行に
  # 割れて照合できない。行継続を「削除」したコピーも足す (空白への置換では
  # `pu\<改行>sh` が `pu sh` に割れる)。削除だけなので $cmd の部分列の性質は保つ。
  cmd_joined="${cmd_norm//\\$'\n'/ }"
  if printf '%s' "$cmd_joined" | grep -qiE "$executes_string_arg" ||
    printf '%s' "$cmd_joined" | grep -qiE "$executes_string_stdin" ||
    printf '%s' "$cmd_joined" | grep -qiE "$executes_string_git"; then
    # エスケープされた空白 / タブを素の区切りにしたコピーも足す: strip_quoted_ranges
    # は `\ ` を残すが、`eval git\ push` / `sh -c git\ push` の 1 語の引数は内側の
    # シェルが `git push` に割る。
    for cmd_src in "$cmd_norm" "${cmd_norm//\\$'\n'/}" "${cmd_norm//\\ / }" \
      "${cmd_norm//\\$'\t'/$'\t'}"; do
      cmd_unquoted="${cmd_src//\$\'/}"
      cmd_unquoted="${cmd_unquoted//\$\"/}"
      cmd_for_match="${cmd_for_match}
$(printf '%s' "$cmd_unquoted" | tr -d "\"'")
$(printf '%s' "$cmd_unquoted" | tr "\"'" '  ')"
    done
  fi

  # コマンド文字列のどこかに git ... push が含まれるか(チェーン・サブシェル・
  # コマンド置換含む。バッククォートも $(...) と同様コマンド開始境界になる)。
  # フラグは「値が = で連結される形式 (--git-dir=/x)」と「スペースで区切られる
  # 形式 (git -C /repo push)」の両方を許容する (値はフラグと誤読しないよう
  # 先頭が - 以外のトークンに限定)。push の直後は空白・行末だけでなく、
  # `;` `&` `|` `)` と閉じバッククォートも文の終端になり得る
  # (`git push;true` / `(git push)` / `$(git push)` を見逃さない)。リダイレクト
  # 演算子も同じ (`git push>/dev/null` / `git push</dev/null`): `<` `>` が終端
  # クラスに無いと、`push` の直後にリダイレクトを密着させた形だけが素通りする。
  #
  # 境界に `/` を含める理由は上の executes_string_arg と同じ: パス指定の
  # `/usr/bin/git push` も同じコマンドで、`/` が境界でないと裸の `git push` は
  # 捕まるのにパス付きだけ素通りするという不整合な穴が残る。-i も同様に、
  # case-insensitive な FS で `GIT push` が本当に git を走らせるため。
  #
  # git とサブコマンドの間の語のうち `$` かバッククォートを含むもの (展開を評価
  # できない語) は「何にでもなりうる = オプションにも、オプションとその値にも
  # なりうる」として、`-` で始まるフラグと同じ扱いで読み飛ばす。`-` で始まる語
  # しか許さないと、`D=--git-d; git ${D}ir=<B>/.git push` のような本物の push が
  # 検知されず、確認なしで通る (ゲートの喪失)。`git $SUB push` ($SUB が log 等)
  # まで確認に倒れるが、過検知は許容する。サブコマンド位置の語
  # (`git stash push` の stash 等) は読み飛ばさない。
  # 値を別の語で取るグローバルオプション (-C / -c / --git-dir / --work-tree 等) だけが
  # 次の語を値として読み飛ばす。それ以外のフラグ (--no-pager 等) の次の語は
  # サブコマンドなので読み飛ばさない (`git --no-pager log --grep push` の `push` は
  # log の引数であって push ではない)。展開を含む語は何にでもなりうるので従来どおり。
  # オプションとその値の語は `\` + 任意の 1 文字を語の一部として読む (git_opt_word)。
  # `[^[:space:]]+` だと `-C /tmp/a\ b` の値が `\` で切れ、`b` をサブコマンドと
  # 読んで push を見失う (`git -C` で始まるので ask: Bash(git push:*) にも掛からない)。
  git_opt_word='([^[:space:]\\]|\\.)+'
  # shellcheck disable=SC2016  # 正規表現中の $ とバッククォートはリテラル(展開させない)
  git_opt_unit='[[:space:]]+((-C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env|--attr-source)[[:space:]]+'"$git_opt_word"'|-'"$git_opt_word"'|[^[:space:]]*[$`][^[:space:]]*([[:space:]]+[^-[:space:]][^[:space:]]*)?)'
  # shellcheck disable=SC2016  # 同上
  git_exp_unit='[[:space:]]+[^[:space:]]*[$`][^[:space:]]*([[:space:]]+[^-[:space:]][^[:space:]]*)?'
  # shellcheck disable=SC2016  # 同上
  push_head='(^|[;&|[:space:](`/])git'
  # shellcheck disable=SC2016  # 同上
  # `\` も終端に含める: インタプリタ経路のコピーには `printf 'git push\n' | bash` の
  # `\n` が残り、`push` の直後が `\` になる。主の照合対象 (strip_quoted_ranges の出力)
  # ではクォート外の `\n` は `n` になるので、ここが効くのはコピーだけ。
  push_tail='[[:space:]]+push([[:space:];&|)<>`\\]|$)'
  push_exp_re="${push_head}(${git_opt_unit})*${git_exp_unit}(${git_opt_unit})*${push_tail}"
  # cmd_for_match はクォート区間の中身ごと消しているので、`git "${OPT}" <dir>
  # push` (OPT=-C なら本物の push) は `git  <dir> push` になり、<dir> がフラグでも
  # 展開でもないため検知できない (確認なしで通る)。クォート「文字」だけを
  # 落として中身を残したコピーに対し、「git と push の間に展開を含む語がある」
  # 形 (push_exp_re) に限って追加で照合する。引用テキストの `echo "git $x push"`
  # まで確認に倒れる過検知は安全側として許容する。展開を含まない引用テキスト
  # (`git commit -m "... git push ..."`) はこの追加照合に掛からず、静かなまま。
  cmd_quotes_stripped=$(printf '%s' "$cmd_norm" | tr -d "\"'")
  # `git … push` の語順を取らない push。permissions.ask は `git push` の前方一致で、
  # `Bash(git:*)` は allow なので、ここで拾わなければ確認なしで走る。
  #   - `git send-pack` / `git http-push`: `git push` が内部で走らせる転送そのもの。
  #   - `git subtree push`: push はサブコマンドの 2 語目 (`subtree -P <dir> push` の
  #     ように間へオプションが入る)。同じセグメント内の語だけを挟んで許す。
  #   - alias: `git -c alias.p=push p` は別名で push する。`git config alias.ps push` は
  #     以後の `git ps` を push にする (後の呼び出しはこのフックから見えないので、
  #     定義する時点が確認できる唯一の機会)。値の中身は解釈しない: git の後ろに
  #     `-c alias.` か `config … alias.` があり、コマンドのどこかに push の 4 文字が
  #     あれば確認へ倒す (`alias.lg 'log --grep=push'` も確認になるが、過検知は安全側)。
  #     照合はシェルが読む綴り (cmd_for_match: クォート区間を除き、`\` を解いたもの) に
  #     掛ける。クォート文字だけ落とした綴りに掛けると、コミットメッセージが alias に
  #     触れるだけで確認になり、逆に `git conf\ig alias.p push` は素通りする。
  #     クォートした値 (`-c 'alias.p=push --force'`) は cmd_for_match から消えるので、
  #     「実行される `git … -c` がある」ことを cmd_for_match で確かめたうえで、クォート
  #     文字を落とした綴りに `-c alias.` を探す。
  # どれも「このリポジトリの現在ブランチを upstream へ」という通常の要約が当てはまら
  # ないので、理由を indirect_push に持ち、下で要約の代わりに出す。
  # shellcheck disable=SC2016  # 正規表現中のバッククォートはリテラル
  pack_tail='[[:space:]]+(send-pack|http-push)([[:space:];&|)<>`\\]|$)'
  # shellcheck disable=SC2016  # 同上
  subtree_tail='[[:space:]]+subtree([[:space:]]+[^[:space:];&|)]+)*'"$push_tail"
  # shellcheck disable=SC2016  # 同上
  alias_re="${push_head}"'[[:space:]]([^;&|]*[[:space:]])?(-c[[:space:]]+alias\.|config[[:space:]]([^;&|]*[[:space:]])?alias\.)'
  # shellcheck disable=SC2016  # 同上
  dash_c_re="${push_head}"'[[:space:]]([^;&|]*[[:space:]])?-c([[:space:]]|$)'
  # shellcheck disable=SC2016  # 同上
  alias_c_re="${push_head}"'[[:space:]]([^;&|]*[[:space:]])?-c[[:space:]]+alias\.'
  indirect_push=""
  if ! echo "$cmd_for_match" | grep -qiE "${push_head}(${git_opt_unit})*${push_tail}"; then
    if printf '%s\n' "$cmd_quotes_stripped" | grep -qiE "$push_exp_re"; then
      :
    elif echo "$cmd_for_match" | grep -qiE "${push_head}(${git_opt_unit})*${pack_tail}"; then
      indirect_push="the command runs git send-pack / http-push, the transport git push itself uses"
    elif echo "$cmd_for_match" | grep -qiE "${push_head}(${git_opt_unit})*${subtree_tail}"; then
      indirect_push="the push goes through git subtree, which sends a split of the history rather than the current branch"
    elif echo "$cmd_for_match" | grep -qiE "$alias_re" ||
      { echo "$cmd_for_match" | grep -qiE "$dash_c_re" &&
        printf '%s\n' "$cmd_quotes_stripped" | grep -qiE "$alias_c_re"; }; then
      indirect_push="the command defines or uses a git alias together with the word push, so a push may run under another name"
    else
      return 1
    fi
  fi

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
  # 一切関与しない (フィールド1が "1" かつフィールド2が "") 場合は、
  # git_c_dir_from_words の -C 解決にフォールバックする (use_legacy_c_lookup)。
  # git_c_dir_from_words 自体は push の引数 (push_report の材料) を取るため、-C が不要な
  # 経路でも 1 回走らせる。
  #
  # cd_target_from_words 自身が push セグメントを見失った場合 (フィールド1が
  # "0"。4000 文字上限超えで呼んでいない場合を含む) は、push の実在は上の検知で
  # 既に確定しているため「このコマンドに対する自前の解析結果を信用できない」印
  # であって「cd が無い」証明にはならない (`bash -c "cd T && git push"` /
  # `git commit -m "v $(date)" && cd T && git push` のように、実行文字列や
  # 引用符解決が絡むと簡易トークナイザは git/push トークンごと見失う)。この
  # 場合はまず cd/pushd/popd らしき語が単語境界だけで見つかるかどうかを見る:
  # 見つかればフックの cwd へのフォールバックはせず要約を諦める。
  #
  # ここで見る対象は cmd_for_match (strip_quoted_ranges の出力) ではなく、
  # cmd_norm からクォート「文字」だけを落として中身を残した文字列にする。
  # strip_quoted_ranges はクォート区間の中身を丸ごと消すため、`"cd" <dir> &&
  # git push` のようにクォートされてはいるが実際には cd ビルトインとして
  # 実行される語 (コマンド名はクォートしても意味が変わらない) が消えてしまい、
  # フックの cwd の要約に後退する。過検知はコミットメッセージ本文にまで
  # 広がりうるが、出るのは note だけなので安全側。
  # 見つからなければ「cd は無さそうだが push セグメントの位置は分からない」
  # 状態であり、git_c_dir_from_words 単体も同じ desync の影響を受けうるので、
  # 同じ legacy フォールバックに委ねる (cd 解析が push を見失ったせいで、
  # git_c_dir_from_words なら拾える -C がフックの cwd に後退するのを防ぐ)。
  #
  # 生の制御文字 (タブ・改行以外の U+0000-U+001F と U+007F) を含むコマンドでは、
  # 行き先の解決自体をしない (note を出す)。2 つのトークナイザは \x01〜\x08・
  # \x0e・\x1c を区切りや印として帯域内で使っており、入力の同じバイトと衝突する:
  # `echo x <0x02> cd <repo> && git push` の cd は実シェルでは echo の引数に
  # 過ぎないのに独立したセグメントとして解決され、<repo> の要約が出てしまう
  # (-C 側も \x01 で `git -C <repo> push` のセグメントを偽造できる)。\r も対象に
  # 含める: bash は \r を空白ではなく語の一部として扱う一方、端末には表示されず、
  # 読み手には存在しない語境界に見えるため。判定は jq で復号後の文字列に対して
  # 行う: bash の $(...) は NUL を黙って落とすので、bash 側の検査では見えない。
  # jq が空/異常な出力を返した場合も note 側 (安全側) に倒れる。
  #
  # `--git-dir` / `--work-tree` / `GIT_DIR` / `GIT_WORK_TREE` がコマンドのどこかに
  # 現れる場合も解決しない (note)。これらは -C も cd も使わずに push 対象の
  # リポジトリを差し替える (`git --git-dir=<B>/.git push` / `GIT_DIR=<B>/.git git
  # push` / `export GIT_DIR=...; git push`) のに、要約は -C と cd しか追わず
  # フックの cwd のリポジトリを出してしまう。値の解決はせず名前の出現だけを見る。
  # 照合前にクォート・バックスラッシュ・改行を落とす (`--git-d""ir` や
  # `--git\-dir` もシェルにとっては --git-dir)。引用テキスト内の言及まで拾う
  # 過検知になるが、出るのは note だけなので安全側。
  #
  # 最後に、決まった行き先が実在するディレクトリでなければ note にする。cd は
  # そこで失敗して push は別の場所 (`;` なら元の cwd) で走るので、何のリポジトリの
  # 要約でもなく「確認できない」と伝える方が正しい。
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
  # CDPATH はフック自身の環境だけでなく、コマンドの中で設定されうる
  # (`CDPATH=<alt>; cd sub` / `export CDPATH=...`)。どちらかで効きうるなら
  # cd_target_from_words に伝え、`/`・`./`・`../` で始まらない相対の cd を解決
  # 不能にさせる。zsh の `cdpath` (CDPATH と連動する配列) の出現も同じ扱い。
  cdpath_live=0
  [ -n "$CDPATH" ] && cdpath_live=1
  case "$cmd_probe" in
  *CDPATH* | *cdpath*) cdpath_live=1 ;;
  esac
  # push が検知された git の、サブコマンドより前 (グローバルオプション位置) に
  # 評価できない展開を含む語がある場合、その語は --git-dir=... 等で push 対象を
  # 差し替えうるので要約しない (note)。
  #
  # 照合は cmd_for_match だけでなく、クォート「文字」だけを落として中身を残した
  # コピー (cmd_quotes_stripped) にも掛ける。cmd_for_match は strip_quoted_ranges
  # がクォート区間の中身ごと消しているため、`git "${D}ir=<B>/.git" push` や
  # `git "$GIT_OPTS" push` のダブルクォート内の展開 (シェルは展開する) が見えず、
  # フックの cwd の要約が出てしまう。シングルクォート内の `$` はシェルにとって
  # リテラルなので過検知になるが、出るのは note だけ。push より後ろの語
  # (`git push origin "$BRANCH"`) は正規表現の位置上この判定に掛からず、通常の
  # 要約のまま。cmd_quotes_stripped と push_exp_re は上の検知で作ったものを使う
  # (検知側も同じコピーで引用内の展開を拾うので、検知と要約の判断が揃う)。
  push_via_expansion=0
  if echo "$cmd_for_match" | grep -qiE "$push_exp_re" ||
    printf '%s\n' "$cmd_quotes_stripped" | grep -qiE "$push_exp_re"; then
    push_via_expansion=1
  fi
  # push の引数と -C を語単位で 1 回だけ解析する (結果は gp_c_dir / gp_push_args)。
  # 生の制御文字を含むコマンドと 4000 文字超は解析しない (理由は下のコメントと
  # strip_quoted_ranges の上限の説明を参照)。-C が不要な経路でも push の引数
  # (push_report の材料) のために回す。
  gp_ran=0
  gp_c_dir=""
  gp_c_foreign=0
  gp_push_found=0
  gp_push_multi_dir=0
  gp_push_args=()
  if [ "$ctrl_count" = "0" ] && [ "${#cmd_norm}" -le 4000 ]; then
    git_c_dir_from_words "$cmd_norm"
    gp_ran=1
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

  # push セグメントを語単位で見つけられなかった (push がラッパー `timeout 5 git …` /
  # `env X=1 git …` / `nice git …` の後ろや、`bash -c '…'` の文字列の中にある) のに
  # コマンドに -C があるなら、その -C が push の宛先である可能性を否定できない。
  # 読み飛ばしてフックの cwd を要約すると、別リポジトリの要約を黙って出すことになる。
  if [ "$use_legacy_c_lookup" -eq 1 ] && [ "$gp_ran" -eq 1 ]; then
    if [ "$gp_c_foreign" -eq 1 ]; then
      unresolved_why="a -C on another git call in the command could not be tied to the push (the push is inside a string or wrapper this hook cannot follow)"
    elif [ "$gp_push_found" -eq 0 ] && [ -z "$gp_c_dir" ] &&
      printf '%s\n' "$cmd_quotes_stripped" | grep -qE '(^|[[:space:]])-C([[:space:]]|$)'; then
      unresolved_why="the command has a -C this hook could not tie to the push (it sits behind a wrapper or inside a string this hook does not follow)"
    elif [ "$gp_push_multi_dir" -eq 1 ]; then
      unresolved_why="the command runs more than one push with different -C directories"
    else
      case "$gp_c_dir" in
      *[[:cntrl:]]*) unresolved_why="the -C directory contains control characters" ;;
      ?*) git_c_opt=(-C "$gp_c_dir") ;;
      esac
    fi
  fi

  # `git submodule foreach '<cmd>'` の push は各サブモジュールの中で走る。フックの
  # cwd のリポジトリの要約は別のリポジトリのものになるので、要約を諦める。
  if [ -z "$unresolved_why" ] &&
    printf '%s\n' "$cmd_quotes_stripped" | grep -qiE 'submodule[[:space:]]+foreach'; then
    unresolved_why="the push runs inside git submodule foreach, i.e. in each submodule rather than in this repository"
  fi

  # 別名・subtree・send-pack 経由の push (上の indirect_push)。要約が語る「現在
  # ブランチの未 push コミット」は、このコマンドが送るものと一致しない。
  if [ -z "$unresolved_why" ] && [ -n "$indirect_push" ]; then
    unresolved_why="$indirect_push"
  fi

  if [ -z "$unresolved_why" ] && [ "${#git_c_opt[@]}" -gt 0 ] && [ ! -d "${git_c_opt[1]}" ]; then
    unresolved_why="the directory this hook read as the push target does not exist"
  fi

  # 現在ブランチは push の引数の説明 (build_push_report) が refspec の source との
  # 照合に使う。要約を出さないとき (宛先リポジトリ不明) は空のまま = 照合しない。
  branch=""
  inside_repo=0
  if [ -z "$unresolved_why" ] && git "${git_c_opt[@]}" rev-parse --is-inside-work-tree &>/dev/null; then
    inside_repo=1
    branch=$(git "${git_c_opt[@]}" rev-parse --abbrev-ref HEAD 2>/dev/null)
  fi
  build_push_report "$branch"
  commits_label="commits to push:"
  if [ "$push_foreign" -eq 1 ]; then
    commits_label="commits on the current branch (NOT necessarily what this push sends):"
  fi
  summary=""
  if [ -n "$unresolved_why" ]; then
    summary="(target repository could not be determined: ${unresolved_why} -- review the target manually before approving)"
  elif [ "$inside_repo" -eq 1 ]; then
    if git "${git_c_opt[@]}" rev-parse --abbrev-ref '@{upstream}' &>/dev/null; then
      commits=$(git "${git_c_opt[@]}" log --oneline '@{upstream}..HEAD' 2>/dev/null | head -10)
      stat=$(git "${git_c_opt[@]}" diff --stat '@{upstream}..HEAD' 2>/dev/null | tail -1)
    else
      commits=$(git "${git_c_opt[@]}" log --oneline -5 2>/dev/null)
      stat="(no upstream: new branch push)"
    fi
    summary="branch: ${branch}
${commits_label}
${commits:-"(none)"}
${stat}"
  fi

  # shellcheck disable=SC2034  # 呼び出し側 (アダプタ) が読む。このファイル内では参照しない
  push_review_body="${push_report}${summary}"
  return 0
}
