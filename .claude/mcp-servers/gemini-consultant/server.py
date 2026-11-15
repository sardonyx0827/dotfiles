#!/usr/bin/env python3
import asyncio
import contextlib
import http.client
import importlib.util
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Callable, Optional, Tuple

# MCP SDK は Claude Code が MCP サーバーを起動する環境で解決される実行時依存で、
# CI の lint ジョブには入れていない (型検査のためだけに pydantic/httpx を含む
# 依存ツリーを固定・追従するコストに見合わない)。FastMCP の API 誤用まで検査
# したくなったら、ここの ignore を外して CI に mcp を固定導入すること。
from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]

mcp = FastMCP("gemini-consultant")

# -------------------------------------------------------------------
# モデル設定
# -------------------------------------------------------------------
# 用途別にモデルを分離
# 深い推論が必要な設計相談 → Pro
# 局所的・高頻度なチェック → Flash
# Gemini 4 から Pro / Flash の名前が消え、gemini-pro-latest / gemini-flash-latest
# が Gemini 4 へ移るかは公式に示されていないため、既定は具体的な ID で書く。
# consult_gemini は advisor の代役 (rules/second-opinion.md) を務め、advisor はメインモデルと
# 同等以上でないと意味がないので、Pro 相当は最上位の Carbon にする。
# Flash 相当は Hydrogen。
DEEP_MODEL = os.environ.get("GEMINI_PRO_MODEL", "gemini-4-carbon")
LIGHT_MODEL = os.environ.get("GEMINI_FLASH_MODEL", "gemini-4-hydrogen")

# -------------------------------------------------------------------
# ログ設定
# -------------------------------------------------------------------
log_file = os.path.expanduser("~/.claude/logs/gemini-consultant.log")

# ログにはプロンプト全文と Gemini の応答全文が入る。umask 任せ (0755 / 0644) だと
# 同じホストの他ユーザーが読めるので、ディレクトリは 0700、ファイルは 0600 にする。
# 共有モジュール (_bash_review_common.py) の ensure_private_dir / _open_private と
# 同じ方針だが、ログの秘匿はそのモジュールが読めるかどうかに依存させない
# (読めないときこそ BLOCK の記録が要る) ので、ここで自前に持つ。
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600

os.makedirs(os.path.dirname(log_file), mode=_PRIVATE_DIR_MODE, exist_ok=True)

MAX_LOG_LINES = 500


def _ensure_private_log_dir() -> None:
    """ログディレクトリを (無ければ 0700 で作り、既存でも) 0700 に絞る。書けない所は拒む。

    os.makedirs の mode は新規作成時にしか効かない。以前の版が 0755 で作った
    ディレクトリは、次の書き込みでここが絞る。

    次のどちらかなら OSError を送出して書き込みを諦めさせる (応答は握りつぶす側
    _log_quietly が守るので、失われるのはログ 1 件だけ):
    - 他人所有のディレクトリ。そこでは相手がファイルを差し替えられるので、
      プロンプト全文を置かない。chmod もしない (できないし、触るべきでもない)。
    - 絞った後も group / other が書ける。差し替えと symlink すり替えの余地が残る。
    読めるだけなら諦めない: 中身は 0600 のファイルが守る。
    POSIX の所有者・モードを持たない環境 (Windows) では検査できないので何もしない。
    """
    directory = os.path.dirname(log_file) or "."
    os.makedirs(directory, mode=_PRIVATE_DIR_MODE, exist_ok=True)
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return
    st = os.stat(directory)
    if st.st_uid != geteuid():
        raise PermissionError(f"log directory {directory} is not owned by this user")
    if st.st_mode & 0o077:
        os.chmod(directory, _PRIVATE_DIR_MODE)
        st = os.stat(directory)
    if st.st_mode & 0o022:
        raise PermissionError(f"log directory {directory} is writable by others")


def _open_private(path: str, flags: int) -> int:
    """open() の opener。0600 で作り、既存ファイルが緩ければ 0600 に絞る。

    - O_NOFOLLOW: ログの位置に symlink が置かれていても辿らない (辿ると別の
      ファイルへプロンプト全文を追記する)。無い環境 (Windows) では 0 になる。
    - 絞りは fd に対して行う (fchmod)。chmod(path) は名前を引き直すので、open と
      chmod の間に差し替えられたパスを絞ってしまい、開いたファイルは緩いまま残る。
    os.open の mode 引数は新規作成時にしか効かないので、以前の版が 0644 で作った
    ファイルはここで絞らないと緩いまま追記され続ける。絞ってから書くため、
    絞れなければ何も書かれない。
    """
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), _PRIVATE_FILE_MODE)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(fd, _PRIVATE_FILE_MODE)
            else:  # fchmod の無い環境 (Windows)
                os.chmod(path, _PRIVATE_FILE_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


# ツールはワーカースレッドで走るので _append_log は並行に呼ばれる。ロック無しだと
# (1) ストリームバッファ (8 KB) を超えるエントリが複数の write(2) に分かれて
#     他スレッドのエントリと混ざる (全文ログは普通にこれを超える)
# (2) 読み → 切り詰め → os.replace のローテーションが、同時に走ったスレッドの
#     追記を丸ごと巻き戻す (lost update)
# の 2 つでログが静かに壊れる。os.replace の原子性は (2) の途中経過を守らない。
# 追記とローテーションを 1 つの臨界区間にする。プロセス間の衝突は対象外。
_LOG_LOCK = threading.Lock()


def _append_log(lines: list[str]) -> None:
    """ログファイルに書き込み、上限を超えた分を古い順に削除する。

    切り詰めは読んで書き戻す操作なので、並行して呼ぶと衝突する (_LOG_LOCK で
    スレッド間は直列化する)。open(log_file, "w") でその場 truncate すると、
    切り詰めと書き込みの間にログが 0 バイトになる窓ができる。共通側
    _bash_review_common.py の append_and_rotate と同じく、プロセスごとに一意な
    一時ファイルへ書いてから os.replace で不可分に差し替える。
    """
    with _LOG_LOCK:
        _ensure_private_log_dir()
        # 追記と読み戻しは同じ fd で行う: 名前を引き直す 2 回目の open だと、
        # 1 回目の後に差し替えられたパスを読んでしまう。
        with open(log_file, "a+", encoding="utf-8", opener=_open_private) as f:
            f.writelines(lines)
            f.seek(0)
            all_lines = f.readlines()

        if len(all_lines) <= MAX_LOG_LINES:
            return

        # mkstemp は 0600 で作るので、差し替え後のログも 0600 のまま。
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(log_file) or ".",
            prefix=os.path.basename(log_file) + ".",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(all_lines[-MAX_LOG_LINES:])
            os.replace(tmp, log_file)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def log_entry(tool: str, status: str, prompt: str, response: str = "") -> None:
    """
    プロンプト全文と Gemini レスポンス全文をログに記録する。

    フォーマット:
        --- [timestamp] STATUS | tool ---
        [PROMPT]
        ...プロンプト全文...

        [RESPONSE]
        ...レスポンス全文（省略なし）...
        ---
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    separator = "-" * 60 + "\n"

    lines = [
        separator,
        f"[{timestamp}] {status:5s} | {tool}\n",
        "[PROMPT]\n",
        prompt + "\n",
    ]

    if response:
        lines += [
            "\n[RESPONSE]\n",
            response + "\n",
        ]

    lines.append(separator)

    _append_log(lines)


def _log_quietly(tool: str, status: str, prompt: str, response: str = "") -> None:
    """ログ書き込みの失敗で Gemini の応答そのものを失わせない。

    _append_log はローテーション失敗時、一時ファイルを片付けたうえで OSError を
    再送出する (中途半端な状態のまま黙るより呼び出し側に伝える、という設計)。
    ただし tool にとってログは副作用でしかなく、応答は既に API 呼び出しを払って
    得た成果物なので、ここで握って応答を優先する。

    握る型は 2 つ。OSError は書き込み・ローテーション失敗 (握らないと tool の外まで
    抜ける: except は ValueError と (URLError, TimeoutError, ...) だけで、素の
    OSError はどちらにも該当しない。URLError は OSError の *サブクラス* であって
    親ではない)。UnicodeError は応答が UTF-8 で書けない場合 — lone surrogate は
    json.loads を通るがファイルへは書けず、UnicodeEncodeError は ValueError の
    サブクラスなので、握らないと `except ValueError` が「APIキー未設定」という
    事実と異なる通知を出したうえで応答を捨てる。

    ValueError 全体ではなく UnicodeError に絞るのは、ログの整形バグ (書式指定子の
    取り違え等) まで無言で飲み込まないため。
    """
    try:
        log_entry(tool, status, prompt, response)
    except (OSError, UnicodeError) as exc:
        # stdout は MCP の JSON-RPC 専用なので絶対に使わない。完全な無言だと
        # 「ログが恒久的に書けない」状態に誰も気付けないため stderr にだけ残す。
        #
        # その警告自体も握る: stderr が閉じていれば print は BrokenPipeError
        # (= OSError) を投げ、それは呼び出し側の except をどれも通らず tool の外
        # まで抜ける — この関数が塞いだのと寸分違わぬ失敗モードを、報告のために
        # 作り直すことになる。
        with contextlib.suppress(OSError):
            print(f"gemini-consultant: log write failed: {exc}", file=sys.stderr)


# -------------------------------------------------------------------
# 通知
# -------------------------------------------------------------------
def _sanitize_notify(text: str, limit: int = 200) -> str:
    """通知用に制御文字を除去し長さを制限する。

    正典コピーは .claude/hooks/_bash_review_common.py の _sanitize_notify。
    秘密スキャンはその共有モジュールを realpath 経由で読む (下の
    _load_scan_secrets) が、通知は「共有モジュールが読めない」という事実そのものを
    知らせる場面でも鳴らす必要がある。読み込みに依存させないため、小さなヘルパーを
    ここに複製したままにしてある。
    """
    cleaned = "".join(ch for ch in text if ch.isprintable())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


def notify(title: str, message: str, timeout: int = 5) -> None:
    try:
        os_name = platform.system()
        safe_title = _sanitize_notify(title, limit=100)
        safe_message = _sanitize_notify(message, limit=200)

        if os_name == "Darwin":
            # printenv 経由で値を取得して AppleScript 注入 (title/message 中の
            # ダブルクォート等でスクリプトを抜け出す攻撃) と、system attribute の
            # MacRoman 解釈による日本語文字化けの両方を回避する
            script = (
                'set titleText to do shell script "printenv CLAUDE_NOTIFY_TITLE || true"\n'
                'set msgText to do shell script "printenv CLAUDE_NOTIFY_MESSAGE || true"\n'
                "display notification msgText with title titleText"
            )
            subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                env={
                    **os.environ,
                    "CLAUDE_NOTIFY_TITLE": safe_title,
                    "CLAUDE_NOTIFY_MESSAGE": safe_message,
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )

        elif os_name == "Linux":
            subprocess.run(
                [
                    "notify-send",
                    "--expire-time",
                    str(timeout * 1000),
                    safe_title,
                    safe_message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )

        elif os_name == "Windows":
            # Windows 専用の任意依存。入っていないことが正常なので、mypy の
            # import 解決失敗は指摘ではない (_bash_review_common.notify と同じ)。
            from win10toast import ToastNotifier  # type: ignore[import-not-found]

            toaster = ToastNotifier()
            toaster.show_toast(safe_title, safe_message, duration=timeout)

    except Exception:
        pass


# -------------------------------------------------------------------
# Gemini API 呼び出し（リトライ付き）
# -------------------------------------------------------------------
class GeminiKeyError(Exception):
    """GEMINI_API_KEY が未設定、または HTTP ヘッダ値として使えない。

    ValueError の *サブクラスにしない* のが要点。素の ValueError で表すと、
    ツール側の `except ValueError` が
    (a) 本来の「キー未設定」
    (b) json.JSONDecodeError (ValueError のサブクラス)
    (c) putheader が送出する「値つき」ValueError
    の 3 つを同じ腕で捕まえてしまう。JSON パース失敗にまで「APIキー未設定」と
    通知し、下の except に並ぶ JSONDecodeError は到達不能な死んだエントリになり、
    (c) では API キーそのものを戻り値とログへ流す。独立した型にすることで
    3 つを別々の腕へ分けられる。
    """


# 同じ要求を繰り返せば通るかもしれない状態だけ再試行する。それ以外
# (400 不正リクエスト / 403 キー無効 / 404 モデル名違い) は何度送っても同じ
# 答えなので、再試行は読み手に必要なメッセージを遅らせるだけになる。
# scripts/gemini_api.py の RETRYABLE_STATUS と同じ集合。
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# 上流のエラーメッセージを引用する上限。「model X is not found for API
# version v1beta」が丸ごと収まり、JSON の壁が応答を押し流さない長さ。
MAX_DETAIL = 400


class GeminiCancelled(Exception):
    """呼び出し側がキャンセルしたので、次の試行 (再試行) を行わなかった。

    他のどの except 腕にも掛からない独立した型にする (OSError / ValueError の
    サブクラスにすると、通常のエラーとして記録・通知されてしまう)。
    """


class GeminiUpstreamError(Exception):
    """API が 2xx 以外を返した。メッセージには状態コードと上流の error.message。

    URLError のサブクラスにはしない。HTTPError が URLError のサブクラスである
    ため、URLError の腕に流すと 400 系まで一律にリトライされてしまう。専用の型で
    識別してツール側の except に明示的に並べる。
    """


def _error_detail(raw: str) -> str:
    """エラー本文から error.message を取り出す。JSON でなければ本文そのもの。"""
    try:
        decoded = json.loads(raw)
    except ValueError:
        return raw.strip()[:MAX_DETAIL]
    if isinstance(decoded, dict):
        err = decoded.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:MAX_DETAIL]
    return raw.strip()[:MAX_DETAIL]


def _upstream_error(exc: urllib.error.HTTPError) -> GeminiUpstreamError:
    """HTTPError を、状態コードと上流メッセージを持つ例外に変換する。

    本文は Google が生成するエラー JSON で、こちらの API キーは含まれない。
    """
    fp = exc.fp
    try:
        raw = fp.read().decode("utf-8", errors="replace") if fp is not None else ""
    except (OSError, http.client.HTTPException):
        # 本文の読み出し自体が失敗しても、状態コードだけは伝える。
        raw = ""
    detail = _error_detail(raw)
    return GeminiUpstreamError(f"HTTP {exc.code}: {detail or exc.reason}")


def _reject_unusable_api_key(api_key: str) -> None:
    """ヘッダ値として送れないキーを、値を一切明かさずに拒否する。

    http.client は不正なヘッダ値を `Invalid header value %r` として送出する
    ため、putheader まで到達させた時点でキーが例外メッセージに載る。ここで
    先回りして弾き、理由だけを述べる。latin-1 を見るのは putheader の
    エンコード先がそれだから。
    """
    if "\r" in api_key or "\n" in api_key:
        raise GeminiKeyError(
            "GEMINI_API_KEY contains a newline, which is illegal in an HTTP "
            "header (a CRLF-terminated .env is the usual cause); "
            "the value is withheld here on purpose"
        )
    try:
        api_key.encode("latin-1")
    except UnicodeEncodeError:
        # from None: 元の例外のメッセージには問題の文字が載るため連鎖させない。
        raise GeminiKeyError(
            "GEMINI_API_KEY contains characters that cannot be sent in an HTTP "
            "header; the value is withheld here on purpose"
        ) from None


def _extract_text(body: object) -> str:
    """レスポンス本文をテキスト化し、途中で打ち切られていればそれを明示する。

    `finishReason` を見ずに parts を連結するだけだと、maxOutputTokens=8192 を
    送っておいて MAX_TOKENS の切り詰めを「完全な回答」として返すことになり、
    設計相談ツールとしては最悪の壊れ方になる (呼び出し側が途中までの論を
    完結したものとして採用する)。

    `candidates` が「キーは在るが空リスト」の場合、素朴な
    `.get("candidates", [{}])[0]` では既定値 [{}] が効かず IndexError になり、
    `list index out of range` という何も説明しない文字列だけが返る。
    プロンプト段階のブロックはこの形で来る。

    形の検査は isinstance で行う。parts に文字列や text=null が混じると
    `p.get("text", "")` が AttributeError / TypeError を投げ、ツール側のどの
    except 腕にも掛からず例外が MCP の外へそのまま抜ける
    (scripts/gemini_api.py の extract_text と同じ選別に揃える)。
    """
    if not isinstance(body, dict):
        body = {}
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        feedback = body.get("promptFeedback")
        blocked = feedback.get("blockReason") if isinstance(feedback, dict) else None
        if blocked:
            return f"[Gemini はプロンプトをブロックしました: blockReason={blocked}]"
        return "[Gemini から候補が返りませんでした]"

    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    text = "".join(
        p["text"]
        for p in (parts if isinstance(parts, list) else [])
        if isinstance(p, dict) and isinstance(p.get("text"), str)
    )

    # STOP が正常完了。それ以外 (MAX_TOKENS / SAFETY / RECITATION 等) は
    # answer が途中で切れているので、部分テキストは活かしつつ理由を添える。
    reason = candidate.get("finishReason") or ""
    if reason and reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        note = f"[Gemini の応答は完結していません: finishReason={reason}]"
        return f"{text}\n\n{note}" if text else note
    # 正常終了でも本文が空なら、それは「言うことが無かった」ではなく異常。
    # 空文字をそのまま返すと呼び出し側は前者として読むので、finishReason ではなく
    # 空であること自体を条件にして明示する (SAFETY 枝と同じ壊れ方の一般形)。
    if not text:
        return (
            f"[Gemini がテキストを返しませんでした: finishReason={reason or '(なし)'}]"
        )
    return text


def call_gemini(
    prompt: str,
    max_tokens: int = 8192,
    model: str | None = None,
    max_retries: int = 3,
    cancel: Optional[threading.Event] = None,
) -> str:
    # cancel は、待っている側 (async のツール) がキャンセルされたときに立つ。
    # ワーカースレッドは強制終了できないので、試行の合間に見て自分から退く:
    # 送信済みの 1 リクエストは終わるまで待つが、次の試行 (最大 180 秒 x 残り回数
    # とバックオフ) は始めない。None なら従来どおり (直接呼ぶ側・テスト)。
    # strip() は体裁ではなくセキュリティ上の措置。CRLF 改行の .env を source すると
    # 値の末尾に "\r" が残るが、http.client.putheader は CR/LF を含むヘッダ値を
    # 拒否し、その ValueError のメッセージに *生の値* を埋め込む
    # (`Invalid header value b'AIza...\r'`)。call_gemini の内側 except は
    # ValueError を捕まえないため、putheader まで届けば例外はツール側へ抜ける。
    # ツール側の最後の `except ValueError` 腕は型名しか残さないが、それに
    # 頼らず strip() と _reject_unusable_api_key で putheader の手前で止める。
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiKeyError("GEMINI_API_KEY not set")
    # strip() が落とせるのは前後だけなので、値の内部に混じった CR/LF や
    # latin-1 で送れない文字はここで自前に弾く。例外メッセージには理由だけを
    # 載せ、値は決して載せない (putheader に到達させたら値が漏れる)。
    _reject_unusable_api_key(api_key)

    resolved_model = model or DEEP_MODEL

    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 1.0,
            },
        }
    ).encode("utf-8")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{resolved_model}"
        f":generateContent"
    )

    last_error: Exception | None = None

    for attempt in range(max_retries):
        # 待ち (バックオフ) の間に立った場合もここで拾う。初回は呼ばれた時点で
        # 立っていても送る意味が無いので、同じ検査を通す。
        if cancel is not None and cancel.is_set():
            raise GeminiCancelled("cancelled before attempt")
        # リトライのたびに Request を再生成する
        # （urllib.request.Request は一度消費されるため）
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            # 180 秒: 応答時間は主に出力の長さで決まる。gemini-4-carbon の実測で
            # 入力 6.4K / 出力 2.6K トークンが 41 秒 (約 60 トークン/秒) なので、
            # 長い回答では 90 秒に届く。最悪 3 回 x 180 秒 + バックオフで約 9 分で、
            # Claude Code 側の MCP 呼び出し上限 (MCP_TOOL_TIMEOUT、既定 約 28 時間) より
            # 十分短い。
            with urllib.request.urlopen(req, timeout=180) as resp:  # nosec: B310
                body = json.loads(resp.read().decode("utf-8"))
                return _extract_text(body)
        except urllib.error.HTTPError as e:
            # HTTPError は URLError のサブクラスなので、この腕は必ず下の腕より
            # 前に置くこと。下の腕が 400/403/404 まで拾うと同じ答えを 3 回
            # 受け取る (待ち時間 3 秒) うえ、本文が読まれず Google が返す
            # error.message (モデル名違い、キー無効 ...) が呼び出し側に届かない。
            # 同じ答えしか返らない状態は 1 回目で報告する。
            last_error = _upstream_error(e)
            if e.code not in RETRYABLE_STATUS:
                raise last_error from e
        except (OSError, http.client.HTTPException) as e:
            # OSError は URLError / TimeoutError を含み、加えて本文の読み取り中に
            # 出るソケット例外 (ConnectionResetError, ssl.SSLError など) も拾う。
            # urllib が URLError で包むのは接続フェーズの例外だけなので、
            # URLError を名指しするだけでは読み取り中の切断がリトライされずに
            # MCP の外へ抜ける (scripts/gemini_api.py も OSError で捕まえている)。
            # HTTPException は本文の途中で接続が切れた IncompleteRead など。
            # OSError 系ではないため、ここで名指ししないとリトライされず、
            # ツール側の except にも掛からずに MCP の外へ抜ける。
            last_error = e
        # 最終試行の失敗後は再試行しないので待つ意味がない
        if attempt < max_retries - 1:
            # 待つ前に見る: キャンセル済みなら、待つこと自体が無駄。
            if cancel is not None and cancel.is_set():
                raise GeminiCancelled("cancelled before backoff")
            time.sleep(2**attempt)

    raise last_error  # type: ignore[misc]


# -------------------------------------------------------------------
# 送信前の秘密スキャン
# -------------------------------------------------------------------
# 外部モデルへテキストを送る他の経路 (Bash フック、エディタの
# scripts/secret_scan.py) はどれも、送る前に共有の scan_secrets を通す。この
# サーバーだけ素通しだと、呼び出し側が貼った資格情報がそのまま Google へ渡り、
# 全文ログにも残る。
#
# 共有モジュールは realpath(__file__) から ../../hooks と辿って読む。
# ~/.claude/mcp-servers は repo の .claude/mcp-servers への symlink なので、
# abspath だと ~/.claude/hooks を指してしまい、そちらが在るかどうかという偶然に
# 結果が左右される (_bash_review_common.py の冒頭コメントと同じ理由)。
# sys.path は触らず、ファイルを名指しで読む: sys.modules のキャッシュに左右されず、
# MCP サーバーの import 解決へ副作用を持ち込まない。
_HOOKS_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)), os.pardir, os.pardir, "hooks"
    )
)
_COMMON_MODULE = "_bash_review_common"

_ScanFn = Callable[[str, object], Tuple[bool, str]]
_scan_secrets_fn: Optional[_ScanFn] = None

if TYPE_CHECKING:
    # 実行時には読まれない (上のとおりファイルを名指しで読む)。mypy にだけ実体を
    # 見せ、_ScanFn が scan_secrets の本物の署名と食い違ったら型検査で落とす。
    # scripts/secret_scan.py が .claude/hooks を mypy の対象に含めることで得ている
    # のと同じ解決を、動的に読む側でも失わないための束縛。
    from _bash_review_common import scan_secrets as _typed_scan_secrets

    _SCAN_CONTRACT: _ScanFn = _typed_scan_secrets


def _load_scan_secrets() -> _ScanFn:
    """共有モジュールの scan_secrets を返す。読めなければ例外を送出する。

    成功だけをキャッシュする。失敗を覚えると、一時的な失敗 (編集中のファイル
    など) でサーバーを再起動するまで全呼び出しが塞がる。並行して初回が走ると
    二重に読み込むが、冪等なので害はない。
    """
    global _scan_secrets_fn
    loaded = _scan_secrets_fn
    if loaded is None:
        path = os.path.join(_HOOKS_DIR, _COMMON_MODULE + ".py")
        spec = importlib.util.spec_from_file_location(_COMMON_MODULE, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded = module.scan_secrets
        _scan_secrets_fn = loaded
    return loaded


def _refuse_unsafe_send(tool: str, question: str) -> Optional[str]:
    """送ってはいけない入力なら、呼び出し側へ返すエラー文字列を返す。無ければ None。

    止めるのは 2 つ。資格情報が見つかった場合と、スキャン自体ができなかった場合
    (fail closed: 検証できない入力は「問題なし」ではない。codex-consultation の
    ゲートと同じ判断)。どちらも何も送らない。

    止めたテキストについては、事実と種別ラベル以外を一切どこにも残さない:
    返り値にもログにも通知にも、値はもちろん断片も、周辺の文面も載せない。
    ラベルは scan_secrets が返す種別名だけ (値は含まない契約)。スキャンできな
    かった場合の入力は秘密を含むかもしれないので、同じ扱いにする。
    """
    try:
        found, label = _load_scan_secrets()(question, {})
    except Exception as exc:
        # 読み込み失敗も scan_secrets の例外も戻り値の形の崩れも、「検証できない」
        # という同じ状態として扱う。例外の型名だけを記録し、メッセージは捨てる。
        detail = f"secret scan unavailable: {type(exc).__name__}"
        reply = (
            "Gemini request blocked: the secret scan is unavailable, so nothing "
            "was sent to Gemini (fail closed). Restore "
            ".claude/hooks/_bash_review_common.py and retry."
        )
    else:
        if not found:
            return None
        detail = f"credential detected: {label}"
        reply = (
            f"Gemini request blocked: a credential was detected in the input "
            f"({label}). Nothing was sent to Gemini. "
            "Remove the credential from the text and retry."
        )
    _log_quietly(tool, "BLOCK", "[NOT LOGGED - blocked by secret scan]", detail)
    notify("Gemini Consultant", "送信前の秘密スキャンで中止しました", 8)
    return reply


# -------------------------------------------------------------------
# ツール定義
# -------------------------------------------------------------------
# 2 つのツールは async def で、中身 (_*_sync) を asyncio.to_thread へ渡す。
# FastMCP は同期関数をイベントループ上で直接呼ぶ。Gemini 呼び出しは最悪
# 3 回 x 180 秒 + バックオフで 9 分を超え、その間サーバーはキャンセルも ping も
# 並行する別のツール呼び出しも処理できなくなる。止まる箇所は HTTP と
# time.sleep のバックオフだけでなく、ログ書き込みと notify (osascript /
# notify-send を最大 10 秒待つ) にもあるので、本体を丸ごとスレッドへ逃がす。
# 本体は従来のまま (戻り値の文字列も例外の腕も不変)。
#
# docstring は async 側に置く: FastMCP はツールの説明をそこから作る。
async def _run_off_loop(
    sync_tool: Callable[[str, threading.Event], str], question: str
) -> str:
    """本体をワーカースレッドで走らせる。待つ側がキャンセルされたら中止を伝える。

    to_thread をキャンセルしても、止まるのは await だけでスレッドは走り続ける
    (スレッドは強制終了できない)。何もしないと、キャンセル後も残りの再試行
    (最大 180 秒 x 3 とバックオフ) を打ち、ログと通知まで出す。そこで Event を
    渡し、call_gemini が試行の合間に見て自分から退く。
    """
    cancel = threading.Event()
    try:
        return await asyncio.to_thread(sync_tool, question, cancel)
    except asyncio.CancelledError:
        cancel.set()
        raise


@mcp.tool()
async def consult_gemini(question: str) -> str:
    """
    アーキテクチャ・設計判断・トレードオフについて Gemini Pro に深く相談する。
    影響範囲が広い・抽象度が高い判断に使う。
    実装を始める前に複雑な問題を整理したいときに使う。
    """
    return await _run_off_loop(_consult_gemini_sync, question)


def _consult_gemini_sync(question: str, cancel: threading.Event) -> str:
    # 入力を先に見る。プロンプトの組み立ても API キーの確認もその後。
    refusal = _refuse_unsafe_send("consult_gemini", question)
    if refusal is not None:
        return refusal

    prompt = (
        "以下のタスクについて、実装前に考慮すべき点を整理してください。\n"
        "コード実装の詳細ではなく、設計判断・落とし穴・代替案に焦点を当ててください。\n"
        "セキュリティ、パフォーマンス、エラー処理の観点もチェックしてください。\n"
        "不確かな情報や知識の範囲外の事項については、その旨を明示してください。\n"
        "推測で回答せず、確信が持てない場合は「要確認」として提示してください。\n"
        f"\nタスク: {question}"
    )

    try:
        result = call_gemini(prompt, model=DEEP_MODEL, cancel=cancel)
        _log_quietly("consult_gemini", "OK", prompt, result)
        notify("Gemini Consultant", f"設計相談完了: {question[:40]}", 4)
        return result
    except GeminiCancelled as e:
        # 待っている側は既にいない (戻り値は捨てられる)。通知は出さず、事実だけ記録する。
        _log_quietly("consult_gemini", "CANCEL", prompt, str(e))
        return "Gemini request cancelled by the caller; no further attempt was made."
    except GeminiKeyError as e:
        _log_quietly("consult_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIキーの問題", 8)
        return f"Gemini API error: {e}"
    # JSONDecodeError は ValueError のサブクラスなので、この腕は必ず下の
    # `except ValueError` より前に置くこと。順序を入れ替えると、ここが死んで
    # JSON パース失敗が「予期しないエラー」に化ける。
    except (
        GeminiUpstreamError,
        OSError,  # URLError / TimeoutError / 読み取り中のソケット例外を含む
        http.client.HTTPException,
        json.JSONDecodeError,
        IndexError,
        KeyError,
    ) as e:
        _log_quietly("consult_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIエラーが発生しました", 10)
        return f"Gemini API error: {e}"
    except ValueError as e:
        # 想定外の ValueError。メッセージを外へ出さないのが要点: http.client の
        # `Invalid header value %r` のように、値そのものを埋め込む例外が
        # この経路に来うる。型名だけを記録し、本文は捨てる。
        _log_quietly(
            "consult_gemini", "ERROR", prompt, f"unexpected {type(e).__name__}"
        )
        notify("Gemini Consultant", "APIエラーが発生しました", 10)
        return (
            "Gemini API error: unexpected "
            f"{type(e).__name__} (details withheld to avoid leaking credentials)"
        )


@mcp.tool()
async def review_gemini(question: str) -> str:
    """
    実装方針や具体的な抜け漏れを Gemini Flash で素早く確認する。
    局所的・繰り返し頻度が高いチェックに使う。
    """
    return await _run_off_loop(_review_gemini_sync, question)


def _review_gemini_sync(question: str, cancel: threading.Event) -> str:
    # 入力を先に見る (理由は _consult_gemini_sync を参照)。
    refusal = _refuse_unsafe_send("review_gemini", question)
    if refusal is not None:
        return refusal

    prompt = (
        "以下の実装方針について、抜け漏れや問題点を確認してください。\n"
        "具体的な実装の観点から、見落としやすい点・注意点を指摘してください。\n"
        "セキュリティ、パフォーマンス、エラー処理の観点もチェックしてください。\n"
        "不確かな情報や知識の範囲外の事項については、その旨を明示してください。\n"
        "推測で回答せず、確信が持てない場合は「要確認」として提示してください。\n"
        f"\n確認対象: {question}"
    )

    try:
        result = call_gemini(prompt, model=LIGHT_MODEL, cancel=cancel)
        _log_quietly("review_gemini", "OK", prompt, result)
        notify("Gemini Consultant", f"レビュー完了: {question[:40]}", 4)
        return result
    except GeminiCancelled as e:
        # 理由は consult_gemini の同じ腕を参照。
        _log_quietly("review_gemini", "CANCEL", prompt, str(e))
        return "Gemini request cancelled by the caller; no further attempt was made."
    except GeminiKeyError as e:
        _log_quietly("review_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIキーの問題", 8)
        return f"Gemini API error: {e}"
    # 例外の順序の理由は consult_gemini の同じ腕を参照。
    except (
        GeminiUpstreamError,
        OSError,  # URLError / TimeoutError / 読み取り中のソケット例外を含む
        http.client.HTTPException,
        json.JSONDecodeError,
        IndexError,
        KeyError,
    ) as e:
        _log_quietly("review_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIエラーが発生しました", 10)
        return f"Gemini API error: {e}"
    except ValueError as e:
        # 想定外の ValueError の扱いは consult_gemini の同じ腕を参照。
        _log_quietly("review_gemini", "ERROR", prompt, f"unexpected {type(e).__name__}")
        notify("Gemini Consultant", "APIエラーが発生しました", 10)
        return (
            "Gemini API error: unexpected "
            f"{type(e).__name__} (details withheld to avoid leaking credentials)"
        )


if __name__ == "__main__":
    mcp.run()
