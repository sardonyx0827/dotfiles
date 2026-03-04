# /Users/sardonyx0827/work/github/dotfiles/.claude/hooks/codex-review.py
import json
import sys
import subprocess

# stdin は非ブロッキングで読む
hook_input = json.loads(sys.stdin.buffer.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})

prompt = f"""
以下のツール呼び出しが安全かどうかを判断してください。
ツール: {tool_name}
内容: {json.dumps(tool_input, ensure_ascii=False)}

安全なら "ALLOW"、危険なら "DENY: 理由" とだけ答えてください。
"""

result = subprocess.run(
    ["codex", "exec", "--skip-git-repo-check", prompt],
    capture_output=True, text=True, timeout=30
)

if "ALLOW" in result.stdout:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Codex reviewed and approved"
        }
    }))
else:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": result.stdout
        }
    }))

sys.exit(0)
