# ~/.claude/hooks/bash-review.py
# 一次処理: Gemini API (高スループット)
# 二次処理: Gemini が ASK/DENY と判定した場合のみ Codex で再確認
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bash_review_common import (
    _can_skip_review,
    _is_deny_command,
    _parse_verdict,
    _split_commands,
    append_and_rotate,
    notify,
    prune_dir,
)
from _bash_review_common import write_detail_log as _write_detail_log  # noqa: E402

# 環境変数由来の設定 (stdin に依存しないので try の外で読む)
api_key = os.environ.get("GEMINI_API_KEY", "")
gemini_model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
gemini_fallback_model = os.environ.get("GEMINI_FLASH_MODEL", "gemini-flash-latest")

_API_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
    IndexError,
    KeyError,
)


def emit_decision(decision: str, reason: str) -> None:
    """permissionDecision を stdout に出力"""
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


def log_summary(decision: str, stage: str, reason: str) -> None:
    """結果をサマリーログに1行で追記し、500行超えたらローテーション"""
    short_cmd = command[:80] + "..." if len(command) > 80 else command
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {decision:5s} | {stage:8s} | {short_cmd} | {reason}\n"
    append_and_rotate(summary_log, line)


def write_detail_log(entries: dict) -> None:
    _write_detail_log(log_file, tool_name, tool_input, entries)


def _build_gemini_payload(target_model: str) -> tuple[str, bytes]:
    target_url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"
    data = json.dumps(
        {
            "contents": [{"parts": [{"text": PROMPT}]}],
            "generationConfig": {
                "maxOutputTokens": 256,
                "temperature": 0.0,
                "thinkingConfig": {"thinkingLevel": "minimal"},
            },
        }
    ).encode("utf-8")
    return target_url, data


def _call_gemini(target_url: str, data: bytes) -> str:
    req = urllib.request.Request(
        target_url,
        data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec: B310
        body = json.loads(resp.read().decode("utf-8"))
        return (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )


def run_gemini_review() -> tuple[str, str]:
    """Gemini の判定結果を (verdict, raw_output) で返す。verdict は ALLOW/ASK/DENY/ERROR。"""
    if not api_key:
        return "ERROR", "GEMINI_API_KEY not set"

    primary_url, primary_payload = _build_gemini_payload(gemini_model)
    try:
        output = _call_gemini(primary_url, primary_payload)
    except _API_ERRORS as primary_err:
        fallback_url, fallback_payload = _build_gemini_payload(gemini_fallback_model)
        try:
            output = _call_gemini(fallback_url, fallback_payload)
        except _API_ERRORS as fallback_err:
            return "ERROR", f"primary={primary_err}, fallback={fallback_err}"

    return _parse_verdict(output), output


def run_codex_review(gemini_verdict: str, gemini_output: str) -> tuple[str, str]:
    """Codex の判定結果を (verdict, raw_output) で返す。"""
    codex_prompt = f"""
以下のツール呼び出しを Gemini が一次レビューしたところ "{gemini_verdict}" と判定しました。
Gemini の応答: {gemini_output.strip()}

改めてあなた (Codex) の観点で安全性を判断してください。
ツール: {tool_name}
内容: {json.dumps(tool_input, ensure_ascii=False)}

安全なら "ALLOW"、危険なら "DENY: 理由"、確認が必要なら "ASK" とだけ答えてください。
"""

    try:
        result = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", codex_prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return "ERROR", f"Codex invocation failed: {err}"

    if result.returncode != 0:
        return "ERROR", f"Codex error: {result.stderr.strip()}"

    return _parse_verdict(result.stdout), result.stdout


# メインフロー
# stdin の破損 (空 / 非JSON / tool_input が非dict) や予期しない例外でも
# 決して素通りさせず、必ずユーザー確認 (ask) に倒してから終了する。
try:
    hook_input = json.loads(sys.stdin.buffer.read())
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # ログ設定
    # 詳細ログ (コマンドごとに1ファイル)
    log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/bash-review"  # nosec B108
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")
    prune_dir(log_dir)  # 1000件を超えたら古いものから削除

    # サマリーログ (1ファイルに追記・500行でローテーション)
    summary_log = os.path.expanduser("~/.claude/logs/bash-review.log")
    os.makedirs(os.path.dirname(summary_log), exist_ok=True)

    # 一次処理: Gemini API 用プロンプト (tool_input に依存するので try 内で組む)
    PROMPT = (
        "以下のツール呼び出しが安全かどうかを判断してください。\n"
        f"ツール: {tool_name}\n"
        f"内容: {json.dumps(tool_input, ensure_ascii=False)}\n\n"
        '安全なら "ALLOW"、危険なら "DENY: 理由"、確認が必要なら"ASK" とだけ答えてください。'
    )

    sub_commands = _split_commands(command)

    # --- 危険コマンドの即時拒否 ---
    for sub_cmd in sub_commands:
        matched, deny_name = _is_deny_command(sub_cmd)
        if matched:
            reason = f"Blocked dangerous command: '{deny_name}'"
            emit_decision("deny", reason)
            write_detail_log({"Result": "DENY (pre-blocked)"})
            log_summary("DENY", "pre", reason)
            notify("Bash Review - 拒否", f"危険コマンド検出: {deny_name}", 8)
            sys.exit(0)

    # --- 安全コマンドのスキップ ---
    if sub_commands and all(_can_skip_review(c) for c in sub_commands):
        emit_decision("allow", "Safe command, skipped review")
        write_detail_log({"Result": "SKIP (safe command)"})
        log_summary("ALLOW", "pre", "safe command")
        sys.exit(0)

    short_cmd = command[:60] + "..." if len(command) > 60 else command

    gemini_verdict, gemini_output = run_gemini_review()

    # Gemini が ALLOW と判定した場合はそのまま許可
    if gemini_verdict == "ALLOW":
        emit_decision("allow", "Gemini reviewed and approved")
        write_detail_log({"Gemini": gemini_output, "Result": "ALLOW (gemini)"})
        log_summary("ALLOW", "gemini", "approved by Gemini")
        notify("Bash Review", f"許可: {short_cmd}", 4)
        sys.exit(0)

    # Gemini が疑わしい (ASK/DENY/ERROR) 場合は Codex に二次確認
    codex_verdict, codex_output = run_codex_review(gemini_verdict, gemini_output)

    if codex_verdict == "ALLOW":
        emit_decision(
            "allow",
            f"Gemini flagged ({gemini_verdict}) but Codex approved: {codex_output.strip()}",
        )
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
        emit_decision(
            "ask",
            f"Gemini={gemini_verdict}, Codex requires confirmation: {codex_output.strip()}",
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

    elif codex_verdict == "ERROR":
        # Codex 呼び出し失敗時は Gemini の判定にフォールバック
        fallback_decision = "ask" if gemini_verdict in ("ASK", "ERROR") else "deny"
        emit_decision(
            fallback_decision,
            f"Gemini={gemini_verdict} ({gemini_output.strip()}), Codex unavailable: {codex_output}",
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

    else:  # DENY
        emit_decision(
            "deny",
            f"Gemini={gemini_verdict}, Codex denied: {codex_output.strip()}",
        )
        write_detail_log(
            {
                "Gemini": gemini_output,
                "Codex": codex_output,
                "Result": "DENY (codex)",
            }
        )
        log_summary("DENY", "codex", f"gemini={gemini_verdict}, codex=DENY")
        notify("Bash Review - 拒否", f"{short_cmd}", 8)

    sys.exit(0)

except Exception as exc:  # noqa: BLE001  Bash ゲートは何があっても ask に倒す
    emit_decision("ask", f"bash-review hook error: {exc}")
    sys.exit(0)
