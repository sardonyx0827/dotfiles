# ~/.claude/hooks/gemini-api-bash-review.py
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bash_review_common import (
    _can_skip_review,  # noqa: E402
    _is_deny_command,
    _parse_verdict,
    _split_commands,
    append_and_rotate,
    notify,
    prune_dir,
)

_API_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
    IndexError,
    KeyError,
)


def emit_decision(decision: str, reason: str) -> None:
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


def log_summary(decision: str, reason: str) -> None:
    """結果をサマリーログに1行で追記し、500行超えたらローテーション"""
    short_cmd = command[:80] + "..." if len(command) > 80 else command
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {decision:5s} | {short_cmd} | {reason}\n"
    append_and_rotate(summary_log, line)


def _build_payload(target_model: str) -> tuple[str, bytes]:
    """指定モデル用の URL と payload を返す"""
    target_url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"
    data = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 256,
                "temperature": 0.0,
                "thinkingConfig": {"thinkingLevel": "minimal"},
            },
        }
    ).encode("utf-8")
    return target_url, data


def _call_gemini(target_url: str, data: bytes) -> str:
    """Gemini API を呼び出してテキスト応答を返す"""
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


# メインフロー
# stdin の破損や予期しない例外でも素通りさせず、必ず ask に倒す。
try:
    hook_input = json.loads(sys.stdin.buffer.read())
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # ログ設定
    # 詳細ログ（既存: コマンドごとに1ファイル）
    log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/gemini-api-review"  # nosec B108
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")
    prune_dir(log_dir)  # 1000件を超えたら古いものから削除

    # サマリーログ（新規: 1ファイルに追記・500行でローテーション）
    summary_log = os.path.expanduser("~/.claude/logs/gemini-bash-review.log")
    os.makedirs(os.path.dirname(summary_log), exist_ok=True)

    sub_commands = _split_commands(command)

    # --- 危険コマンドの即時拒否 ---
    for sub_cmd in sub_commands:
        matched, deny_name = _is_deny_command(sub_cmd)
        if matched:
            reason = f"Blocked dangerous command: '{deny_name}'"
            emit_decision("deny", reason)
            with open(log_file, "w") as f:
                f.write(f"Tool Name: {tool_name}\n")
                f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
                f.write("Gemini Output: DENY (pre-blocked)\n")
            log_summary("DENY", reason)
            notify("Gemini Review - 拒否", f"危険コマンド検出: {deny_name}", 8)
            sys.exit(0)

    # --- 安全コマンドのスキップ (複雑構文を含む場合はスキップせずレビューへ) ---
    if sub_commands and all(_can_skip_review(c) for c in sub_commands):
        emit_decision("allow", "Safe command, skipped Gemini review")
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write("Gemini Output: SKIP")
        log_summary("SKIP", "safe command")
        # 安全コマンドは通知不要
        sys.exit(0)

    # Gemini API 呼び出し
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        emit_decision("ask", "GEMINI_API_KEY not set, skipped review")
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write("Gemini Output: SKIP (no API key)")
        log_summary("ASK", "GEMINI_API_KEY not set")
        notify("Gemini Review", "APIキー未設定のため確認が必要です", 8)
        sys.exit(0)

    prompt = (
        "以下のツール呼び出しが安全かどうかを判断してください。\n"
        f"ツール: {tool_name}\n"
        f"内容: {json.dumps(tool_input, ensure_ascii=False)}\n\n"
        '安全なら "ALLOW"、危険なら "DENY: 理由"、確認が必要なら"ASK" とだけ答えてください。'
    )

    model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
    model_fallback = os.environ.get("GEMINI_FLASH_MODEL", "gemini-flash-latest")

    gemini_output = ""
    primary_url, primary_payload = _build_payload(model)
    try:
        gemini_output = _call_gemini(primary_url, primary_payload)
    except _API_ERRORS as primary_err:
        # フォールバックモデルでリトライ
        fallback_url, fallback_payload = _build_payload(model_fallback)
        try:
            gemini_output = _call_gemini(fallback_url, fallback_payload)
        except _API_ERRORS as fallback_err:
            gemini_output = f"ERROR: primary={primary_err}, fallback={fallback_err}"
            emit_decision(
                "ask",
                f"Gemini API error (fallback model failed): {fallback_err}",
            )
            with open(log_file, "w") as f:
                f.write(f"Tool Name: {tool_name}\n")
                f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
                f.write(f"Gemini Output: {gemini_output}\n")
            log_summary("ERROR", f"primary={primary_err}, fallback={fallback_err}")
            notify("Gemini Review Error", "APIエラーのため確認が必要です", 8)
            sys.exit(0)

    # 判定結果の処理
    short_cmd = command[:60] + "..." if len(command) > 60 else command
    verdict = _parse_verdict(gemini_output)

    if verdict == "ALLOW":
        emit_decision("allow", "Gemini reviewed and approved")
        log_summary("ALLOW", "approved by Gemini")
        notify("Gemini Review", f"許可: {short_cmd}", 4)

    elif verdict == "ASK":
        emit_decision("ask", "Gemini requires confirmation: " + gemini_output)
        log_summary("ASK", gemini_output.strip())
        notify("Gemini Review - 確認が必要", f"{short_cmd}", 8)

    else:  # DENY
        emit_decision("deny", gemini_output)
        log_summary("DENY", gemini_output.strip())
        notify("Gemini Review", f"拒否: {short_cmd}", 8)

    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write(f"Gemini Output: {gemini_output}\n")

    sys.exit(0)

except Exception as exc:  # noqa: BLE001  Bash ゲートは何があっても ask に倒す
    emit_decision("ask", f"gemini-api-bash-review hook error: {exc}")
    sys.exit(0)
