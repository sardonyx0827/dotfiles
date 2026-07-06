# ~/.claude/hooks/claude-bash-review.py
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bash_review_common import (
    _can_skip_review,  # noqa: E402
    _parse_verdict,
    _split_commands,
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


# メインフロー
# stdin の破損や予期しない例外でも素通りさせず、必ず ask に倒す。
try:
    hook_input = json.loads(sys.stdin.buffer.read())
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # /tmp/claude_hooks/logs/PreToolUse/Bash/claude-review ディレクトリがなければ作成
    # ログには、ツール名、ツール入力、Claudeの出力を保存
    log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/claude-review"  # nosec B108
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")
    prune_dir(log_dir)  # 1000ファイルを超えたら古いものから削除

    sub_commands = _split_commands(command)
    # すべてのサブコマンドが安全 かつ 複雑構文を含まない場合のみスキップする
    # (コマンド置換 / リダイレクト等でのレビュー迂回を防ぐ)
    if sub_commands and all(_can_skip_review(c) for c in sub_commands):
        emit_decision("allow", "Safe command, skipped Claude review")
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write("Claude Output: SKIP")
        sys.exit(0)

    prompt = f"""
以下のツール呼び出しが安全かどうかを判断してください。
ツール: {tool_name}
内容: {json.dumps(tool_input, ensure_ascii=False)}

安全なら "ALLOW"、危険なら "DENY: 理由"、確認が必要なら"ASK" とだけ答えてください。
"""

    result = subprocess.run(
        ["claude", "--model", "haiku", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=30,
    )

    # errorがあればユーザーに確認する
    if result.returncode != 0:
        emit_decision("ask", "Error during review, skipped review: " + result.stderr)
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write(f"Claude Output: ERROR: {result.stderr}\n")
        sys.exit(0)

    verdict = _parse_verdict(result.stdout)

    if verdict == "ALLOW":
        emit_decision("allow", "Claude reviewed and approved")
    elif verdict == "ASK":
        emit_decision("ask", "Claude requires confirmation: " + result.stdout)
    else:
        emit_decision("deny", result.stdout)

    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write(f"Claude Output: {result.stdout}\n")

    sys.exit(0)

except Exception as exc:  # noqa: BLE001  Bash ゲートは何があっても ask に倒す
    emit_decision("ask", f"claude-bash-review hook error: {exc}")
    sys.exit(0)
