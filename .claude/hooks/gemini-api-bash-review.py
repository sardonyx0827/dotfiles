# ~.claude/hooks/gemini-api-review.py
import json
import os
import platform
import re
import subprocess
import sys
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
log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/gemini-api-review"  # nosec B108
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")

# ファイルが1000件を超えたら古いものから削除
files = sorted(os.listdir(log_dir))
excess = len(files) - 1000
for f in files[: max(0, excess)]:
    os.remove(os.path.join(log_dir, f))

# サマリーログ（新規: 1ファイルに追記・500行でローテーション）
summary_log = os.path.expanduser("~/.claude/logs/gemini-bash-review.log")
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
def _sanitize_notify(text: str, limit: int = 200) -> str:
    """通知用に制御文字を除去し長さを制限する"""
    cleaned = "".join(ch for ch in text if ch.isprintable())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


def notify(title: str, message: str, timeout: int = 5) -> None:
    try:
        os_name = platform.system()
        safe_title = _sanitize_notify(title, limit=100)
        safe_message = _sanitize_notify(message, limit=200)

        if os_name == "Darwin":
            # macOS: printenv 経由で値を取得して AppleScript 注入と
            # system attribute の MacRoman 解釈による日本語文字化けを回避する
            script = (
                'set titleText to do shell script "printenv CLAUDE_NOTIFY_TITLE || true"\n'
                'set msgText to do shell script "printenv CLAUDE_NOTIFY_MESSAGE || true"\n'
                "display notification msgText with title titleText"
            )
            subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                env={
                    **os.environ,
                    "CLAUDE_NOTIFY_TITLE": safe_title,
                    "CLAUDE_NOTIFY_MESSAGE": safe_message,
                },
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
                    safe_title,
                    safe_message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )

        elif os_name == "Windows":
            # Windows: Toast通知を送る（Windows 10以降）
            from win10toast import ToastNotifier

            toaster = ToastNotifier()
            toaster.show_toast(safe_title, safe_message, duration=timeout)

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

# -------------------------------------------------------------------
# 危険なコマンドの拒否判定
# -------------------------------------------------------------------
DENY_COMMANDS = ["curl", "wget", "nc", "ssh", "shred", "dd", "rm -rf /", "rm -rf ~", "rm -rf ."]


def _split_commands(cmd: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*(?:&&|\|\||[|;])\s*", cmd) if p.strip()]


def _is_safe_command(cmd: str) -> bool:
    return any(cmd == safe or cmd.startswith(safe + " ") for safe in SAFE_COMMANDS)


def _is_deny_command(cmd: str) -> tuple[bool, str]:
    """危険コマンドに一致するか判定し、(一致したか, 一致したコマンド名) を返す"""
    for deny in DENY_COMMANDS:
        if cmd == deny or cmd.startswith(deny + " "):
            return True, deny
    return False, ""


sub_commands = _split_commands(command)

# --- 危険コマンドの即時拒否 ---
for sub_cmd in sub_commands:
    matched, deny_name = _is_deny_command(sub_cmd)
    if matched:
        reason = f"Blocked dangerous command: '{deny_name}'"
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write("Gemini Output: DENY (pre-blocked)\n")
        log_summary("DENY", reason)
        notify("Gemini Review - 拒否", f"危険コマンド検出: {deny_name}", 8)
        sys.exit(0)

# --- 安全コマンドのスキップ ---
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

model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
model_fallback = os.environ.get("GEMINI_FLASH_MODEL", "gemini-flash-latest")


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


_API_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
    IndexError,
    KeyError,
)

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
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": f"Gemini API error (fallback model failed): {fallback_err}",
                    }
                }
            )
        )
        with open(log_file, "w") as f:
            f.write(f"Tool Name: {tool_name}\n")
            f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
            f.write(f"Gemini Output: {gemini_output}\n")
        log_summary("ERROR", f"primary={primary_err}, fallback={fallback_err}")
        notify("Gemini Review Error", "APIエラーのため確認が必要です", 8)
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
    notify("Gemini Review - 確認が必要", f"{short_cmd}", 8)

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
    notify("Gemini Review", f"拒否: {short_cmd}", 8)

with open(log_file, "w") as f:
    f.write(f"Tool Name: {tool_name}\n")
    f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
    f.write(f"Gemini Output: {gemini_output}\n")

sys.exit(0)
