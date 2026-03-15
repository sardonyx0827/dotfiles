# ~.claude/hooks/gemini-api-review.py
import os
import sys
import re
import json
import subprocess
import platform
import time
import urllib.error
import urllib.request

# stdin は非ブロッキングで読む
hook_input = json.loads(sys.stdin.buffer.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
command = tool_input.get("command", "")

# -------------------------------------------------------------------
# ログ設定
# -------------------------------------------------------------------
# 詳細ログ（既存: コマンドごとに1ファイル）
log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/gemini-api-review"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")

# ファイルが1000件を超えたら古いものから削除
files = sorted(os.listdir(log_dir))
excess = len(files) - 1000
for f in files[: max(0, excess)]:
    os.remove(os.path.join(log_dir, f))

# サマリーログ（新規: 1ファイルに追記・500行でローテーション）
summary_log = os.path.expanduser("~/.claude/logs/gemini-review.log")
os.makedirs(os.path.dirname(summary_log), exist_ok=True)


def log_summary(decision: str, reason: str) -> None:
    """結果をサマリーログに1行で追記し、500行超えたらローテーション"""
    short_cmd = command[:80] + "..." if len(command) > 80 else command
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {decision:5s} | {short_cmd} | {
        reason
    }\n"
    with open(summary_log, "a") as f:
        f.write(line)
    # ローテーション
    with open(summary_log) as f:
        lines = f.readlines()
    if len(lines) > 500:
        with open(summary_log, "w") as f:
            f.writelines(lines[-500:])


# -------------------------------------------------------------------
# 通知
# -------------------------------------------------------------------
def notify(title: str, message: str, timeout: int = 5) -> None:
    try:
        os_name = platform.system()

        if os_name == "Darwin":
            # macOS: osascript で通知センターに送る
            subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}"',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )

        elif os_name == "Linux":
            # Linux: notify-send を使う（libnotify が必要）
            # timeout は notify-send では ミリ秒単位
            subprocess.run(
                [
                    "notify-send",
                    "--expire-time",
                    str(timeout * 1000),
                    title,
                    message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )

    except Exception:
        pass  # 通知の失敗はメイン処理に影響させない


# -------------------------------------------------------------------
# 安全なコマンドのスキップ判定
# -------------------------------------------------------------------
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


def _split_commands(cmd: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*(?:&&|\|\||[|;])\s*", cmd) if p.strip()]


def _is_safe_command(cmd: str) -> bool:
    return any(cmd == safe or cmd.startswith(safe + " ") for safe in SAFE_COMMANDS)


sub_commands = _split_commands(command)
if sub_commands and all(_is_safe_command(c) for c in sub_commands):
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Safe command, skipped Gemini review",
                }
            }
        )
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write("Gemini Output: SKIP")
    log_summary("SKIP", "safe command")
    # 安全コマンドは通知不要
    sys.exit(0)


# -------------------------------------------------------------------
# Gemini API 呼び出し
# -------------------------------------------------------------------
api_key = os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "GEMINI_API_KEY not set, skipped review",
                }
            }
        )
    )
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

model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
payload = json.dumps(
    {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 256,
            "temperature": 0.0,
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        },
    }
).encode("utf-8")

gemini_output = ""
try:
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
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
except (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
    IndexError,
    KeyError,
) as e:
    gemini_output = f"ERROR: {e}"
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": f"Gemini API error: {e}",
                }
            }
        )
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write(f"Gemini Output: {gemini_output}\n")
    log_summary("ERROR", str(e))
    notify("Gemini Review Error", "APIエラーのため確認が必要です", 10)
    sys.exit(0)


# -------------------------------------------------------------------
# 判定結果の処理
# -------------------------------------------------------------------
short_cmd = command[:60] + "..." if len(command) > 60 else command

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
    log_summary("ALLOW", "approved by Gemini")
    notify("Gemini Review", f"許可: {short_cmd}", 4)

elif "ASK" in gemini_output:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "Gemini requires confirmation: "
                    + gemini_output,
                }
            }
        )
    )
    log_summary("ASK", gemini_output.strip())
    notify("Gemini Review - 確認が必要", f"{short_cmd}", 15)

else:  # DENY
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
    log_summary("DENY", gemini_output.strip())

with open(log_file, "w") as f:
    f.write(f"Tool Name: {tool_name}\n")
    f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
    f.write(f"Gemini Output: {gemini_output}\n")

sys.exit(0)
