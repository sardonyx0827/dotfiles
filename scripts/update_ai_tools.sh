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

# codex / gemini-cli だけの npm 版。`run_if_installed npm install -g <pkg>` は
# npm 自体の有無しか見ておらず、npm はあるが <pkg> は入れたことがない機械でも
# そのまま `npm install -g` してしまう ("update" のつもりが新規インストールに
# なる)。しかも Homebrew などで既に入っている環境では、npm 管理の二重コピーが
# 生える。「入っている」を「npm 管理のグローバルパッケージである
# (`npm ls -g --depth=0 <pkg>` が成功する)」と定義し、そうでなければ何も
# せずスキップする。`npm ls` はネットワークに出ないので、オフラインでも安全に
# 判定できる。失敗を報告して次へ進む部分は run_if_installed と同じ設計。
update_npm_managed() {
  local pkg="$1"
  if ! command -v npm >/dev/null 2>&1; then
    echo "skip: npm is not installed" >&2
    return 0
  fi
  if ! npm ls -g --depth=0 "$pkg" >/dev/null 2>&1; then
    echo "skip: $pkg is not an npm-managed global; leaving it alone" >&2
    return 0
  fi
  npm install -g "$pkg@latest" ||
    echo "warning: npm install -g $pkg@latest failed (continuing)" >&2
}

echo "Updating AI command-line tools..."
echo "# claude code"
run_if_installed claude update
# `npm install -g <pkg>@latest`, not `npm update -g`: update resolves inside
# the semver range recorded at install time and will not cross a major
# version, so a new major of either CLI was skipped while the script still
# reported success. install.sh installs both with `npm install -g`, and
# @latest is the upgrade path both vendors document.
echo "# codex"
update_npm_managed @openai/codex
echo "# gemini cli"
update_npm_managed @google/gemini-cli
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
