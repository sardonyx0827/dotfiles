# /Users/sardonyx0827/work/github/dotfiles/.claude/hooks/pre_tool_use_codex_guard.py
import json
import os
import sys
import time
import urllib.error
import urllib.request

hook_input = json.loads(sys.stdin.buffer.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})

# Codex MCP以外はスキップ (ツール名プレフィックスは環境に合わせて調整)
CODEX_TOOL_PREFIXES = ("mcp__codex__", "codex__")
if not any(tool_name.startswith(p) for p in CODEX_TOOL_PREFIXES):
    sys.exit(0)

# ログディレクトリ
log_dir = "/tmp/claude_hooks/logs/PreToolUse/Mcp/pre_tool_use_codex_guard"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"codex_guard_{int(time.time())}.log")

files = sorted(os.listdir(log_dir))
excess = len(files) - 1000
for f in files[: max(0, excess)]:
    os.remove(os.path.join(log_dir, f))

# tool_inputから指示テキストを取り出す


def extract_instruction(ti: dict) -> str:
    for key in ("prompt", "message", "instruction", "task", "query", "input"):
        if key in ti:
            return str(ti[key])
    return json.dumps(ti, ensure_ascii=False)


instruction = extract_instruction(tool_input)

if not instruction.strip():
    sys.exit(0)


# Gemini API 呼び出し
api_key = os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    print(
        json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "GEMINI_API_KEY not set, skipped review",
            }
        })
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write("Gemini Output: SKIP (no API key)\n")
    sys.exit(0)

prompt = (
    "以下の指示をCodexというコーディングエージェントに渡した場合、"
    "Codexがファイルの新規作成・編集・削除などの「実装作業」を自律的に開始する可能性を判定してください。\n\n"
    f"ツール: {tool_name}\n"
    f"指示内容: {json.dumps(tool_input, ensure_ascii=False)}\n\n"
    '実装作業を開始しそうなら "DENY: 理由"、'
    '判断が難しい場合は "ASK"、'
    '調査・分析・提案のみで実装しなさそうなら "ALLOW" とだけ答えてください。'
)

model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

url = f"https://generativelanguage.googleapis.com/v1beta/models/{
    model}:generateContent"
payload = json.dumps({
    "contents": [{"parts": [{"text": prompt}]}],
    "generationConfig": {
        "maxOutputTokens": 256,
        "temperature": 0.0,
        "thinkingConfig": {"thinkingLevel": "MINIMAL"},
    },
}).encode("utf-8")

gemini_output = ""
try:
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": api_key},
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
        json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"Gemini API error: {e}",
            }
        })
    )
    with open(log_file, "w") as f:
        f.write(f"Tool Name: {tool_name}\n")
        f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
        f.write(f"Gemini Output: {gemini_output}\n")
    sys.exit(0)

if "ALLOW" in gemini_output:
    print(
        json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Gemini reviewed and approved",
            }
        })
    )
elif "ASK" in gemini_output:
    print(
        json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "Gemini requires confirmation: " + gemini_output,
            }
        })
    )
else:
    print(
        json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": gemini_output,
            }
        })
    )

with open(log_file, "w") as f:
    f.write(f"Tool Name: {tool_name}\n")
    f.write(f"Tool Input: {json.dumps(tool_input, ensure_ascii=False)}\n")
    f.write(f"Gemini Output: {gemini_output}\n")

sys.exit(0)
