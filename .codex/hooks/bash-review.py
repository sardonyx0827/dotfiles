# ~/.codex/hooks/bash-review.py
# 一次処理: Gemini API (高スループット)
# 二次処理: Gemini が ASK/DENY と判定した場合のみ Codex で再確認
# Codex は permissionDecision JSON を扱わないため、許可は exit 0 (無出力)、
# ブロック (ask/deny) は exit 2 + stderr で理由を伝える。
#
# Gemini/Codex のレビュー呼び出しロジックは _bash_review_common.py に集約し、
# claude 変種 (.claude/hooks/bash-review.py) とドリフトしないようにしてある。
# この入口が持つのは「判定結果 (verdict) を exit code + stderr に変換する」
# 変種固有の処理だけ。
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bash_review_common import (
    _can_skip_review,
    _is_deny_command,
    _split_commands,
    build_review_prompt,
    notify,
    prune_dir,
)
from _bash_review_common import log_summary as _log_summary  # noqa: E402
from _bash_review_common import run_codex_review as _run_codex_review  # noqa: E402
from _bash_review_common import run_gemini_review as _run_gemini_review  # noqa: E402
from _bash_review_common import write_detail_log as _write_detail_log  # noqa: E402

# 環境変数由来の設定 (stdin に依存しないので try の外で読む)
api_key = os.environ.get("GEMINI_API_KEY", "")
gemini_model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
gemini_fallback_model = os.environ.get("GEMINI_FLASH_MODEL", "gemini-flash-latest")


# 判定確定後にログ/通知が例外を投げても、下の except の fail-safe
# (block/exit 2) で確定済みの結果を上書きさせないための状態。ブロック系は
# emit_block を通るのでここで 2 を確定し、許可系は各分岐で 0 を確定する。
decided_exit: int | None = None


def emit_block(reason: str) -> None:
    """ブロック理由を stderr に出力する (exit 2 と組で使う)"""
    global decided_exit
    decided_exit = 2
    print(reason, file=sys.stderr)


# 共有モジュールの関数を、この入口のモジュールグローバル
# (summary_log / command / log_file / tool_name / tool_input / prompt / api_key
# / gemini_model / gemini_fallback_model) を閉じ込めた薄いラッパーで包む。
# これらのグローバルは try 内で stdin を読んでから設定されるが、ラッパーは
# それ以降にしか呼ばれないため実行時に解決される。
def log_summary(decision: str, stage: str, reason: str) -> None:
    _log_summary(summary_log, command, decision, stage, reason)


def write_detail_log(entries: dict) -> None:
    _write_detail_log(log_file, tool_name, tool_input, entries)


def run_gemini_review() -> tuple[str, str]:
    return _run_gemini_review(prompt, api_key, gemini_model, gemini_fallback_model)


def run_codex_review(gemini_verdict: str, gemini_output: str) -> tuple[str, str]:
    return _run_codex_review(gemini_verdict, gemini_output, tool_name, tool_input)


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
    # 詳細ログ (コマンドごとに1ファイル)
    log_dir = "/tmp/codex_hooks/logs/PreToolUse/Bash/bash-review"  # nosec B108
    os.makedirs(log_dir, exist_ok=True)
    # ナノ秒 + PID でファイル名を一意化する。秒粒度 (int(time.time())) だと
    # 同一秒内に複数コマンドをレビューした際に同名となり、後のログが前を上書き
    # して監査ログが失われる。
    log_file = os.path.join(log_dir, f"bash_cmd_{time.time_ns()}_{os.getpid()}.log")
    prune_dir(log_dir)  # 1000件を超えたら古いものから削除

    # サマリーログ (1ファイルに追記・500行でローテーション)
    summary_log = os.path.expanduser("~/.codex/logs/bash-review.log")
    os.makedirs(os.path.dirname(summary_log), exist_ok=True)

    # 一次処理: Gemini API 用プロンプト (tool_input に依存するので try 内で組む)
    prompt = build_review_prompt(tool_name, tool_input)

    sub_commands = _split_commands(command)

    # --- 危険コマンドの即時拒否 ---
    for sub_cmd in sub_commands:
        matched, deny_name = _is_deny_command(sub_cmd)
        if matched:
            reason = f"Blocked dangerous command: '{deny_name}'"
            emit_block(reason)
            write_detail_log({"Result": "DENY (pre-blocked)"})
            log_summary("DENY", "pre", reason)
            notify("Bash Review - 拒否", f"危険コマンド検出: {deny_name}", 8)
            sys.exit(2)

    # --- 安全コマンドのスキップ ---
    if sub_commands and all(_can_skip_review(c) for c in sub_commands):
        decided_exit = 0
        write_detail_log({"Result": "SKIP (safe command)"})
        log_summary("ALLOW", "pre", "safe command")
        sys.exit(0)

    short_cmd = command[:60] + "..." if len(command) > 60 else command

    gemini_verdict, gemini_output = run_gemini_review()

    # Gemini が ALLOW と判定した場合はそのまま許可
    if gemini_verdict == "ALLOW":
        decided_exit = 0
        write_detail_log({"Gemini": gemini_output, "Result": "ALLOW (gemini)"})
        log_summary("ALLOW", "gemini", "approved by Gemini")
        notify("Bash Review", f"許可: {short_cmd}", 4)
        sys.exit(0)

    # Gemini が疑わしい (ASK/DENY/ERROR) 場合は Codex に二次確認
    codex_verdict, codex_output = run_codex_review(gemini_verdict, gemini_output)

    if codex_verdict == "ALLOW":
        decided_exit = 0
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": f"ALLOW (codex overrides gemini {gemini_verdict})",
            }
        )
        log_summary("ALLOW", "codex", f"gemini={gemini_verdict}, codex=ALLOW")
        notify("Bash Review", f"Codex 承認: {short_cmd}", 4)

    elif codex_verdict == "ASK":
        emit_block(
            f"Gemini={gemini_verdict}, Codex requires confirmation: {codex_output.strip()}"
        )
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": "ASK (codex)",
            }
        )
        log_summary("ASK", "codex", f"gemini={gemini_verdict}, codex=ASK")
        notify("Bash Review - 確認が必要", f"{short_cmd}", 8)
        sys.exit(2)

    elif codex_verdict == "ERROR":
        # Codex 呼び出し失敗時は Gemini の判定にフォールバック。
        # ask / deny のどちらに倒れても Codex ではブロック (exit 2) になる。
        fallback_decision = "ask" if gemini_verdict in ("ASK", "ERROR") else "deny"
        emit_block(
            f"Gemini={gemini_verdict} ({gemini_output.strip()}), Codex unavailable: {codex_output}"
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
            f"gemini={gemini_verdict}, codex=ERROR",
        )
        notify("Bash Review", f"Codexエラー: {short_cmd}", 8)
        sys.exit(2)

    else:  # DENY
        emit_block(f"Gemini={gemini_verdict}, Codex denied: {codex_output.strip()}")
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": "DENY (codex)",
            }
        )
        log_summary("DENY", "codex", f"gemini={gemini_verdict}, codex=DENY")
        notify("Bash Review - 拒否", f"{short_cmd}", 8)
        sys.exit(2)

    sys.exit(0)

except SystemExit:
    raise
except Exception as exc:  # noqa: BLE001  Bash ゲートは何があってもブロックに倒す
    # 判定確定前の例外だけブロック (exit 2) に倒す。確定後 (ログ/通知) の
    # 例外では確定済みの結果 (許可なら 0 / ブロックなら 2) を維持する。
    if decided_exit is None:
        emit_block(f"bash-review hook error: {exc}")
        sys.exit(2)
    sys.exit(decided_exit)
