# ~/.codex/hooks/bash-review.py
# 判定の 3 層構造 (静的 DENY / 高リスク層の二モデル AND ゲート / 低リスク層の
# Gemini 一次 + Codex 二次) は _bash_review_common.py のヘッダー参照。
# Codex は permissionDecision の allow/deny は解釈するが ask は未サポートで、
# ask を返すとフックがエラー扱いになり fail-open する (実測 Codex 0.144.5:
# "PreToolUse hook returned unsupported permissionDecision:ask")。Claude の ask
# 相当 (人間への確認停止) を安全に表現する手段が Codex には無いため、許可は
# exit 0 (無出力)、ブロック (内部判定が ask/deny いずれでも) は exit 2 + stderr で
# 伝える (一律 fail-closed)。deny JSON は権威的に効く (Codex は迂回しない) が、
# 内部 ask 判定まで deny に潰すと意味が変わるうえ利点も乏しいため採用しない。
#
# Gemini/Codex の呼び出し (プロンプト生成・実行・パース) は _bash_review_common.py
# に集約している。一方、判定の組み合わせ木 (Gemini×Codex のどちらが
# allow/block になるか、理由文・detail log・summary・通知) はこの入口自身にも
# あり、claude 変種 (.claude/hooks/bash-review.py) 側の木を意図的にミラーした
# 複製である。変更するときは両方の入口を揃えて直すこと。共有分岐の理由コメントは
# .claude 側を正とし、こちらは参照と Codex 固有の注記に留める。
# 注釈の遅延評価は Python 3.9 で import を通すため (下の `decided_exit: int | None`
# はモジュール直下の注釈なので、これが無いと 3.9 では実行時に評価されて落ちる)。
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import stat

# 要約の子プロセスは固定の argv で起動する (shell は使わない)。
import subprocess  # nosec B404
import sys
import time

# 共有モジュールの実体は .claude/hooks/_bash_review_common.py 一本 (共有方法と
# realpath を使う理由は同ファイルのヘッダーコメント参照)。以下はその参照側。
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "..", "..", ".claude", "hooks"
    ),
)
from _bash_review_common import (
    DEFAULT_GEMINI_FALLBACK_MODEL,
    DEFAULT_GEMINI_MODEL,
    HARNESS_CWD_UNKNOWN,
    _can_skip_review,
    _sanitize_notify,
    _split_commands,
    build_review_prompt,
    classify_high_risk,
    codex_flag_ask_reason,
    combine_high_risk_verdicts,
    describe_exception,
    find_deny_command,
    format_dual_verdict_reason,
    harness_write_ask_reason,
    input_limit_reason,
    note_detail_log_disabled,
    notify,
    prepare_detail_log_dir,
    scan_secrets,
)
from _bash_review_common import run_codex_review as _run_codex_review  # noqa: E402
from _bash_review_common import run_gemini_review as _run_gemini_review  # noqa: E402
from _bash_review_common import (  # noqa: E402
    run_parallel_reviews as _run_parallel_reviews,
)

# ログの書き込みは失敗しても判定を変えない版 (_bash_review_common.py の try_* の
# コメント参照)。
from _bash_review_common import try_log_summary as _log_summary  # noqa: E402
from _bash_review_common import try_write_detail_log as _write_detail_log  # noqa: E402

# サンドボックス CLI (csb) の分類 (common ではなくこの入口から読む理由は .claude 側参照)。
from _sandbox_gate import (  # noqa: E402
    CSB_LINK,
    LAUNCH_ENV_DROP,
    CsbCommand,
    classify_sandbox_command,
    host_syntax_ok,
    parse_csb,
    parse_csb_wrapper,
    render_preview,
    running_python_problem,
    state_dir,
    trusted_path,
)

# 環境変数由来の設定 (stdin に依存しないので try の外で読む)
# 鍵は前後の空白を落とす (.claude 側の入口と同じ理由: CRLF の秘密ファイル由来の \r)。
api_key = os.environ.get("GEMINI_API_KEY", "").strip()
gemini_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
gemini_fallback_model = os.environ.get(
    "GEMINI_FLASH_MODEL", DEFAULT_GEMINI_FALLBACK_MODEL
)


# 判定確定後にログ/通知が例外を投げても、下の except の fail-safe
# (block/exit 2) で確定済みの結果を上書きさせないための状態。ブロック系は
# emit_block を通るのでここで 2 を確定し、許可系は各分岐で 0 を確定する。
decided_exit: int | None = None


# ブロック理由に必ず添えるエージェント向けの指示。Codex は exit 2 のブロックを
# 「忠告フィードバック」として扱い、ブロックされた元コマンドは実行しない代わりに、
# より単純な別コマンドへ自律的に置き換えて目的達成を図りがち (各コマンドは個別に
# 再審査されるので実行される変種自体は正当だが、Gemini が DENY で示した『意図』は
# 人間の目に触れないまま進む)。Codex には Claude の ask 相当 (人間への確認停止) が
# 無いため、せめて理由文で「回避を試みず人間に報告せよ」と明示して自律リトライを
# 抑制する。
_AGENT_BLOCK_DIRECTIVE = (
    " — BLOCKED by bash-review policy. Report this to the user; do NOT retry with "
    "alternative or simplified commands to work around it."
)


def emit_block(reason: str) -> None:
    """ブロック理由 (+ エージェント向け指示) を stderr に出力する (exit 2 と組で使う)"""
    global decided_exit
    decided_exit = 2
    print(reason + _AGENT_BLOCK_DIRECTIVE, file=sys.stderr)


# この実行の詳細ログを書けたか。書けたときだけサマリーの reason に
# ` detail=<basename>` を添える (理由は .claude/hooks/bash-review.py の同箇所参照)。
detail_written = False


def detail_ref() -> str:
    if not detail_written or log_file is None:
        return ""
    return f" detail={os.path.basename(log_file)}"


# 共有モジュールの関数を、この入口のモジュールグローバルを閉じ込めた薄い
# ラッパーで包む (理由は .claude/hooks/bash-review.py の同箇所参照)。
def log_summary(
    decision: str, stage: str, reason: str, *, redact_command: bool = False
) -> None:
    _log_summary(
        summary_log,
        command,
        decision,
        stage,
        reason + detail_ref(),
        redact_command=redact_command,
    )


def write_detail_log(entries: dict, *, redact_input: bool = False) -> None:
    global detail_written
    detail_written = _write_detail_log(
        log_file, tool_name, tool_input, entries, redact_input=redact_input
    )


# --- サンドボックス CLI (csb) ---
# 以下は .claude/hooks/bash-review.py の同名の定義のミラー (理由のコメントは向こう)。
# Codex の入口から見た csb の実体は、共有モジュールを探すのと同じ場所 (上の
# sys.path.insert) の claude_sandbox.py。
HOOKS_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "..", "..", ".claude", "hooks"
    )
)
# >>> shared: sandbox-setup (両入口で同一。tests/test_bash_review_sandbox.py が固定する)
SANDBOX_CLI = os.path.join(HOOKS_DIR, "claude_sandbox.py")
# 人の確認が要る境界の操作。
SANDBOX_BOUNDARIES = frozenset({"create", "net on", "apply", "image build"})
# サンドボックスの段 (実体の確認と要約の子プロセス) 全体にかける時間の上限 (秒)。
SANDBOX_BUDGET = 5.0
# 時間切れの子プロセスを止めるときの、1 段 (SIGTERM / SIGKILL) ごとの待ち (秒)。
# 2 段ぶんを子プロセスの待ちから先に差し引き、段全体を SANDBOX_BUDGET に収める。
SANDBOX_STOP_GRACE = 0.5
# 失敗時の確認の文面の上限 (文字)。要約は render_preview が自分の枠で描く。
SANDBOX_TEXT_LIMIT = 4000
# ~/.local/bin/csb として読む上限 (ラッパーは 3 行で、これより長ければ別物)。
SANDBOX_WRAPPER_MAX = 4096
SANDBOX_ERROR_REASON = "csb の判定中にエラーが起きたため確認が必要"
SANDBOX_IDENTITY_DENY = (
    "csb の実体を確かめられない (~/.local/bin/csb が install.sh の書いた固定のラッパーで"
    "ない、PATH の csb と違う、など) ため、境界の操作 (create / net on / apply / image"
    " build) は実行"
    "しない。install.sh で csb を入れ直してから実行し直す"
)
SANDBOX_IDENTITY_ASK = (
    "csb の実体を確かめられない (~/.local/bin/csb が install.sh の書いた固定のラッパーで"
    "ない、PATH の csb と違う、など) ため、静的には許可しない。~/.local/bin/csb の中身を"
    "確かめてから承認する"
)
# 要約の子プロセス。このフックと同じインタプリタ (sys.executable) を隔離モード (-I:
# PYTHON* の環境変数・user site・cwd を sys.path に入れない) と -S (site を読まない) で
# 起動し、LAUNCH_ENV_DROP を外した環境で、この入口の claude_sandbox.py を import して
# preview_main を呼ぶ。
# コマンドは argv ではなく stdin の JSON で渡す (ps に出さない)。
_PREVIEW_DRIVER = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "import claude_sandbox; sys.exit(claude_sandbox.preview_main())"
)


def session_cwd(payload: object) -> str | None:
    """ハーネスの判定とサンドボックスの段が共有する、セッションの作業ディレクトリ。

    payload の cwd が NUL を含まない絶対パスの文字列ならそれ。そうでなければフック自身の
    cwd (Claude Code はフックをセッションの cwd で起動する。Codex では確かめていない)。
    どちらも使えなければ None。入口はそのとき HARNESS_CWD_UNKNOWN をハーネスの判定に
    渡す (cwd=None は字面だけの判定で、変数・cd・名前付きディレクトリを通る書き込みを
    見逃すため)。相対の書き込みは確認へ回り、csb の実体は確かめない (絶対パスでない)。
    """
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if isinstance(cwd, str) and "\x00" not in cwd and os.path.isabs(cwd):
        return cwd
    try:
        return os.getcwd()
    except OSError:
        return None


# <<< shared: sandbox-setup


def review_cwd_of(payload: object) -> str:
    """コマンドが実際に走るディレクトリ。Codex のシェルツールは tool_input.workdir で走る。

    - workdir が無い (キーが無いか null): session_cwd のまま (None なら HARNESS_CWD_UNKNOWN)
    - NUL も `~` も含まない絶対パスの文字列: それ
    - 相対パスの文字列: session_cwd と join する (session_cwd が None なら分からない)
    - それ以外 (`~` を含む・空・NUL・文字列でない): HARNESS_CWD_UNKNOWN。ハーネスの
      判定は相対の書き込みを確認へ回し、csb の実体は確かめない (絶対パスでないため)
    `~` は展開しない: Codex がどう解くか (展開するか、文字どおりの名前か) を決めて
    いないので、推測して当てはめない。
    """
    base = session_cwd(payload)
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    workdir = tool_input.get("workdir") if isinstance(tool_input, dict) else None
    if workdir is None:
        return HARNESS_CWD_UNKNOWN if base is None else base
    if (
        not isinstance(workdir, str)
        or not workdir
        or "\x00" in workdir
        or "~" in workdir
    ):
        return HARNESS_CWD_UNKNOWN
    if os.path.isabs(workdir):
        return workdir
    if base is None:
        return HARNESS_CWD_UNKNOWN
    return os.path.join(base, workdir)


# >>> shared: sandbox-helpers (両入口で同一。tests/test_bash_review_sandbox.py が固定する)
def csb_is_ours(cwd: object) -> bool:
    """~/.local/bin/csb が、この入口の claude_sandbox.py を起動する固定のラッパーか。

    確かめること (どれか欠けたら False。例外も False):
    - セッションの cwd が分かっている (session_cwd が None なら確かめない)
    - ~ から ~/.local/bin/csb までが trusted_path を満たす (owners は自分、top は ~):
      通るディレクトリは自分の所有で、group / other が書けない。trusted_path は、自分の
      所有で宛先が ~ の下の symlink なら辿って続ける (宛先が ~ の外なら通らない)
    - ~/.local と ~/.local/bin が symlink でない本物のディレクトリ (lstat)。symlink
      (`~/.local/bin -> ~/repo/bin`) だと、ラッパーの実体は `.local/bin` の綴りを
      含まないパスにあり、そこへの書き込みはハーネスの照合が確認に回さないため
    - ~/.local/bin/csb が symlink でない通常ファイルで、自分の所有で、group / other が
      書けない (O_NOFOLLOW で開き、開いたものを fstat で見る)
    - バイト列が csb_wrapper_text(PY, ~) と完全に一致する (_sandbox_gate)
    - PY が信頼できるシステムの python3 で、このフックを動かしているインタプリタ
      (sys.executable) と同じパス (running_python_problem。realpath では比べない:
      別の venv の bin/python は同じ実体を指しても別の pyvenv.cfg と site-packages を読む)
    - ~/.claude/hooks/claude_sandbox.py の realpath がこの入口の claude_sandbox.py と同じ
    - フックの PATH の csb (which) が ~/.local/bin/csb と同じファイル (samefile)
    """
    try:
        if not (isinstance(cwd, str) and os.path.isabs(cwd)):
            return False
        home = os.path.normpath(os.path.expanduser("~"))
        link = os.path.join(home, *CSB_LINK)
        if trusted_path(link, owners=(os.geteuid(),), top=home):
            return False
        parent = home
        for part in CSB_LINK[:-1]:
            parent = os.path.join(parent, part)
            if not stat.S_ISDIR(os.lstat(parent).st_mode):
                return False
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(os.open(link, flags), "rb") as fh:
            st = os.fstat(fh.fileno())
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.geteuid()
                or st.st_mode & 0o022
            ):
                return False
            data = fh.read(SANDBOX_WRAPPER_MAX + 1)
        python = parse_csb_wrapper(data, home)
        if python is None or running_python_problem(
            python, sys.executable, sys.version_info
        ):
            return False
        cli = os.path.join(home, ".claude", "hooks", "claude_sandbox.py")
        if os.path.realpath(cli) != os.path.realpath(SANDBOX_CLI):
            return False
        found = shutil.which("csb")
        return found is not None and os.path.samefile(found, link)
    except Exception:  # noqa: BLE001  確かめられない = 満たさない
        return False


def _stop_preview(proc: subprocess.Popen) -> None:
    """要約の子プロセスをプロセスグループごと止める (git の孫プロセスも残さない)。

    先に SIGTERM、終わらなければ SIGKILL。待ちは 1 段ごとに SANDBOX_STOP_GRACE 秒で、
    子プロセスが答えなくてもここで止まり続けない。
    """
    for sig, fallback in (
        (signal.SIGTERM, proc.terminate),
        (signal.SIGKILL, proc.kill),
    ):
        pid = proc.pid
        try:
            # pid 0 / 1 / 負の値を killpg に渡すと自分や全体のグループに届く。
            if not (isinstance(pid, int) and pid > 1):
                raise ProcessLookupError
            os.killpg(pid, sig)
        except OSError:
            fallback()
        try:
            proc.communicate(timeout=SANDBOX_STOP_GRACE)
            return
        except subprocess.TimeoutExpired:
            pass


def sandbox_preview(
    cmd: CsbCommand, command: str, cwd: object, deadline: float
) -> tuple[dict | None, str]:
    """create / apply / image build の確認の data (claude_sandbox.preview_main の JSON)。

    (data, "") か (None, 失敗の語)。子プロセスは deadline で打ち切り、グループごと
    止める。失敗の語は固定の語彙だけ: 子プロセスの出力や理由は文面へ持ち込まない。
    呼ぶのは csb の実体を確かめた後だけ (差し替わったコードを import しないため)。
    """
    cwd_ok = isinstance(cwd, str) and os.path.isabs(cwd)
    if cmd.sub == "create" and not cwd_ok:
        return None, "作業ディレクトリ不明"
    request = {
        "argv": shlex.split(command)[1:],
        "cwd": cwd if cwd_ok else "/",
        "state": state_dir(),
    }
    proc = subprocess.Popen(  # nosec B603
        [sys.executable, "-I", "-S", "-c", _PREVIEW_DRIVER, HOOKS_DIR],
        env={k: v for k, v in os.environ.items() if k not in LAUNCH_ENV_DROP},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=HOOKS_DIR,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(
            json.dumps(request).encode("utf-8"),
            timeout=max(0.1, deadline - time.monotonic() - 2 * SANDBOX_STOP_GRACE),
        )
    except subprocess.TimeoutExpired:
        _stop_preview(proc)
        return None, "時間切れ"
    except BaseException:
        _stop_preview(proc)
        raise
    if proc.returncode == 2:
        return None, "拒否"
    if proc.returncode != 0:
        return None, "エラー"
    try:
        data = json.loads(out.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError も ValueError
        return None, "エラー"
    return (data, "") if isinstance(data, dict) else (None, "エラー")


def _cap(text: str) -> str:
    if len(text) <= SANDBOX_TEXT_LIMIT:
        return text
    return text[: SANDBOX_TEXT_LIMIT - 1] + "…"


def sandbox_boundary_text(
    base: str,
    cmd: CsbCommand,
    command: str,
    cwd: object,
    secret: bool,
    deadline: float,
) -> tuple[str, str, str]:
    """(確認の文面, 承認させない理由, 要約の状態)。実体を確かめた正規形の境界にだけ使う。

    create / apply / image build には、子プロセスの data を render_preview (_sandbox_gate) で描いた
    要約を付ける。判定に効く項目が表示枠に収まらなければ承認させない (理由を返す)。
    秘密を検出したコマンドでは要約を作らない (create の要約には --setup の値が載る)。
    要約を作れなければ固定の語だけを足して確認のまま返す。状態は "" (要約の対象外)
    / "ok" / "表示枠超過" / 失敗の語。
    """
    if cmd.sub not in ("create", "apply", "image build"):
        return _cap(base), "", ""
    try:
        if secret:
            data, failure = None, "秘密を検出"
        else:
            data, failure = sandbox_preview(cmd, command, cwd, deadline)
        if data is not None:
            text, refusal = render_preview(data, base)
            if refusal:
                return "", refusal, "表示枠超過"
            return text, "", "ok"
    except Exception:  # noqa: BLE001  要約が作れないだけで判定 (確認) は変えない
        failure = "エラー"
    return _cap(f"{base}（要約を作れなかった: {failure}）"), "", failure


# <<< shared: sandbox-helpers


def run_gemini_review() -> tuple[str, str]:
    return _run_gemini_review(prompt, api_key, gemini_model, gemini_fallback_model)


def run_codex_review(gemini_verdict: str, gemini_output: str) -> tuple[str, str]:
    return _run_codex_review(gemini_verdict, gemini_output, tool_name, tool_input)


def run_parallel_reviews() -> tuple[tuple[str, str], tuple[str, str]]:
    return _run_parallel_reviews(
        prompt, api_key, gemini_model, gemini_fallback_model, tool_name, tool_input
    )


# -------------------------------------------------------------------
# メインフロー
# stdin の破損 (空 / 非JSON / tool_input が非dict) や予期しない例外でも
# 素通りさせず、必ずブロック (exit 2) に倒してユーザー確認へ回す。
# -------------------------------------------------------------------
try:
    hook_input = json.loads(sys.stdin.buffer.read())
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # ログ設定
    # ログの準備と書き込みの失敗は判定に影響させず、この実行の詳細ログを止めるだけに
    # する (理由は .claude/hooks/bash-review.py の同箇所参照)。以前は例外が catch-all に
    # 落ち、`ls` を含む全コマンドが exit 2 のブロックになっていた。

    # サマリーログ (1ファイルに追記・500行でローテーション)
    summary_log = os.path.expanduser("~/.codex/logs/bash-review.log")

    # 詳細ログ (コマンドごとに1ファイル)
    log_dir = "/tmp/codex_hooks/logs/PreToolUse/Bash/bash-review"  # nosec B108
    # root (/tmp/codex_hooks) から下の各階層を検査する。通らなければ詳細ログは止める。
    detail_log_problem = prepare_detail_log_dir(
        log_dir,
        root="/tmp/codex_hooks",  # nosec B108
    )
    # ナノ秒 + PID でファイル名を一意化する (理由は .claude/hooks/bash-review.py 参照)。
    log_file = (
        None
        if detail_log_problem
        else os.path.join(log_dir, f"bash_cmd_{time.time_ns()}_{os.getpid()}.log")
    )
    if detail_log_problem:
        # 止めたことをサマリーに 1 行残す (理由は .claude/hooks/bash-review.py 参照)。
        note_detail_log_disabled(summary_log, detail_log_problem)

    # --- 入力サイズの上限 (何よりも先に。理由は .claude/hooks/bash-review.py 参照) ---
    # Codex には ask 相当が無いので、結果は exit 2 のブロックとして返す (秘密検出と同じ
    # 規約: 内部判定は ASK として記録し、ブロックで人間へ回す)。
    limit_reason = input_limit_reason(command, tool_input)
    if limit_reason:
        omitted = "[input not logged: over the review size limit]"
        reason = (
            f"{limit_reason}; not analysed or sent to external review — "
            "confirm manually"
        )
        emit_block(reason)
        detail_written = _write_detail_log(
            log_file, tool_name, {"command": omitted}, {"Result": "ASK (input limit)"}
        )
        _log_summary(summary_log, omitted, "ASK", "size", reason + detail_ref())
        notify("Bash Review - 確認が必要", "入力が大きすぎるため静的解析を省略", 8)
        sys.exit(2)

    # 一次処理: Gemini API 用プロンプト (tool_input に依存するので try 内で組む)
    prompt = build_review_prompt(tool_name, tool_input)

    sub_commands = _split_commands(command)

    # --- 秘密スキャン (静的解析) ---
    # 判定 (deny/safe-skip/secret-block) の優先順位は変えず、結果は redact の
    # スイッチとして先に使う (理由は .claude/hooks/bash-review.py の同箇所参照)。
    secret_found, secret_label = scan_secrets(command, tool_input)

    # --- 危険コマンドの即時拒否 (改行分割は find_deny_command が捌く) ---
    # command も渡す理由は .claude 側の同じ行のコメントを参照。
    matched, deny_name = find_deny_command(sub_commands, command)
    if matched:
        reason = f"Blocked dangerous command: '{deny_name}'"
        emit_block(reason)
        write_detail_log({"Result": "DENY (pre-blocked)"}, redact_input=secret_found)
        log_summary("DENY", "pre", reason, redact_command=secret_found)
        notify("Bash Review - 拒否", f"危険コマンド検出: {deny_name}", 8)
        sys.exit(2)

    # --- codex の権限を広げるフラグは静的にブロック (.claude 側では ask) ---
    # 秘密を検出済みなら理由文を固定文にする (stderr はエージェントが読む)。
    codex_reason = codex_flag_ask_reason(sub_commands, command, redact=secret_found)
    if codex_reason:
        emit_block(codex_reason)
        write_detail_log({"Result": "ASK (codex flag)"}, redact_input=secret_found)
        log_summary("ASK", "pre", codex_reason, redact_command=secret_found)
        notify("Bash Review - 確認が必要", "codex の権限を広げるフラグを検出", 8)
        sys.exit(2)

    # --- コマンドが走るディレクトリ (workdir があればそれ。review_cwd_of) ---
    # ハーネスの判定とサンドボックスの段が同じ値を使う (理由は .claude 側参照)。
    review_cwd = review_cwd_of(hook_input)

    # --- ハーネス自身の設定への書き込みは静的にブロック (.claude 側では ask) ---
    harness_reason = harness_write_ask_reason(sub_commands, command, cwd=review_cwd)
    if harness_reason:
        emit_block(harness_reason)
        write_detail_log({"Result": "ASK (harness write)"}, redact_input=secret_found)
        log_summary("ASK", "pre", harness_reason, redact_command=secret_found)
        notify("Bash Review - 確認が必要", "ハーネス設定への書き込みを検出", 8)
        sys.exit(2)

    # >>> shared: sandbox-stage (両入口で同一。違ってよいのは emit と終了コードの 3 行だけ。
    # tests/test_bash_review_sandbox.py が固定する)
    # --- サンドボックス CLI (csb) ---
    # 分類は classify_sandbox_command (_sandbox_gate.py)。モデルの判定で覆らないよう、
    # safe-skip と LLM 呼び出しより前に決める。正規形 (構文が単純で parse_csb できる
    # もの) は、まず csb の実体を確かめる (csb_is_ours)。
    # - 実体を確かめられない: 境界 (create / net on / apply / image build) は固定文で
    #   拒否、それ以外の正規形は固定文で確認。要約の子プロセスは起動しない (差し替わった
    #   コードを import しない)。通常のレビューには落とさない。
    # - 実体が確かな境界: 確認。create / apply / image build には CLI と同じ材料の要約を
    #   付け、判定に効く項目が表示枠に収まらなければ拒否する。秘密を検出していても確認
    #   のまま。
    # - 実体が確かな正規形の allow: 秘密が無ければ許可。秘密があれば下の流れへ。
    # - 正規形でない ask (迂回形): 確認 (要約なし)。分類の例外も確認に倒す。
    sandbox_deadline = time.monotonic() + SANDBOX_BUDGET
    sandbox_cmd: CsbCommand | None = None
    try:
        sandbox_decision, sandbox_reason = classify_sandbox_command(command)
        if sandbox_decision and host_syntax_ok(command):
            sandbox_cmd = parse_csb(command)
    except Exception:  # noqa: BLE001  判定できない csb は人へ倒す
        sandbox_decision, sandbox_reason = "ask", SANDBOX_ERROR_REASON
        sandbox_cmd = None
    sandbox_cwd = review_cwd
    sandbox_verdict, sandbox_text, sandbox_log = "", "", sandbox_reason
    if sandbox_cmd is not None and not csb_is_ours(sandbox_cwd):
        if sandbox_cmd.sub in SANDBOX_BOUNDARIES:
            sandbox_verdict, sandbox_text = "deny", SANDBOX_IDENTITY_DENY
        else:
            sandbox_verdict, sandbox_text = "ask", SANDBOX_IDENTITY_ASK
        sandbox_log = "csb の実体を確かめられない"
    elif sandbox_cmd is not None and sandbox_decision == "allow":
        if not secret_found:
            sandbox_verdict, sandbox_text = "allow", sandbox_reason
    elif sandbox_cmd is not None and sandbox_decision == "ask":
        sandbox_text, refusal, summary_state = sandbox_boundary_text(
            sandbox_reason,
            sandbox_cmd,
            command,
            sandbox_cwd,
            secret_found,
            sandbox_deadline,
        )
        sandbox_verdict = "deny" if refusal else "ask"
        sandbox_text = refusal or sandbox_text
        if summary_state:
            sandbox_log = f"{sandbox_reason} (summary={summary_state})"
    elif sandbox_decision == "ask":
        sandbox_verdict, sandbox_text = "ask", _cap(sandbox_reason)
    if sandbox_verdict in ("ask", "deny"):
        emit_block(sandbox_text)
        write_detail_log(
            {"Result": f"{sandbox_verdict.upper()} (sandbox)"},
            redact_input=secret_found,
        )
        log_summary(
            sandbox_verdict.upper(),
            "sandbox",
            sandbox_log,
            redact_command=secret_found,
        )
        if sandbox_verdict == "deny":
            notify("Bash Review - 拒否", "csb の境界の操作を止めた", 8)
        else:
            notify("Bash Review - 確認が必要", "サンドボックス CLI (csb) の確認", 8)
        sys.exit(2)
    if sandbox_verdict == "allow":
        decided_exit = 0
        write_detail_log({"Result": "ALLOW (sandbox)"})
        log_summary("ALLOW", "sandbox", sandbox_reason)
        sys.exit(0)
    # <<< shared: sandbox-stage

    # --- 安全コマンドのスキップ ---
    if sub_commands and all(_can_skip_review(c) for c in sub_commands):
        decided_exit = 0
        write_detail_log({"Result": "SKIP (safe command)"}, redact_input=secret_found)
        log_summary("ALLOW", "pre", "safe command", redact_command=secret_found)
        sys.exit(0)

    # --- 秘密の外部送信ガード (LLM 呼び出し前) ---
    # deny でも safe-skip でもない (=これから LLM へ送られる) コマンドに生の資格
    # 情報が載っていれば、Gemini/Codex を一切呼ばずブロック (exit 2) する (外部
    # 送信の防止)。stderr・通知・ログには一致した値を出さず種別ラベルのみ。
    if secret_found:
        emit_block(
            f"Command appears to contain a credential ({secret_label}); "
            "not sending to external review — confirm manually"
        )
        write_detail_log(
            {"Result": f"ASK (secret pre-scan: {secret_label})"}, redact_input=True
        )
        log_summary(
            "ASK", "secret", f"credential detected: {secret_label}", redact_command=True
        )
        notify(
            "Bash Review - 確認が必要", "認証情報を検出したため外部レビューを回避", 8
        )
        sys.exit(2)

    short_cmd = command[:60] + "..." if len(command) > 60 else command
    review_started = time.monotonic()

    # --- 高リスクコマンドの並列二重レビュー (両モデル ALLOW 一致時のみ自動実行) ---
    risk_label = classify_high_risk(sub_commands, command)
    if risk_label:
        (gemini_verdict, gemini_output), (codex_verdict, codex_output) = (
            run_parallel_reviews()
        )
        elapsed = time.monotonic() - review_started
        decision = combine_high_risk_verdicts(gemini_verdict, codex_verdict)
        reason = format_dual_verdict_reason(
            risk_label, gemini_verdict, gemini_output, codex_verdict, codex_output
        )
        # 両モデル ALLOW 一致のみ許可 (exit 0)。それ以外 (ask/deny) は
        # Codex ではブロック (exit 2) で理由を stderr に返す。
        # 判定はログより先に確定する (他の分岐・Claude 側と同じ順序)。ログが例外を
        # 投げても、allow を catch-all の exit 2 に、ブロックの理由を "hook error" に
        # 差し替えないため。
        if decision == "allow":
            decided_exit = 0
        else:
            emit_block(reason)
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": f"{decision.upper()} (high-risk: {risk_label})",
                "Elapsed": f"{elapsed:.1f}s",
            }
        )
        log_summary(
            decision.upper(),
            "highrisk",
            f"risk={risk_label}, gemini={gemini_verdict}, "
            f"codex={codex_verdict}, took={elapsed:.1f}s",
        )
        if decision == "allow":
            notify("Bash Review", f"高リスク許可 (両モデル承認): {short_cmd}", 4)
            sys.exit(0)
        if decision == "deny":
            notify("Bash Review - 拒否", f"{short_cmd}", 8)
        else:
            notify("Bash Review - 確認が必要", f"高リスク: {short_cmd}", 8)
        sys.exit(2)

    gemini_verdict, gemini_output = run_gemini_review()

    # Gemini が ALLOW と判定した場合はそのまま許可
    if gemini_verdict == "ALLOW":
        decided_exit = 0
        write_detail_log({"Gemini": gemini_output, "Result": "ALLOW (gemini)"})
        log_summary(
            "ALLOW",
            "gemini",
            f"approved by Gemini, took={time.monotonic() - review_started:.1f}s",
        )
        notify("Bash Review", f"許可: {short_cmd}", 4)
        sys.exit(0)

    # Gemini が疑わしい (ASK/DENY/ERROR) 場合は Codex に二次確認
    codex_verdict, codex_output = run_codex_review(gemini_verdict, gemini_output)
    elapsed = time.monotonic() - review_started

    if codex_verdict == "ALLOW" and gemini_verdict == "DENY":
        # 上書きしない理由は .claude/hooks/bash-review.py の同分岐参照。
        # Codex には ask 相当が無いため、結果は exit 2 のブロックとして返す。
        emit_block(
            f"Gemini=DENY: {_sanitize_notify(gemini_output.strip(), limit=160)} "
            f"but Codex approved: {_sanitize_notify(codex_output.strip(), limit=160)}"
            " — confirm manually"
        )
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": "ASK (gemini DENY vs codex ALLOW, override disabled)",
            }
        )
        log_summary(
            "ASK",
            "codex",
            f"gemini=DENY, codex=ALLOW (override disabled), took={elapsed:.1f}s",
        )
        notify("Bash Review - 確認が必要", f"判定不一致: {short_cmd}", 8)
        sys.exit(2)

    elif codex_verdict == "ALLOW" and gemini_verdict == "ASK":
        # 解消しない理由は .claude/hooks/bash-review.py の同分岐参照。
        # Codex には ask 相当が無いため、結果は exit 2 のブロックとして返す。
        emit_block(
            f"Gemini=ASK (confirmation requested) but Codex approved: "
            f"{_sanitize_notify(codex_output.strip(), limit=160)} — confirm manually"
        )
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": "ASK (gemini ASK vs codex ALLOW, override disabled)",
            }
        )
        log_summary(
            "ASK",
            "codex",
            f"gemini=ASK, codex=ALLOW (override disabled), took={elapsed:.1f}s",
        )
        notify("Bash Review - 確認が必要", f"要確認: {short_cmd}", 8)
        sys.exit(2)

    elif codex_verdict == "ALLOW":
        # gemini==ERROR (無意見) のみここに来る。単独 Codex ALLOW で解消してよい理由は
        # .claude/hooks/bash-review.py の同分岐参照。
        decided_exit = 0
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": f"ALLOW (codex resolves gemini {gemini_verdict})",
            }
        )
        log_summary(
            "ALLOW",
            "codex",
            f"gemini={gemini_verdict}, codex=ALLOW, took={elapsed:.1f}s",
        )
        notify("Bash Review", f"Codex 承認: {short_cmd}", 4)

    elif codex_verdict == "ASK":
        emit_block(
            f"Gemini={gemini_verdict}, Codex requires confirmation: "
            f"{_sanitize_notify(codex_output.strip(), limit=160)}"
        )
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": "ASK (codex)",
            }
        )
        log_summary(
            "ASK", "codex", f"gemini={gemini_verdict}, codex=ASK, took={elapsed:.1f}s"
        )
        notify("Bash Review - 確認が必要", f"{short_cmd}", 8)
        sys.exit(2)

    elif codex_verdict == "ERROR":
        # Codex 呼び出し失敗時は Gemini の判定にフォールバック。
        # ask / deny のどちらに倒れても Codex ではブロック (exit 2) になる。
        fallback_decision = "ask" if gemini_verdict in ("ASK", "ERROR") else "deny"
        emit_block(
            f"Gemini={gemini_verdict} "
            f"({_sanitize_notify(gemini_output.strip(), limit=160)}), "
            f"Codex unavailable: {_sanitize_notify(codex_output, limit=160)}"
        )
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": f"{fallback_decision.upper()} (codex error fallback)",
            }
        )
        log_summary(
            fallback_decision.upper(),
            "fallback",
            f"gemini={gemini_verdict}, codex=ERROR, took={elapsed:.1f}s",
        )
        notify("Bash Review", f"Codexエラー: {short_cmd}", 8)
        sys.exit(2)

    else:  # DENY
        emit_block(
            f"Gemini={gemini_verdict}, Codex denied: "
            f"{_sanitize_notify(codex_output.strip(), limit=160)}"
        )
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": "DENY (codex)",
            }
        )
        log_summary(
            "DENY", "codex", f"gemini={gemini_verdict}, codex=DENY, took={elapsed:.1f}s"
        )
        notify("Bash Review - 拒否", f"{short_cmd}", 8)
        sys.exit(2)

    sys.exit(0)

except SystemExit:
    raise
except Exception as exc:  # noqa: BLE001  Bash ゲートは何があってもブロックに倒す
    # 判定確定前の例外だけブロック (exit 2) に倒す。確定後 (ログ/通知) の
    # 例外では確定済みの結果 (許可なら 0 / ブロックなら 2) を維持する。
    # stderr には例外の型と場所だけを出す (文面は出さない: describe_exception)。
    if decided_exit is None:
        emit_block(f"bash-review hook error: {describe_exception(exc)}")
        sys.exit(2)
    sys.exit(decided_exit)
