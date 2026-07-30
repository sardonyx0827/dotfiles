#!/usr/bin/env bash

# 新規プロジェクトの雛形を作る。
#
# 作るのは「無いものだけ」で、既にあるファイル / ディレクトリには一切触れない。
# 何度実行しても安全 (冪等) であることが前提の設計で、だからこそ新規ディレクトリ
# だけでなく、途中まで手で作った既存プロジェクトにも同じコマンドを向けられる。
#
# .zshrc の np() はこのスクリプトを呼んでから作成先へ cd する。子プロセスは親
# シェルの cwd を変えられないので、作成先の絶対パスは $NEW_PROJECT_DIR_FILE で
# 指定されたファイル経由で np() に返す。ドライラン / 失敗時に書かないことが、
# そのまま「np() が cd しない」の意味になる。オプション解釈をこちら側に一本化
# するための口でもあるので、np() 側にフラグの知識を持たせないこと。
set -euo pipefail

# 作成するディレクトリと、README に書く説明。"名前:説明" を 1 本の配列で持つ
# のは、macOS の system bash 3.2 に連想配列が無いため。ここを足せば README の
# 構成表も追随する。
SCAFFOLD_DIRS=(
  "docs:ドキュメント置き場"
  "assets:画像などのアセット置き場"
)
DEFAULT_BRANCH="main"

dry_run=0
target=""

usage() {
  cat <<'EOF'
Usage: new_project.sh [-n|--dry-run] [--] [DIR]

新規プロジェクトの雛形を DIR (省略時はカレントディレクトリ) に用意する。
既存のファイルやディレクトリは上書きしないので、何度実行しても安全。

  docs/ assets/  作成する (空のときだけ .gitkeep を置く)
  README.md      雛形を作成する
  git            リポジトリがまだ無ければ init する (既定ブランチ: main)

Options:
  -n, --dry-run  何も作らず、作る予定のものだけを表示する
  -h, --help     このヘルプを表示する
EOF
}

warn() { echo "new_project.sh: $1" >&2; }

die() {
  warn "$1"
  usage >&2
  exit 2
}

# 実行した / する予定の 1 行を出す。ラベルは create か skip。
say() {
  local prefix=""
  if ((dry_run)); then prefix="[dry-run] "; fi
  printf '%s%-7s %s\n' "$prefix" "$1" "$2"
}

set_target() {
  if [[ -n "$target" ]]; then
    die "too many arguments: '$1'"
  fi
  target="$1"
}

# ディレクトリが空か。`ls` の出力を読む代わりにグロブで見るのは、変わった名前の
# ファイルでも壊れないようにするため。存在しないディレクトリは「空」と答える
# (ドライランではまだ作られていない)。
is_empty_dir() {
  local path="$1" entry
  [[ -d "$path" ]] || return 0
  for entry in "$path"/* "$path"/.[!.]* "$path"/..?*; do
    if [[ -e "$entry" || -L "$entry" ]]; then
      return 1
    fi
  done
  return 0
}

# 既存ファイルは絶対に上書きしない。rel は $root からの相対パス (表示にも使う)。
# -L も見るのは、-L 無しだとリンク切れのシンボリックリンクが「無い」と判定され、
# そこへ書き込むとリンクの先 (プロジェクトの外) にファイルが生まれてしまうため。
create_file() {
  local rel="$1" content="$2" path="$root/$1"
  if [[ -e "$path" || -L "$path" ]]; then
    say skip "$rel (already exists)"
    return 0
  fi
  say create "$rel"
  if ((dry_run == 0)); then
    if [[ -n "$content" ]]; then
      printf '%s\n' "$content" >"$path"
    else
      : >"$path"
    fi
  fi
}

scaffold_dir() {
  local name="$1" path="$root/$1"
  # シンボリックリンクには触らない。リンク切れなら書き抜け / mkdir 失敗になり、
  # 生きたリンクならプロジェクトの外に .gitkeep を置くことになる。どちらも困る。
  if [[ -L "$path" ]]; then
    warn "'$name' is a symlink; skipped"
    return 0
  fi
  # 同名のファイルが居座っている場合。消さずに諦め、残りの雛形は続ける。
  if [[ -e "$path" && ! -d "$path" ]]; then
    warn "'$name' exists and is not a directory; skipped"
    return 0
  fi
  if [[ -d "$path" ]]; then
    say skip "$name/ (already exists)"
  else
    say create "$name/"
    if ((dry_run == 0)); then mkdir -p -- "$path"; fi
  fi
  # 空ディレクトリは git が追跡しないので .gitkeep を置く。中身があるなら
  # 既に追跡できるので、置くとゴミが増えるだけ。
  if is_empty_dir "$path"; then
    create_file "$name/.gitkeep" ""
  fi
}

readme_body() {
  local entry
  printf '# %s\n\n## 概要\n\n<!-- TODO: このプロジェクトの目的を書く -->\n' "$project"
  printf '\n## ディレクトリ構成\n\n'
  for entry in "${SCAFFOLD_DIRS[@]}"; do
    # Markdown のバッククォートは二重引用符で書く。単引用符に入れると
    # コマンド置換と読み違えられる (SC2016)。
    printf -- "- \`%s/\` — %s\n" "${entry%%:*}" "${entry#*:}"
  done
}

init_git() {
  local toplevel home_abs
  if ! command -v git >/dev/null 2>&1; then
    warn "git not found; skipped repository initialization"
    return 0
  fi
  if [[ -d "$root/.git" ]]; then
    say skip "git init (already a repository)"
    return 0
  fi
  # 引数無しの np を $HOME でうっかり叩いたとき、ホーム全体がリポジトリに
  # なるのだけは避ける。ディレクトリと README は害が無いので作ってよい。
  home_abs="$(cd "${HOME:-/nonexistent}" 2>/dev/null && pwd -P || printf '%s' "${HOME:-}")"
  if [[ -n "$home_abs" && "$abs" == "$home_abs" ]]; then
    warn "refusing to run git init in \$HOME"
    return 0
  fi
  # 既存リポジトリの中に入れ子のリポジトリを作るのは、まず事故。$probe から
  # 見るのは、ドライランでは $root がまだ存在せず git -C が使えないため。
  if toplevel="$(git -C "$probe" rev-parse --show-toplevel 2>/dev/null)"; then
    say skip "git init (already inside the work tree of $toplevel)"
    return 0
  fi
  say create "git repository (branch: $DEFAULT_BRANCH)"
  if ((dry_run)); then return 0; fi
  # `git init -b` は git 2.28 以降。この .gitconfig は init.defaultBranch を
  # 設定していないので、古い git に素の init を任せると master になってしまう。
  # 拾えるように、init してから HEAD を張り替えるフォールバックを持つ。
  if ! git -C "$root" init -q -b "$DEFAULT_BRANCH" 2>/dev/null; then
    git -C "$root" init -q
    git -C "$root" symbolic-ref HEAD "refs/heads/$DEFAULT_BRANCH"
  fi
}

end_of_opts=0
while (($#)); do
  if ((end_of_opts)); then
    set_target "$1"
  else
    case "$1" in
    -n | --dry-run) dry_run=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    --) end_of_opts=1 ;;
    -*) die "unknown option: '$1'" ;;
    *) set_target "$1" ;;
    esac
  fi
  shift
done

root="${target:-.}"

if [[ -e "$root" && ! -d "$root" ]]; then
  warn "'$root' exists and is not a directory"
  exit 1
fi

# $root のうち実在する最も近い祖先。ドライランでは $root 自体がまだ無いので、
# cd / git -C の足場としてこちらを使う。パスは全て `--` 越しに渡す:
# `--` はこのスクリプト自身がオプション終端として提供している以上、その先の
# `-foo` を外部コマンドのオプションパーサに横取りされては意味がない。
probe="$root"
while [[ ! -d "$probe" ]]; do
  probe="$(dirname -- "$probe")"
done

# 実在部分は cd で解決し、まだ無い残りを継ぎ足して絶対パスにする
# (realpath -m や readlink -f は BSD userland に無い)。
rest="${root#"$probe"}"

# 実在部分の `..` は上の cd が物理解決するが、まだ無い部分に残った `..` は
# 解決しようがない (subdir/newdir/.. の .. が何を指すかは newdir を作るまで
# 決まらない)。単なる文字列連結では嘘の絶対パスになるので、ここで断る。
case "/$rest/" in
*/../*)
  warn "cannot resolve '..' below a directory that does not exist yet: '$root'"
  exit 1
  ;;
esac

abs="$(cd -- "$probe" && pwd -P)${rest:+/${rest#/}}"
project="$(basename -- "$abs")"

if [[ -d "$root" ]]; then
  printf 'project: %s\n' "$abs"
else
  printf 'project: %s (new)\n' "$abs"
  if ((dry_run == 0)); then mkdir -p -- "$root"; fi
fi

for dir_entry in "${SCAFFOLD_DIRS[@]}"; do
  scaffold_dir "${dir_entry%%:*}"
done

create_file "README.md" "$(readme_body)"

init_git

# np() が cd する先。ドライランでは書かない = 移動しない。
if [[ -n "${NEW_PROJECT_DIR_FILE:-}" ]] && ((dry_run == 0)); then
  printf '%s\n' "$abs" >"$NEW_PROJECT_DIR_FILE"
fi
