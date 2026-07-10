#!/usr/bin/env bash
set -euo pipefail

# Runs "$@" only when the tool exists, so one missing CLI does not
# abort the remaining updates under set -e.
run_if_installed() {
  if command -v "$1" >/dev/null 2>&1; then
    "$@"
  else
    echo "skip: $1 is not installed" >&2
  fi
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
