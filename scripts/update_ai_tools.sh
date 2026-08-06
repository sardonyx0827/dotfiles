#!/usr/bin/env bash
set -euo pipefail

# Runs "$@" only when the tool exists, so one missing CLI does not
# abort the remaining updates under set -e.
#
# 失敗も同じ扱いにする。以前はここが不在だけを見ていたため、インストール済みの
# ツールの更新が非ゼロで終わると終了ステータスがそのまま伝播し、set -e が
# スクリプト全体を落としていた (最初の claude update が転ぶと codex / gemini /
# copilot の更新もバージョン表示も丸ごと実行されない)。npm レジストリの一時障害の
# 方が CLI 不在より起きやすく、しかも「残りが黙って飛ぶ」ので気付きにくい。
# 一括更新のスクリプトとしては、1 つの失敗を報告して次へ進むのが正しい。
run_if_installed() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "skip: $1 is not installed" >&2
    return 0
  fi
  "$@" || echo "warning: $* failed (continuing)" >&2
}

echo "Updating AI command-line tools..."
echo "# claude code"
run_if_installed claude update
echo "# codex"
run_if_installed npm update -g @openai/codex
echo "# gemini cli"
run_if_installed npm upgrade -g @google/gemini-cli
echo "# copilot cli"
run_if_installed copilot update

echo "Updated versions:"
echo "# claude code"
run_if_installed claude --version
echo "# codex"
run_if_installed codex --version
echo "# gemini cli"
run_if_installed gemini --version
echo "# copilot cli"
run_if_installed copilot --version
