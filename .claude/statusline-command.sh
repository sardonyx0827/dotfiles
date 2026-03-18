#!/bin/bash
# =============================================================================
# Claude Code Status Line - Rose Pine Theme
# =============================================================================
# セットアップ:
#   chmod +x ~/.claude/statusline.sh
#   ~/.claude/settings.json に以下を追加:
#   {
#     "statusLine": {
#       "type": "command",
#       "command": "~/.claude/statusline.sh",
#       "padding": 0
#     }
#   }
# =============================================================================

# --- Rose Pine カラーパレット (truecolor) ---
RESET='\033[0m'
PINE='\033[38;2;49;116;143m'    # #31748f  ディレクトリ
FOAM='\033[38;2;156;207;216m'   # #9ccfd8  コンテキスト低 / git staged
IRIS='\033[38;2;196;167;231m'   # #c4a7e7  git ブランチ
ROSE='\033[38;2;235;188;186m'   # #ebbcba  モデル名
GOLD='\033[38;2;246;193;119m'   # #f6c177  コンテキスト中 / git unstaged / コスト
LOVE='\033[38;2;235;111;146m'   # #eb6f92  コンテキスト高
MUTED='\033[38;2;110;106;134m'  # #6e6a86  区切り / git untracked
SUBTLE='\033[38;2;144;140;170m' # #908caa  補助テキスト

# --- JSON 入力を読み込む ---
input=$(cat)

# --- フィールド取得 ---
cwd=$(echo "$input" | jq -r '.workspace.current_dir      // ""')
ctx_pct_raw=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
ctx_pct=$(echo "$ctx_pct_raw" | cut -d. -f1)
model_raw=$(echo "$input" | jq -r '.model.display_name          // ""')
session_id=$(echo "$input" | jq -r '.session_id                 // ""')

# --- ディレクトリ表示 (~/... 短縮) ---
short_dir="${cwd/#$HOME/\~}"
# 3階層より深い場合は末尾2階層のみ表示
dir_display=$(echo "$short_dir" | awk -F'/' '{
    if (NF > 3) printf "~/../%s/%s", $(NF-1), $NF
    else        print $0
}')

# --- コンテキスト使用率の色 ---
if [ "$ctx_pct" -lt 50 ]; then
  ctx_color="$FOAM"
elif [ "$ctx_pct" -lt 80 ]; then
  ctx_color="$GOLD"
else
  ctx_color="$LOVE"
fi

# --- モデル名の短縮 ---
# "claude-sonnet-4-6" -> "Sonnet 4.6" など見やすく整形
model_display=$(echo "$model_raw" |
  sed 's/claude-//I' |
  sed 's/-/ /g' |
  awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2)); print}')
[ -z "$model_display" ] && model_display="--"

# --- Git 情報 ---
git_info=""
if git -C "$cwd" rev-parse --git-dir >/dev/null 2>&1; then
  branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short HEAD 2>/dev/null ||
    git -C "$cwd" rev-parse --short HEAD 2>/dev/null)

  staged=$(git -C "$cwd" --no-optional-locks diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')
  unstaged=$(git -C "$cwd" --no-optional-locks diff --numstat 2>/dev/null | wc -l | tr -d ' ')
  untracked=$(git -C "$cwd" --no-optional-locks ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')

  indicators=""
  [ "$staged" -gt 0 ] && indicators+="${FOAM}+${staged}${RESET}"
  [ "$unstaged" -gt 0 ] && indicators+="${GOLD}~${unstaged}${RESET}"
  [ "$untracked" -gt 0 ] && indicators+="${MUTED}?${untracked}${RESET}"

  if [ -n "$indicators" ]; then
    git_info="${IRIS}${branch}${RESET} ${indicators}"
  else
    git_info="${IRIS}${branch}${RESET}"
  fi
fi

# --- セッションコスト (JSONL から集計) ---
cost_str="-.----"
if [ -n "$session_id" ]; then
  # セッション ID を含む JSONL ファイルを探す
  jsonl_file=$(grep -rl "\"$session_id\"" ~/.claude/projects/ 2>/dev/null | head -1)
  if [ -n "$jsonl_file" ]; then
    cost=$(jq -r 'select(.costUSD != null) | .costUSD' "$jsonl_file" 2>/dev/null |
      awk '{s+=$1} END {printf "%.4f", s+0}')
    cost_str="\$${cost}"
  fi
fi

# --- 区切り文字 ---
SEP="${MUTED} | ${RESET}"

# --- 出力を組み立て ---
parts=()
parts+=("${PINE}${dir_display}${RESET}")
[ -n "$git_info" ] && parts+=("${git_info}")
parts+=("${ROSE}${model_display}${RESET}")
parts+=("${ctx_color}ctx ${ctx_pct}%${RESET}")
parts+=("${GOLD}${cost_str}${RESET}")

# 配列を区切り文字で結合
result=""
for i in "${!parts[@]}"; do
  [ "$i" -gt 0 ] && result+="$SEP"
  result+="${parts[$i]}"
done

printf "%b\n" "$result"
