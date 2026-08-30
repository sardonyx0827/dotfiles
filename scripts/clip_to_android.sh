#!/bin/sh
# 標準入力を「Android から読める場所」へ書き出す。
#
# Android の Linux ターミナル (AVF) は、端末アプリが橋渡しをしない限り VM の
# クリップボードを本体へ持って行かない (ゲスト側から通知する口が無く、
# readClipboard を呼ぶかどうかは完全にアプリ側の都合)。一方 /mnt/shared は
# Android の共有ストレージ (/storage/emulated/0) が virtiofs で見えていて、
# VM から書ける。そこで
#
#   tmux でコピー → 共有ストレージにファイル → Android のブラウザで開いて
#   「コピー」を 1 タップ
#
# という経路を用意する。ブラウザで開くのは同時に書き出す index.html で、
# file:// では navigator.clipboard が使えない (セキュアコンテキストでない) ため
# execCommand('copy') に落ちる。それも塞がれていれば、長押し→全選択→コピーで
# 拾えるように内容をそのまま textarea に置いてある。
#
# 注意: 書き出した内容は Android のストレージに平文で残る。ストレージ権限を
# 持つアプリからは読める。パスワードのような物を流したら `--clear` で消すこと。
# 途中経過は VM 内の /tmp (tmpfs, 0600) も通るが、こちらは終了時に消す。
#
# set -e は付ける。書けなかったのに成功を返すと、貼り付けられると思い込む。
set -eu

SHARE_DIR="${CLIP_TO_ANDROID_DIR:-/mnt/shared/Download/tmux-clip}"
# Android 側から見えるパス。/mnt/shared が /storage/emulated/0 なので読み替える。
ANDROID_DIR="/storage/emulated/0/Download/tmux-clip"

notify() {
  # tmux から呼ばれたときだけステータス行に出す。copy-pipe の子には端末が無く、
  # stdout/stderr はどこにも表示されないため、これが唯一の反応になる。
  [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1 && tmux display-message "$1"
  return 0
}

die() {
  notify "clip_to_android: $1"
  echo "clip_to_android: $1" >&2
  exit 1
}

[ -d "$SHARE_DIR" ] || mkdir -p "$SHARE_DIR" 2>/dev/null ||
  die "$SHARE_DIR を作れません (Android の共有ストレージはマウントされていますか)"

# 後始末は先に仕掛ける。mktemp より前なのは、2 つ目が落ちたときに 1 つ目が
# 消えずに残るため。空文字を渡しても `rm -f` は黙って 0 を返すので、まだ作って
# いない分は素通りする。
#
# EXIT だけでは足りない: dash はシグナルで死ぬとき EXIT trap を走らせないので、
# 入力待ちのところで Ctrl-C されると選択範囲の平文が /tmp と共有フォルダに
# 残る。INT/TERM/HUP でも同じ後始末を通す。
payload=""
escaped=""
new_html=""
new_txt=""
cleanup() { rm -f "$payload" "$escaped" "$new_html" "$new_txt"; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM HUP

# 読み書きの中身は /tmp (tmpfs, 0600) に置く。共有フォルダ側に置くと、Android
# のストレージへ平文が出る時間がその分だけ延びる。
payload=$(mktemp)
escaped=$(mktemp)

# 差し替え用は mv を原子的にしたいので同じディレクトリに作る。固定名にしない
# のは、先回りしてシンボリックリンクを置かれると mv/cp がそれを辿って任意の
# ファイルを上書きしうるため。
new_html=$(mktemp -p "$SHARE_DIR" .clip.XXXXXXXX) ||
  die "$SHARE_DIR へ書き込めません"
new_txt=$(mktemp -p "$SHARE_DIR" .clip.XXXXXXXX) ||
  die "$SHARE_DIR へ書き込めません"

if [ "${1:-}" = "--clear" ]; then
  : >"$payload"
else
  cat >"$payload"
  # VM 側のクリップボードにも入れる (pbcopy が無い環境では黙って諦める)。
  if command -v pbcopy >/dev/null 2>&1; then
    pbcopy <"$payload" || notify "clip_to_android: pbcopy に失敗しました"
  fi
fi

bytes=$(wc -c <"$payload" | tr -d ' ')
stamp=$(date '+%H:%M:%S')

sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' "$payload" >"$escaped"
# 改行で終わっていない入力に sed が改行を足す実装がある (GNU sed は足さない)。
# textarea の中身はそのままコピーされるので、1 バイト増えれば原文と変わる。
# 「元は改行で終わっていない」かつ「sed の出力は改行で終わっている」ときだけ
# 削る -- 実装を決め打ちして無条件に削ると、GNU sed では最後の 1 文字が消える。
# `[ -n "$(tail -c1 f)" ]` が真 = 最終バイトが改行ではない (コマンド置換が
# 末尾の改行を落とすため)。
if [ -s "$payload" ] && [ -n "$(tail -c1 "$payload")" ] &&
  [ -z "$(tail -c1 "$escaped")" ]; then
  truncate -s -1 "$escaped"
fi

# 書き込みは全部 die を通す。copy-pipe から呼ばれた子には端末が無く、
# set -e で落ちるだけだと画面には何も出ない = 成功したように見える。
{
  cat <<'HTML_HEAD'
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>tmux clip</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; padding: 12px; font: 15px/1.5 system-ui, sans-serif; }
  header { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  h1 { font-size: 16px; margin: 0; }
  .meta { color: #888; font-size: 13px; }
  button { font-size: 17px; padding: 12px 20px; margin: 10px 0; width: 100%;
           border-radius: 8px; border: 1px solid #888; background: transparent;
           color: inherit; }
  textarea { width: 100%; box-sizing: border-box; min-height: 55vh;
             font-family: ui-monospace, monospace; font-size: 14px;
             white-space: pre; }
  #status { min-height: 1.5em; font-size: 14px; }
</style>
</head>
<body>
<header><h1>tmux → Android</h1><span class="meta" id="meta"></span></header>
<button id="copy">コピー</button>
<div id="status">3 秒ごとに自動で読み直します</div>
<textarea id="text" readonly spellcheck="false">
HTML_HEAD
  cat "$escaped"
  cat <<'HTML_TAIL'
</textarea>
<script>
  var text = document.getElementById("text");
  var status = document.getElementById("status");
  var timer = setTimeout(function () { location.reload(); }, 3000);

  function stopReload(message) {
    clearTimeout(timer);
    status.textContent = message;
  }

  document.getElementById("copy").addEventListener("click", function () {
    stopReload("コピーしました（自動更新は停止中。再開はページを再読み込み）");
    var value = text.value;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).catch(function () { fallback(); });
      return;
    }
    fallback();
  });

  function fallback() {
    // file:// はセキュアコンテキストではないので navigator.clipboard が無い。
    text.focus();
    text.setSelectionRange(0, text.value.length);
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    if (!ok) {
      stopReload("コピーできませんでした。テキストを長押し→全選択→コピーしてください");
    }
  }

  text.addEventListener("touchstart", function () {
    stopReload("自動更新を停止しました（再開はページを再読み込み）");
  }, { passive: true });
</script>
HTML_TAIL
  printf '<script>document.getElementById("meta").textContent = "%s / %s バイト";</script>\n' \
    "$stamp" "$bytes"
  cat <<'HTML_END'
</body>
</html>
HTML_END
} >"$new_html" || die "$SHARE_DIR へ書き込めません"

cp -f "$payload" "$new_txt" || die "$SHARE_DIR へ書き込めません"

# どちらも temp → mv で差し替える。ページは 3 秒ごとに読み直されるので、
# 書きかけを読ませない。
mv -f "$new_html" "$SHARE_DIR/index.html" || die "index.html を差し替えられません"
mv -f "$new_txt" "$SHARE_DIR/latest.txt" || die "latest.txt を差し替えられません"

if [ "${1:-}" = "--clear" ]; then
  notify "clip_to_android: 共有ストレージの内容を消しました"
else
  notify "clip_to_android: $bytes バイトを $ANDROID_DIR へ書き出しました"
fi
