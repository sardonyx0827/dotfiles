#!/usr/bin/env python3
# ~/.claude/mcp-servers/gemini-consultant/server.py
import json
import os
import subprocess
import platform
import time
import urllib.error
import urllib.request
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gemini-consultant")

# -------------------------------------------------------------------
# モデル設定
# -------------------------------------------------------------------
# 用途別にモデルを分離
# 深い推論が必要な設計相談 → Pro
# 局所的・高頻度なチェック → Flash
DEEP_MODEL = os.environ.get("GEMINI_PRO_MODEL", "gemini-3.1-pro-preview")
LIGHT_MODEL = os.environ.get("GEMINI_FLASH_MODEL", "gemini-3-flash")

# -------------------------------------------------------------------
# ログ設定
# -------------------------------------------------------------------
log_file = os.path.expanduser("~/.claude/logs/gemini-consultant.log")
os.makedirs(os.path.dirname(log_file), exist_ok=True)

MAX_LOG_LINES = 500


def _append_log(lines: list[str]) -> None:
    """ログファイルに書き込み、上限を超えた分を古い順に削除する。"""
    with open(log_file, "a", encoding="utf-8") as f:
        f.writelines(lines)

    with open(log_file, encoding="utf-8") as f:
        all_lines = f.readlines()

    if len(all_lines) > MAX_LOG_LINES:
        with open(log_file, "w", encoding="utf-8") as f:
            f.writelines(all_lines[-MAX_LOG_LINES:])


def log_entry(tool: str, status: str, prompt: str, response: str = "") -> None:
    """
    プロンプト全文と Gemini レスポンス全文をログに記録する。

    フォーマット:
        --- [timestamp] STATUS | tool ---
        [PROMPT]
        ...プロンプト全文...
        [RESPONSE]
        ...レスポンス全文（省略なし）...
        ---
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    separator = "-" * 60 + "\n"

    lines = [
        separator,
        f"[{timestamp}] {status:5s} | {tool}\n",
        "[PROMPT]\n",
        prompt + "\n",
    ]

    if response:
        lines += [
            "[RESPONSE]\n",
            response + "\n",
        ]

    lines.append(separator)

    _append_log(lines)


# -------------------------------------------------------------------
# 通知
# -------------------------------------------------------------------
def notify(title: str, message: str, timeout: int = 5) -> None:
    try:
        os_name = platform.system()

        if os_name == "Darwin":
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

        elif os_name == "Windows":
            from win10toast import ToastNotifier

            toaster = ToastNotifier()
            toaster.show_toast(title, message, duration=timeout)

    except Exception:
        pass


# -------------------------------------------------------------------
# Gemini API 呼び出し（リトライ付き）
# -------------------------------------------------------------------
def call_gemini(
    prompt: str,
    max_tokens: int = 8192,
    model: str | None = None,
    max_retries: int = 3,
) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    resolved_model = model or DEEP_MODEL

    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.0,
            },
        }
    ).encode("utf-8")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{resolved_model}"
        f":generateContent"
    )

    last_error: Exception | None = None

    for attempt in range(max_retries):
        # リトライのたびに Request を再生成する
        # （urllib.request.Request は一度消費されるため）
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                parts = (
                    body.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                )
                return "".join(p.get("text", "") for p in parts)
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
            wait = 2**attempt  # 指数バックオフ: 1秒 → 2秒 → 4秒
            time.sleep(wait)

    raise last_error  # type: ignore[misc]


# -------------------------------------------------------------------
# ツール定義
# -------------------------------------------------------------------
@mcp.tool()
def consult_gemini(question: str) -> str:
    """
    アーキテクチャ・設計判断・トレードオフについて Gemini Pro に深く相談する。
    影響範囲が広い・抽象度が高い判断に使う。
    実装を始める前に複雑な問題を整理したいときに使う。
    """
    prompt = (
        "以下のタスクについて、実装前に考慮すべき点を整理してください。\n"
        "コード実装の詳細ではなく、設計判断・落とし穴・代替案に焦点を当ててください。\n"
        "不確かな情報や知識の範囲外の事項については、その旨を明示してください。\n"
        "推測で回答せず、確信が持てない場合は「要確認」として提示してください。\n"
        f"\nタスク: {question}"
    )

    try:
        result = call_gemini(prompt, model=DEEP_MODEL)
        log_entry("consult_gemini", "OK", prompt, result)
        notify("Gemini Consultant", f"設計相談完了: {question[:40]}", 4)
        return result
    except ValueError as e:
        log_entry("consult_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIキー未設定", 8)
        return f"Gemini API error: {e}"
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        IndexError,
        KeyError,
    ) as e:
        log_entry("consult_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIエラーが発生しました", 10)
        return f"Gemini API error: {e}"


@mcp.tool()
def review_gemini(question: str) -> str:
    """
    実装方針や具体的な抜け漏れを Gemini Flash で素早く確認する。
    局所的・繰り返し頻度が高いチェックに使う。
    """
    prompt = (
        "以下の実装方針について、抜け漏れや問題点を確認してください。\n"
        "具体的な実装の観点から、見落としやすい点・注意点を指摘してください。\n"
        "不確かな情報や知識の範囲外の事項については、その旨を明示してください。\n"
        "推測で回答せず、確信が持てない場合は「要確認」として提示してください。\n"
        f"\n確認対象: {question}"
    )

    try:
        result = call_gemini(prompt, model=LIGHT_MODEL)
        log_entry("review_gemini", "OK", prompt, result)
        notify("Gemini Consultant", f"レビュー完了: {question[:40]}", 4)
        return result
    except ValueError as e:
        log_entry("review_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIキー未設定", 8)
        return f"Gemini API error: {e}"
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        IndexError,
        KeyError,
    ) as e:
        log_entry("review_gemini", "ERROR", prompt, str(e))
        notify("Gemini Consultant", "APIエラーが発生しました", 10)
        return f"Gemini API error: {e}"


if __name__ == "__main__":
    mcp.run()
