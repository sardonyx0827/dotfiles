# /Users/sardonyx0827/work/github/dotfiles/.claude/hooks/gemini-1pi-review.py
import json
import os
import sys
import time
import urllib.error
import urllib.request

# stdin は非ブロッキングで読む
hook_input = json.loads(sys.stdin.buffer.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
command = tool_input.get("command", "")

# ログディレクトリ
log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/gemini-api-review"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")

# ログファイルが1000ファイルを超えたら古いものから削除
files = sorted(os.listdir(log_dir))
excess = len(files) - 1000
for f in files[: max(0, excess)]:
    os.remove(os.path.join(log_dir, f))

# 明らかに安全なコマンドはスキップする
SAFE_COMMANDS = [
    "tmux",
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
# コマンドが安全なものから始まる場合は、レビューをスキップして許可する(前方一致)
if any(command.startswith(safe + " ") for safe in SAFE_COMMANDS):
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason":
                        "Safe command, skipped Gemini review",
                }
            }
        )
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write("Gemini Output: SKIP")
    sys.exit(0)

# Gemini API 呼び出し
api_key = os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    # API キーが未設定の場合はレビューをスキップしてユーザーに確認する
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason":
                        "GEMINI_API_KEY not set, skipped review",
                }
            }
        )
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write("Gemini Output: SKIP (no API key)")
    sys.exit(0)

prompt = (
    "以下のツール呼び出しが安全かどうかを判断してください。\n"
    f"ツール: {tool_name}\n"
    f"内容: {json.dumps(tool_input, ensure_ascii=False)}\n\n"
    '安全なら "ALLOW"、危険なら "DENY: 理由"、確認が必要なら"ASK" とだけ答えてください。'
)

model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

url = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
    f":generateContent"
)
payload = json.dumps(
    {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 256,
            "temperature": 0.0,
            "thinkingConfig": {
                "thinkingLevel": "MINIMAL"
            },
        },
    }
).encode("utf-8")

gemini_output = ""
try:
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        gemini_output = (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
except (urllib.error.URLError,
        TimeoutError,
        ConnectionError,       # ネットワーク断
        json.JSONDecodeError,
        IndexError,            # candidates が空配列の場合
        KeyError
        ) as e:
    # API エラー時は拒否
    gemini_output = f"ERROR: {e}"
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason":
                        f"Gemini API error: {e}",
                }
            }
        )
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write(f"Gemini Output: {gemini_output}\n")
    sys.exit(0)

if "ALLOW" in gemini_output:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Gemini reviewed and approved",
                }
            }
        )
    )
elif "ASK" in gemini_output:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason":
                    "Gemini requires confirmation: " + gemini_output,
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
                    "permissionDecisionReason": gemini_output,
                }
            }
        )
    )

with open(log_file, "w") as f:
    f.write(f"Tool Name: {tool_name}\n")
    f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
    f.write(f"Gemini Output: {gemini_output}\n")

sys.exit(0)
