# ~/.claude/hooks/bash-review.py
# 判定の 3 層構造 (静的 DENY / 高リスク層の二モデル AND ゲート / 低リスク層の
# Gemini 一次 + Codex 二次) は _bash_review_common.py のヘッダー参照。
#
# Gemini/Codex の呼び出し (プロンプト生成・実行・パース) は _bash_review_common.py
# に集約している。一方、判定の組み合わせ木 (Gemini×Codex のどちらが
# ask/allow/deny になるか、理由文・detail log・summary・通知) はこの入口自身が
# 保持しており、codex 変種 (.codex/hooks/bash-review.py) 側に意図的にミラーした
# 複製がある。この木を変更するときは両方の入口を揃えて直すこと。分岐の理由
# コメントはこの .claude 側にまとめて書いてあり、.codex 側は参照のみに留める。
# 注釈の遅延評価は Python 3.9 (macOS の /usr/bin/python3) で import を通すため
# (_bash_review_common.py の同じ行のコメントを参照)。
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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

# サンドボックス CLI (csb) の分類。_sandbox_gate は _bash_review_common を import する
# ので、common からではなくこの入口から読む (common から読むと循環する)。
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
# 鍵は前後の空白を落とす: CRLF で保存した秘密ファイルから読むと末尾に \r が付き、
# ヘッダ値として拒まれる (途中に残る制御文字は run_gemini_review が送らずに弾く)。
api_key = os.environ.get("GEMINI_API_KEY", "").strip()
gemini_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
gemini_fallback_model = os.environ.get(
    "GEMINI_FLASH_MODEL", DEFAULT_GEMINI_FALLBACK_MODEL
)


# 端末判定を stdout に出した後、後続のログ書き込みや通知が例外を投げても、
# 下の except の fail-safe (ask) でその判定を上書き (格下げ) させないためのフラグ。
decision_emitted = False


def emit_decision(decision: str, reason: str) -> None:
    """permissionDecision を stdout に出力"""
    global decision_emitted
    decision_emitted = True
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


# この実行の詳細ログを実際に書けたか。書けたときだけ、サマリーの reason の末尾に
# ` detail=<詳細ログの basename>` を添える (detail_ref)。bash-review-log mod はこの名前で
# サマリーの行と詳細ログを 1 対 1 に突き合わせる (名前の無い旧い行は時刻とコマンドの
# 先頭で推定する)。どの分岐も詳細ログを書いてからサマリーを書く。
detail_written = False


def detail_ref() -> str:
    if not detail_written or log_file is None:
        return ""
    return f" detail={os.path.basename(log_file)}"


# 共有モジュールの関数を、この入口のモジュールグローバル
# (summary_log / command / log_file / tool_name / tool_input / prompt / api_key
# / gemini_model / gemini_fallback_model) を閉じ込めた薄いラッパーで包む。
# これらのグローバルは try 内で stdin を読んでから設定されるが、ラッパーは
# それ以降にしか呼ばれないため実行時に解決される。
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
# この入口から見た csb の実体。~/.local/bin/csb は install.sh が書く固定のラッパー
# (_sandbox_gate.csb_wrapper_text) で、~/.claude/hooks/claude_sandbox.py を起動する。
# ~/.claude はリポジトリの .claude への symlink なので realpath で比べる。
HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
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


# メインフロー
# stdin の破損 (空 / 非JSON / tool_input が非dict) や予期しない例外でも
# 決して素通りさせず、必ずユーザー確認 (ask) に倒してから終了する。
try:
    hook_input = json.loads(sys.stdin.buffer.read())
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # ログ設定
    # ログの準備と書き込みは判定より前に走るが、判定には影響させない。root を別の uid が
    # 作っていた (`sudo claude`・コンテナ)、symlink が仕込まれていた、書き込めない、と
    # いった環境の事情は「この実行の詳細ログを止める」だけで済ませる (以前は例外が
    # catch-all に落ち、`ls` を含む全コマンドが ask になっていた)。コマンドの解析・
    # レビューの例外は従来どおりフェイルクローズのまま。

    # サマリーログ (1ファイルに追記・500行でローテーション)
    summary_log = os.path.expanduser("~/.claude/logs/bash-review.log")

    # 詳細ログ (コマンドごとに1ファイル)
    log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/bash-review"  # nosec B108
    # root (/tmp/claude_hooks) から下の各階層を「実ディレクトリ・自分の所有・0700」で
    # 検査する (ログにはコマンド全文が入る)。通らなければ詳細ログは止め (log_file が
    # None)、古いログの掃除も検査に通ったときだけ行う (symlink の先を掃除しない)。
    detail_log_problem = prepare_detail_log_dir(
        log_dir,
        root="/tmp/claude_hooks",  # nosec B108
    )
    # ナノ秒 + PID でファイル名を一意化する。秒粒度 (int(time.time())) だと
    # 同一秒内に複数コマンドをレビューした際に同名となり、後のログが前を上書き
    # して監査ログが失われる。
    log_file = (
        None
        if detail_log_problem
        else os.path.join(log_dir, f"bash_cmd_{time.time_ns()}_{os.getpid()}.log")
    )
    if detail_log_problem:
        # bash-review-log mod は全文をこの詳細ログから引く。黙って止めると全文の照合が
        # 無言で外れるので、サマリーに 1 行残す。この時点では秘密スキャン前なので
        # コマンドは書かない。止まり続ける間 毎回書くとサマリーが NOTE で埋まるので、
        # 直近 100 行に既にあれば書かない (note_detail_log_disabled)。
        note_detail_log_disabled(summary_log, detail_log_problem)

    # --- 入力サイズの上限 (何よりも先に。分割・秘密スキャン・プロンプト生成より前) ---
    # 上限超過は二次の計算量になり得る入力。解析せず ask に倒す (deny でも無言 allow でも
    # ない)。解析していない = 秘密の有無も分からないので、生コマンドはログへ書かない。
    limit_reason = input_limit_reason(command, tool_input)
    if limit_reason:
        omitted = "[input not logged: over the review size limit]"
        reason = (
            f"{limit_reason}; not analysed or sent to external review — "
            "confirm manually"
        )
        emit_decision("ask", reason)
        detail_written = _write_detail_log(
            log_file, tool_name, {"command": omitted}, {"Result": "ASK (input limit)"}
        )
        _log_summary(summary_log, omitted, "ASK", "size", reason + detail_ref())
        notify("Bash Review - 確認が必要", "入力が大きすぎるため静的解析を省略", 8)
        sys.exit(0)

    # 一次処理: Gemini API 用プロンプト (tool_input に依存するので try 内で組む)
    prompt = build_review_prompt(tool_name, tool_input)

    sub_commands = _split_commands(command)

    # --- 秘密スキャン (静的解析) ---
    # 判定 (deny/safe-skip/secret-ask) の優先順位は変えないが、結果はどの分岐で
    # ログを書く場合でも redact のスイッチとして先に使う。deny される curl の
    # bearer や safe-skip される echo <key> も、秘密が載っていればローカルログ
    # (ディスク常駐) には生コマンドを残さない。
    secret_found, secret_label = scan_secrets(command, tool_input)

    # --- 危険コマンドの即時拒否 (改行分割は find_deny_command が捌く) ---
    # command も渡す: heredoc 本体や複数行のクォート文字列 (コミットメッセージ等) の
    # 行が DENY 名で始まるだけでハード DENY しないため (find_deny_command 参照)。
    matched, deny_name = find_deny_command(sub_commands, command)
    if matched:
        reason = f"Blocked dangerous command: '{deny_name}'"
        emit_decision("deny", reason)
        write_detail_log({"Result": "DENY (pre-blocked)"}, redact_input=secret_found)
        log_summary("DENY", "pre", reason, redact_command=secret_found)
        notify("Bash Review - 拒否", f"危険コマンド検出: {deny_name}", 8)
        sys.exit(0)

    # --- codex のサンドボックス / 承認を外すフラグは静的に ask ---
    # モデルの判定で覆らないよう LLM 呼び出しより前に決める (codex_flag_ask_reason)。
    # 秘密を検出済みなら理由文を固定文にする (理由文は redact_command の対象外)。
    codex_reason = codex_flag_ask_reason(sub_commands, command, redact=secret_found)
    if codex_reason:
        emit_decision("ask", codex_reason)
        write_detail_log({"Result": "ASK (codex flag)"}, redact_input=secret_found)
        log_summary("ASK", "pre", codex_reason, redact_command=secret_found)
        notify("Bash Review - 確認が必要", "codex の権限を広げるフラグを検出", 8)
        sys.exit(0)

    # --- セッションの作業ディレクトリ (ハーネスの判定とサンドボックスの段で同じ値) ---
    # 相対の行き先・cd の連鎖・ディレクトリへの書き込みは、この cwd で解決してから照合する
    # (harness_write_ask_reason の cwd=)。None なら字面だけで判定する (session_cwd)。
    review_cwd = session_cwd(hook_input)
    if review_cwd is None:
        review_cwd = HARNESS_CWD_UNKNOWN

    # --- ハーネス自身の設定 (settings / hooks / mods 等) への書き込みは静的に ask ---
    # ゲートを書き換えるコマンドを単独モデルの ALLOW で通さない (harness_write_ask_reason)。
    # 理由文は固定の語彙だけで組まれ、コマンドの語を含まない。
    harness_reason = harness_write_ask_reason(sub_commands, command, cwd=review_cwd)
    if harness_reason:
        emit_decision("ask", harness_reason)
        write_detail_log({"Result": "ASK (harness write)"}, redact_input=secret_found)
        log_summary("ASK", "pre", harness_reason, redact_command=secret_found)
        notify("Bash Review - 確認が必要", "ハーネス設定への書き込みを検出", 8)
        sys.exit(0)

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
        emit_decision(sandbox_verdict, sandbox_text)
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
        sys.exit(0)
    if sandbox_verdict == "allow":
        emit_decision("allow", sandbox_text)
        write_detail_log({"Result": "ALLOW (sandbox)"})
        log_summary("ALLOW", "sandbox", sandbox_reason)
        sys.exit(0)
    # <<< shared: sandbox-stage

    # --- 安全コマンドのスキップ ---
    if sub_commands and all(_can_skip_review(c) for c in sub_commands):
        emit_decision("allow", "Safe command, skipped review")
        write_detail_log({"Result": "SKIP (safe command)"}, redact_input=secret_found)
        log_summary("ALLOW", "pre", "safe command", redact_command=secret_found)
        sys.exit(0)

    # --- 秘密の外部送信ガード (LLM 呼び出し前) ---
    # deny でも safe-skip でもない (=これから LLM へ送られる) コマンドに生の資格
    # 情報が載っていれば、Gemini/Codex を一切呼ばず ask にフェイルクローズする
    # (外部送信の防止)。理由・通知・ログには一致した値を出さず種別ラベルのみ。
    if secret_found:
        reason = (
            f"Command appears to contain a credential ({secret_label}); "
            "not sending to external review — confirm manually"
        )
        emit_decision("ask", reason)
        write_detail_log(
            {"Result": f"ASK (secret pre-scan: {secret_label})"}, redact_input=True
        )
        log_summary(
            "ASK", "secret", f"credential detected: {secret_label}", redact_command=True
        )
        notify(
            "Bash Review - 確認が必要", "認証情報を検出したため外部レビューを回避", 8
        )
        sys.exit(0)

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
        emit_decision(decision, reason)
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
        if decision == "deny":
            notify("Bash Review - 拒否", f"{short_cmd}", 8)
        elif decision == "allow":
            notify("Bash Review", f"高リスク許可 (両モデル承認): {short_cmd}", 4)
        else:
            notify("Bash Review - 確認が必要", f"高リスク: {short_cmd}", 8)
        sys.exit(0)

    gemini_verdict, gemini_output = run_gemini_review()

    # Gemini が ALLOW と判定した場合はそのまま許可
    if gemini_verdict == "ALLOW":
        emit_decision("allow", "Gemini reviewed and approved")
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
        # Gemini の明示的 DENY を Codex の ALLOW 単独で自動上書きしない。
        # 上書きを許すと、攻撃者はどちらか一方のモデルさえ説得すれば実行に
        # 至れてしまう (実質 OR ゲート化)。両判定を添えてユーザー判断に回す。
        emit_decision(
            "ask",
            f"Gemini=DENY: {_sanitize_notify(gemini_output.strip(), limit=160)} "
            f"but Codex approved: {_sanitize_notify(codex_output.strip(), limit=160)}"
            " — confirm manually",
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

    elif codex_verdict == "ALLOW" and gemini_verdict == "ASK":
        # Gemini の ASK は「確認が必要」= 人間確認の明示要求 (レビュープロンプトの
        # 定義)。ERROR (不可用 = 無意見) と違い単なる不確実ではないため、DENY と
        # 同じく単独 Codex ALLOW では解消せず、両判定を添えて人間へ回す。片方の
        # ALLOW で ASK を潰せると、片モデルさえ説得すれば実行に至る OR ゲートに
        # なり、「疑わしきは人間へ倒す」原則が ASK/DENY で非一貫になる。
        emit_decision(
            "ask",
            f"Gemini=ASK (confirmation requested) but Codex approved: "
            f"{_sanitize_notify(codex_output.strip(), limit=160)} — confirm manually",
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

    elif codex_verdict == "ALLOW":
        # ここに来るのは gemini==ERROR (Gemini 不可用 = 唯一の意見が Codex) のみ。
        # DENY/ASK は上の分岐で捌き済み。無意見なので graceful degradation として
        # 単独 Codex ALLOW で解消してよい (ここは摩擦を増やさない)。
        emit_decision(
            "allow",
            f"Gemini flagged ({gemini_verdict}) but Codex approved: "
            f"{_sanitize_notify(codex_output.strip(), limit=160)}",
        )
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
        emit_decision(
            "ask",
            f"Gemini={gemini_verdict}, Codex requires confirmation: "
            f"{_sanitize_notify(codex_output.strip(), limit=160)}",
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

    elif codex_verdict == "ERROR":
        # Codex 呼び出し失敗時は Gemini の判定にフォールバック
        fallback_decision = "ask" if gemini_verdict in ("ASK", "ERROR") else "deny"
        emit_decision(
            fallback_decision,
            f"Gemini={gemini_verdict} "
            f"({_sanitize_notify(gemini_output.strip(), limit=160)}), "
            f"Codex unavailable: {_sanitize_notify(codex_output, limit=160)}",
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

    else:  # DENY
        emit_decision(
            "deny",
            f"Gemini={gemini_verdict}, Codex denied: "
            f"{_sanitize_notify(codex_output.strip(), limit=160)}",
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

    sys.exit(0)

except Exception as exc:  # noqa: BLE001  Bash ゲートは何があっても ask に倒す
    # 判定を出す前の例外だけ ask に倒す。判定を出した後 (ログ/通知) の例外で
    # deny/allow を ask に格下げしたり、JSON を二重に出力したりしない。
    # 理由には例外の型と場所だけを出す (文面は出さない: describe_exception)。
    if not decision_emitted:
        emit_decision("ask", f"bash-review hook error: {describe_exception(exc)}")
    sys.exit(0)
