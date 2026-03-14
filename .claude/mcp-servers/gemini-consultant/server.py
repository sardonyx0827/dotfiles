#!/usr/bin/env python3
# ~/.claude/mcp-servers/gemini-consultant/server.py

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gemini-consultant")

# -------------------------------------------------------------------
# ログ設定
# -------------------------------------------------------------------
log_file = os.path.expanduser("~/.claude/logs/gemini-consultant.log")
os.makedirs(os.path.dirname(log_file), exist_ok=True)


def log_summary(tool: str, status: str, detail: str) -> None:
    short = detail[:80] + "..." if len(detail) > 80 else detail
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {status:5s} | {tool} | {short}\n"
    with open(log_file, "a") as f:
        f.write(line)
    with open(log_file) as f:
        lines = f.readlines()
    if len(lines) > 500:
        with open(log_file, "w") as f:
            f.writelines(lines[-500:])


# -------------------------------------------------------------------
# 通知
# -------------------------------------------------------------------
def notify(title: str, message: str, timeout: int = 5) -> None:
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        pass


# -------------------------------------------------------------------
# Gemini API 呼び出し
# -------------------------------------------------------------------
def call_gemini(prompt: str, max_tokens: int = 8192) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent"
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.0,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        parts = (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        # parts が複数ある場合（思考モデルなど）もすべて結合して返す
        return "".join(p.get("text", "") for p in parts)


# -------------------------------------------------------------------
# ツール定義
# -------------------------------------------------------------------
@mcp.tool()
def consult_gemini(question: str) -> str:
    """
    設計判断・落とし穴・代替案について Gemini に相談する。
    実装を始める前に複雑な問題を整理したいときに使う。
    """
    prompt = (
        "以下のタスクについて、実装前に考慮すべき点を整理してください。\n"
        "コード実装の詳細ではなく、設計判断・落とし穴・代替案に焦点を当ててください。\n\n"
        f"タスク: {question}"
    )

    try:
        result = call_gemini(prompt)
        log_summary("consult_gemini", "OK", question)
        notify("Gemini Consultant", f"相談完了: {question[:40]}", 4)
        return result
    except ValueError as e:
        log_summary("consult_gemini", "ERROR", str(e))
        notify("Gemini Consultant", "APIキー未設定", 8)
        return f"Gemini API error: {e}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, IndexError, KeyError) as e:
        log_summary("consult_gemini", "ERROR", str(e))
        notify("Gemini Consultant", "APIエラーが発生しました", 10)
        return f"Gemini API error: {e}"


if __name__ == "__main__":
    mcp.run()
