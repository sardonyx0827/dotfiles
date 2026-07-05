# ~/.claude/hooks/bash-review.py
# 一次処理: Gemini API (高スループット)
# 二次処理: Gemini が ASK/DENY と判定した場合のみ Codex で再確認
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# フック入力のJSONをstdinから読み込む
hook_input = json.loads(sys.stdin.buffer.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
command = tool_input.get("command", "")

# ログ設定
# 詳細ログ (コマンドごとに1ファイル)
log_dir = "/tmp/claude_hooks/logs/PreToolUse/Bash/bash-review"  # nosec B108
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"bash_cmd_{int(time.time())}.log")

# ファイルが1000件を超えたら古いものから削除
files = sorted(os.listdir(log_dir))
excess = len(files) - 1000
for f in files[: max(0, excess)]:
    os.remove(os.path.join(log_dir, f))

# サマリーログ (1ファイルに追記・500行でローテーション)
summary_log = os.path.expanduser("~/.claude/logs/bash-review.log")
os.makedirs(os.path.dirname(summary_log), exist_ok=True)


def log_summary(decision: str, stage: str, reason: str) -> None:
    """結果をサマリーログに1行で追記し、500行超えたらローテーション"""
    short_cmd = command[:80] + "..." if len(command) > 80 else command
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {decision:5s} | {stage:8s} | {short_cmd} | {reason}\n"
    with open(summary_log, "a") as f:
        f.write(line)
    with open(summary_log) as f:
        lines = f.readlines()
    if len(lines) > 500:
        with open(summary_log, "w") as f:
            f.writelines(lines[-500:])


def write_detail_log(entries: dict) -> None:
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        for key, value in entries.items():
            f.write(f"{key}: {value}\n")


# 通知
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
            # printenv 経由で値を取得して AppleScript 注入と system attribute の
            # MacRoman 解釈による日本語文字化けの両方を回避する
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
            from win10toast import ToastNotifier

            toaster = ToastNotifier()
            toaster.show_toast(safe_title, safe_message, duration=timeout)

    except Exception:
        pass  # 通知の失敗はメイン処理に影響させない


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


# 安全/危険コマンドの判定
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

DENY_COMMANDS = [
    "curl",
    "wget",
    "nc",
    "ssh",
    "shred",
    "dd",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf .",
]

# Safe-skip is intentionally conservative: these tokens can hide execution
# or writes inside an otherwise harmless-looking command prefix.
COMPLEX_SHELL_SYNTAX = re.compile(r"[\r\n`<>]|\$\(|(?<!&)&(?!&)")


def _split_commands(cmd: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*(?:&&|\|\||[|;])\s*", cmd) if p.strip()]


def _is_safe_command(cmd: str) -> bool:
    return any(cmd == safe or cmd.startswith(safe + " ") for safe in SAFE_COMMANDS)


def _can_skip_review(cmd: str) -> bool:
    return not COMPLEX_SHELL_SYNTAX.search(cmd) and _is_safe_command(cmd)


def _is_deny_command(cmd: str) -> tuple[bool, str]:
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


# 一次処理: Gemini API
PROMPT = (
    "以下のツール呼び出しが安全かどうかを判断してください。\n"
    f"ツール: {tool_name}\n"
    f"内容: {json.dumps(tool_input, ensure_ascii=False)}\n\n"
    '安全なら "ALLOW"、危険なら "DENY: 理由"、確認が必要なら"ASK" とだけ答えてください。'
)

api_key = os.environ.get("GEMINI_API_KEY", "")
gemini_model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
gemini_fallback_model = os.environ.get("GEMINI_FLASH_MODEL", "gemini-flash-latest")


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


_API_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
    IndexError,
    KeyError,
)


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


# 二次処理: Codex
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
