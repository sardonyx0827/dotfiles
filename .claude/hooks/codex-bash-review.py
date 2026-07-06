# ~/.claude/hooks/codex-bash-review.py
import json
import os
import subprocess
import sys
import time

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


# メインフロー
# stdin の破損や予期しない例外でも素通りさせず、必ず ask に倒す。
try:
    hook_input = json.loads(sys.stdin.buffer.read())
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # ログ設定
    # 詳細ログ（既存: コマンドごとに1ファイル）
    log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/codex-review"  # nosec B108
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")
    prune_dir(log_dir)  # 1000件を超えたら古いものから削除

    # サマリーログ（1ファイルに追記・500行でローテーション）
    summary_log = os.path.expanduser("~/.claude/logs/codex-bash-review.log")
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
                f.write("Codex Output: DENY (pre-blocked)\n")
            log_summary("DENY", reason)
            notify("Codex Review - 拒否", f"危険コマンド検出: {deny_name}", 8)
            sys.exit(0)

    # --- 安全コマンドのスキップ (複雑構文を含む場合はスキップせずレビューへ) ---
    if sub_commands and all(_can_skip_review(c) for c in sub_commands):
        emit_decision("allow", "Safe command, skipped Codex review")
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write("Codex Output: SKIP")
        log_summary("SKIP", "safe command")
        sys.exit(0)

    prompt = f"""
以下のツール呼び出しが安全かどうかを判断してください。
ツール: {tool_name}
内容: {json.dumps(tool_input, ensure_ascii=False)}

安全なら "ALLOW"、危険なら "DENY: 理由"、確認が必要なら"ASK" とだけ答えてください。
"""

    result = subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", prompt],
        capture_output=True,
        text=True,
        timeout=30,
    )

    # errorがあればユーザーに確認する
    if result.returncode != 0:
        emit_decision("ask", "Error during Codex review: " + result.stderr)
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write(f"Codex Output: ERROR: {result.stderr}\n")
        log_summary("ERROR", f"Codex error: {result.stderr.strip()}")
        notify("Codex Review Error", "エラーのため確認が必要です", 8)
        sys.exit(0)

    # 判定結果の処理
    short_cmd = command[:60] + "..." if len(command) > 60 else command
    verdict = _parse_verdict(result.stdout)

    if verdict == "ALLOW":
        emit_decision("allow", "Codex reviewed and approved")
        log_summary("ALLOW", "approved by Codex")
        notify("Codex Review", f"許可: {short_cmd}", 4)

    elif verdict == "ASK":
        emit_decision("ask", "Codex requires confirmation: " + result.stdout)
        log_summary("ASK", result.stdout.strip())
        notify("Codex Review - 確認が必要", f"{short_cmd}", 8)

    else:  # DENY
        emit_decision("deny", result.stdout)
        log_summary("DENY", result.stdout.strip())
        notify("Codex Review", f"拒否: {short_cmd}", 8)

    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write(f"Codex Output: {result.stdout}\n")

    sys.exit(0)

except Exception as exc:  # noqa: BLE001  Bash ゲートは何があっても ask に倒す
    emit_decision("ask", f"codex-bash-review hook error: {exc}")
    sys.exit(0)
