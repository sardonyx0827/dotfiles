#!/bin/bash

# Read JSON input from stdin
input=$(cat)

# Extract information from JSON
current_dir=$(echo "$input" | jq -r '.workspace.current_dir // empty')

# Fallback to current directory if JSON parsing fails
if [ -z "$current_dir" ] || [ "$current_dir" = "null" ]; then
    current_dir=$(pwd)
fi

# Get current directory basename (equivalent to %c in zsh)
base_dir=$(basename "$current_dir")

# Check if we're in a git repository and get branch info
git_info=""
if git rev-parse --git-dir > /dev/null 2>&1; then
    branch=$(git branch --show-current 2>/dev/null)
    if [ -n "$branch" ]; then
        # Check if there are uncommitted changes
        if ! git diff-index --quiet HEAD -- 2>/dev/null; then
            git_info="($branch*)"
        else
            git_info="($branch)"
        fi
    fi
fi

# Get virtual environment info if available
venv_info=""
if [ -n "$VIRTUAL_ENV" ]; then
    venv_name=$(basename "$VIRTUAL_ENV")
    venv_info="${venv_name}! "
fi

# Get ccusage statusline info (pass the Claude Code JSON input)
usage_info=$(echo "$input" | npx ccusage@latest statusline 2>/dev/null || echo "")

# Build the status line similar to kennethreitz theme
printf "%s%s %s» %s" "${venv_info}" "${base_dir}" "${git_info}" "${usage_info}"
