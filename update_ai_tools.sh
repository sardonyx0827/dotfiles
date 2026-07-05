#!/usr/bin/env bash
echo "Updating AI command-line tools..."
echo "# claude code"
claude update
echo "# codex"
npm update -g @openai/codex
echo "# gemini cli"
npm upgrade -g @google/gemini-cli
echo "# copilot cli"
copilot update

echo "Updated versions:"
echo "# claude code"
claude --version
echo "# codex"
codex --version
echo "# gemini cli"
gemini --version
echo "# copilot cli"
copilot --version
