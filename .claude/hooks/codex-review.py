# /Users/sardonyx0827/work/github/dotfiles/.claude/hooks/codex-review.py
import json
import os
import subprocess
import sys
import time

# stdin は非ブロッキングで読む
hook_input = json.loads(sys.stdin.buffer.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
command = tool_input.get("command", "")

# /tmp/claude_hooks/logs/PreToolUse/Bash/codex-review ディレクトリがなければ作成
# ログには、ツール名、ツール入力、Codexの出力を保存
log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/codex-review"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")

# ログファイルが1000ファイルを超えたら古いものから削除
files = sorted(os.listdir(log_dir))
excess = len(files) - 1000
for f in files[: max(0, excess)]:
    os.remove(os.path.join(log_dir, f))

# 念の為、明らかに安全なコマンドはスキップする
SAFE_COMMANDS = [
    "ls",
    "cat",
    "pwd",
    "echo",
    "printf",
    "git status",
    "git log",
    "git diff",
    "git branch",
    "grep",
    "rg",
    "find",
    "head",
    "tail",
    "wc",
    "which",
    "whereis",
    "uname",
    "date",
    "tree",
    "jq",
    "sed",
    "awk",
    "npm run",
    "pnpm run",
    "yarn run",
    "tsc",
    "eslint",
    "prettier",
    "pytest",
    "vitest",
    "jest",
]
# コマンドが安全なものから始まる場合は、Codexのレビューをスキップして許可する(前方一致)
if any(command.startswith(safe + " ") for safe in SAFE_COMMANDS):
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason":
                        "Safe command, skipped Codex review",
                }
            }
        )
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write("Codex Output: SKIP")
    sys.exit(0)

prompt = f"""
以下のツール呼び出しが安全かどうかを判断してください。
ツール: {tool_name}
内容: {json.dumps(tool_input, ensure_ascii=False)}

安全なら "ALLOW"、危険なら "DENY: 理由" とだけ答えてください。
"""

result = subprocess.run(
    ["codex", "exec", "--skip-git-repo-check", prompt],
    capture_output=True,
    text=True,
    timeout=30,
)

if "ALLOW" in result.stdout:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Codex reviewed and approved",
                }
            }
        )
    )
else:

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": result.stdout,
                }
            }
        )
    )

with open(log_file, "w") as f:
    f.write(f"Tool Name: {tool_name}\n")
    f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
    f.write(f"Codex Output: {result.stdout}\n")

sys.exit(0)
