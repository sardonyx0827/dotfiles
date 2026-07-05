# ~/.claude/hooks/claude-bash-review.py
import json
import os
import re
import subprocess
import sys
import time

# フック入力のJSONをstdinから読み込む
hook_input = json.loads(sys.stdin.buffer.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
command = tool_input.get("command", "")

# /tmp/claude_hooks/logs/PreToolUse/Bash/claude-review ディレクトリがなければ作成
# ログには、ツール名、ツール入力、Claudeの出力を保存
log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/claude-review"  # nosec B108
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")

# ログファイルが1000ファイルを超えたら古いものから削除
files = sorted(os.listdir(log_dir))
excess = len(files) - 1000
for f in files[: max(0, excess)]:
    os.remove(os.path.join(log_dir, f))

# 明らかに安全なコマンドはスキップする
SAFE_COMMANDS = [
    # tmux は send-keys / new-session / run-shell で任意コマンド実行が可能な
    # ため全体をセーフ扱いにせず、読み取り系サブコマンドに限定する
    "tmux ls",
    "tmux list-sessions",
    "tmux list-windows",
    "tmux list-panes",
    "tmux has-session",
    "tmux display-message",
    "tmux show-options",
    "tmux capture-pane",
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
    "head",
    "tail",
    "wc",
    "which",
    "whereis",
    "uname",
    "date",
    "tree",
    "jq",
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


def _split_commands(cmd: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*(?:&&|\|\||[|;])\s*", cmd) if p.strip()]


def _is_safe_command(cmd: str) -> bool:
    return any(cmd == safe or cmd.startswith(safe + " ") for safe in SAFE_COMMANDS)


def _parse_verdict(output: str) -> str:
    """レビュー応答から判定を厳密に抽出する。

    行頭の ALLOW / ASK / DENY トークンのみを判定として採用し、
    DENY > ASK > ALLOW の優先順で解決する。部分文字列一致では判定しない
    ("DISALLOW" や DENY の理由文中に現れる "ALLOW" で許可に化けない)。
    判定トークンが見つからない応答は ASK に倒してユーザー確認へ回す。
    """
    verdicts = set()
    for line in output.splitlines():
        m = re.match(r'^\s*["\'`*_#>-]*\s*(ALLOW|ASK|DENY)\b', line)
        if m:
            verdicts.add(m.group(1))
    for verdict in ("DENY", "ASK", "ALLOW"):
        if verdict in verdicts:
            return verdict
    return "ASK"


sub_commands = _split_commands(command)
# すべてのサブコマンドが安全な場合のみスキップする
if sub_commands and all(_is_safe_command(c) for c in sub_commands):
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Safe command, skipped Claude review",
                }
            }
        )
    )
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
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "Error during review, skipped review: "
                    + result.stderr,
                }
            }
        )
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write(f"Claude Output: ERROR: {result.stderr}\n")
    sys.exit(0)

verdict = _parse_verdict(result.stdout)

if verdict == "ALLOW":
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Claude reviewed and approved",
                }
            }
        )
    )
elif verdict == "ASK":
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "Claude requires confirmation: "
                    + result.stdout,
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
    f.write(f"Claude Output: {result.stdout}\n")

sys.exit(0)
