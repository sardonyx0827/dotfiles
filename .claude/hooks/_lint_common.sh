#!/bin/bash
# _lint_common.sh
# lint.sh が共有する言語別 静的解析マトリクス。
#
# このファイルが唯一の実体。.codex/hooks/ 側には複製もリンクも無く、あちらの
# lint.sh が cd -P で ../../.claude/hooks を解決して直接読む。編集はここだけ。
# 契約(source 時に副作用を持たない / exit せず return する / hook_ 名前空間)は
# _hook_common.sh のヘッダを参照。hook_log を使うので、source する側は先に
# _hook_common.sh を読み込んでいること。
#
# ■ なぜ wrapper と分けるか
#
# 対象の決め方とプロトコルが .claude 版と .codex 版で本質的に違う:
#   - Claude: PostToolUse で .tool_input.file_path の 1 ファイル
#   - Codex : PostToolUse だが apply_patch のマーカーから複数ファイル。stdout は
#             構造化出力として解釈されるため exec 1>/dev/null で捨てる
# 一方「1 ファイルをどう解析するか」は完全に同一で、以前は約 230 行が
# インデント 1 段違いで両者にコピペされていた(しかも js/ts, rs, go, java, c/c++,
# rb, php はテストが 1 件も無かった)。共有するのはこの解析部分だけで、
# 対象の集約・通知・終了コードの決定は wrapper に残す。
#
# ■ 失敗の判定方法が linter ごとに違う
#
# 大半は終了コードを見るが、clippy / checkstyle / cppcheck は「終了コード 0 の
# まま stdout に指摘を書く」ため出力を grep している。ここを取り違えると
# 「問題を見つけたのに通す」という最悪の壊れ方をするので、
# tests/test_lint_and_format.py::TestLintLanguageMatrix が両者を固定している。
#
# その grep 側に落とし穴が 2 つあり、3 ツールとも踏んでいた。
#
#   1. severity の綴り。clippy の lint は warn 既定、checkstyle の
#      google_checks.xml も severity=warning で、どちらも既定設定では "error"
#      を一度も出さない。error だけを探すと恒久的に緑になる。cppcheck は
#      --enable で 4 カテゴリ要求しながら 2 つしか見ていなかった。マッチさせる
#      のは「ツールが既定で出す綴り」であって、名前から連想する綴りではない。
#   2. 出力形式そのもの。cppcheck 1.x の `(severity)` を前提にした grep は、
#      既定が `severity:` に変わった 2.x に対して何一つマッチせず、(error) の
#      指摘ごと素通ししていた。既定形式に委ねると、ツール側の変更でゲートが
#      黙って死ぬ。grep する形式は --template で自分で固定する。
#
# どちらもテストは通っていた。error 形状の出力しかスタブしておらず、コードが
# 既に正しく扱えるケースだけを固定していたためで、カバレッジは何の防波堤にも
# ならなかった。スタブはツールの実出力から起こすこと。

# _go_dir_has_analyzable_package <dir>
#
# <dir> に「go ツールチェインが実際に解析できる Go パッケージ」があるかを、
# ツールチェイン自身に 1 つの肯定的な質問で尋ねる。解析できない理由は
# 「コードが間違っている」ではないので、区別せずに指摘として報告してはいけない。
#
# ツールの出力を grep して判定してはならない。診断メッセージ本文にその語が
# 含まれる本物のエラー — 例えば
#     var x int = "matched no packages"
#   → vet: ./main.go:4:14: cannot use "matched no packages" ... as int value
# — まで「検査できなかった」と誤分類し、コンパイルの通らないファイルに対して
# lint が緑を返す。実際にそう作り込んで security review で検出された。
#
# `go env GOMOD` も使わない。GOPATH モード (GO111MODULE=off) では空を返すのに
# `go vet .` は正常に動くため、「空なら skip」にすると *全ファイルの検査が
# 黙って消える*。モジュール判定は、本当に知りたいこと (解析できるか) の
# 代理としてそもそも不正確だった。
#
# 実測した go1.27 の応答 (files = GoFiles/TestGoFiles/XTestGoFiles の個数):
#   通常のパッケージ          files=1/0/0  rc=0  vet rc=0  → 解析する
#   複数ファイルのパッケージ  files=2/0/0  rc=0  vet rc=0  → 解析する
#   GOPATH モード             files=1/0/0  rc=0  vet rc=0  → 解析する
#   build 制約で全除外        files=0/0/0  rc=0  vet rc=1  → skip
#   名前に空白を含むディレクトリ files=0/0/0 rc=0 vet rc=1 → skip (malformed import path)
#   モジュール外              files=(空)   rc=1  vet rc=1  → skip
# 下 3 つはいずれも「vet は失敗するが、それは指摘ではない」ケース。
# 単一ファイル検査だった頃はどれも rc=0 で素通りしていたので、ここを取り違えると
# `//go:build tools` や `//go:build integration` のような普通の書き方に対して
# 「存在しないエラーを直せ」とエージェントに指示することになる。
#
# cd -P で物理解決する: bash の論理 cd だと symlink 経由のパスのまま go に渡り、
# 判定と実行で見ているディレクトリがずれる。
_go_dir_has_analyzable_package() {
  command -v go >/dev/null 2>&1 || return 1
  local counts
  counts=$(cd -P "$1" 2>/dev/null &&
    go list -e -f '{{len .GoFiles}}{{len .TestGoFiles}}{{len .XTestGoFiles}}' . 2>/dev/null) ||
    return 1
  case "$counts" in
  "" | 000) return 1 ;;
  *) return 0 ;;
  esac
}

# hook_lint_file <file> <errors_var_name> <log_file>
#
# 1 ファイルを解析する。問題があれば errors_var_name で指定された変数に生の
# エラー文字列を入れて 1 を返す。無ければ空文字を入れて 0 を返す。
# 表示用の整形(ファイル名の見出しや区切り線)は呼び出し元の責務。
#
# 注意: bash は動的スコープなので、errors_var_name にこの関数内の local と同じ
# 名前(LINT_ERRORS 等)を渡すと local 側に書き込まれ、呼び出し元には何も届かない。
# 静かに壊れるため下でガードしている。

hook_lint_file() {
  local FILE_PATH="$1"
  local hook_out_var="$2"
  local hook_log_file="$3"
  local EXTENSION="${FILE_PATH##*.}"
  local BASENAME
  BASENAME=$(basename "$FILE_PATH")
  local LINT_ERRORS=""
  local PROJECT_ROOT HAS_ESLINT_CONFIG ESLINT_BIN CONFIG OUTPUT HAS_MYPY_CONFIG RELATED cfg
  local GO_PKG_DIR

  # 出力変数名がこの関数の local と衝突すると、printf -v は local を書き換えて
  # しまい呼び出し元には何も返らない。黙って通るより落とす。
  case "$hook_out_var" in
  FILE_PATH | EXTENSION | BASENAME | LINT_ERRORS | PROJECT_ROOT | OUTPUT | \
    HAS_ESLINT_CONFIG | ESLINT_BIN | CONFIG | HAS_MYPY_CONFIG | RELATED | cfg | \
    hook_out_var | hook_log_file)
    echo "hook_lint_file: output variable '$hook_out_var' collides with an internal local" >&2
    return 2
    ;;
  esac

  hook_log "$hook_log_file" "--- lint start: $FILE_PATH ---"
  echo "Linting: $BASENAME"

  case "$EXTENSION" in

  # JavaScript / TypeScript
  js | jsx | ts | tsx)
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)

    # ESLint設定ファイルの存在確認
    HAS_ESLINT_CONFIG=false
    if [ -n "$PROJECT_ROOT" ]; then
      for cfg in eslint.config.js eslint.config.mjs eslint.config.cjs .eslintrc .eslintrc.js .eslintrc.json .eslintrc.yml; do
        [ -f "$PROJECT_ROOT/$cfg" ] && HAS_ESLINT_CONFIG=true && break
      done
    fi

    ESLINT_BIN=""
    if [ -n "$PROJECT_ROOT" ] && [ -x "$PROJECT_ROOT/node_modules/.bin/eslint" ]; then
      ESLINT_BIN="$PROJECT_ROOT/node_modules/.bin/eslint"
    elif command -v eslint >/dev/null 2>&1; then
      ESLINT_BIN="eslint"
    fi

    if $HAS_ESLINT_CONFIG && [ -n "$ESLINT_BIN" ]; then
      echo "  Running ESLint ($ESLINT_BIN)..."
      if ! OUTPUT=$("$ESLINT_BIN" "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[ESLint]\n${OUTPUT}\n"
      else
        echo "  ESLint passed"
      fi
    elif ! $HAS_ESLINT_CONFIG; then
      echo "  ESLint config not found, skipping"
    else
      echo "  ESLint not found"
    fi

    # TypeScriptの型チェック（tsconfig.jsonが存在する場合のみ）
    if [[ "$EXTENSION" == "ts" || "$EXTENSION" == "tsx" ]]; then
      if [ -n "$PROJECT_ROOT" ] && [ -f "$PROJECT_ROOT/tsconfig.json" ]; then
        if command -v tsc >/dev/null 2>&1; then
          echo "  Running tsc (type check)..."
          if ! OUTPUT=$(cd "$PROJECT_ROOT" && tsc --noEmit 2>&1); then
            # 変更ファイルに関連するエラーのみ抽出。
            # -F 必須: ファイル名はパターンではなくリテラルとして照合する。素の
            # grep だと BASENAME が ERE として解釈され、Next.js の動的ルート
            # `[id].tsx` は `[id]` が文字クラス (i か d の 1 文字) になって tsc
            # 自身のエラー行に一致しない。すると RELATED が空になり、LINT_ERRORS
            # へ何も積まれないまま return 0 ——「tsc が弾いたコードでゲートが緑を
            # 返す」という、このファイル冒頭が戒めている最悪の壊れ方をする。
            # -- は BASENAME が `-` で始まる場合にオプション扱いされないため。
            RELATED=$(echo "$OUTPUT" | grep -F -- "$BASENAME")
            if [ -n "$RELATED" ]; then
              LINT_ERRORS="${LINT_ERRORS}[TypeScript]\n${RELATED}\n"
            fi
          else
            echo "  tsc passed"
          fi
        fi
      fi
    fi
    ;;

  # Python
  py)
    # ruff: flake8/isort/pyupgrade互換の高速オールインワンlinter
    if command -v ruff >/dev/null 2>&1; then
      echo "  Running ruff check..."
      if ! OUTPUT=$(ruff check "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[ruff]\n${OUTPUT}\n"
      else
        echo "  ruff passed"
      fi
    else
      echo "  ruff not found"
    fi

    # bandit: セキュリティ脆弱性の検出
    if command -v bandit >/dev/null 2>&1; then
      echo "  Running bandit (security)..."
      # -ll: 中程度以上の重大度のみ報告
      if ! OUTPUT=$(bandit -ll -q "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[bandit - security]\n${OUTPUT}\n"
      else
        echo "  bandit passed"
      fi
    else
      echo "  bandit not found"
    fi

    # mypy: 型チェック（mypy.iniかpyproject.tomlがある場合のみ）
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
    HAS_MYPY_CONFIG=false
    if [ -n "$PROJECT_ROOT" ]; then
      [ -f "$PROJECT_ROOT/mypy.ini" ] && HAS_MYPY_CONFIG=true
      [ -f "$PROJECT_ROOT/pyproject.toml" ] && grep -q "\[tool.mypy\]" "$PROJECT_ROOT/pyproject.toml" && HAS_MYPY_CONFIG=true
    fi
    if $HAS_MYPY_CONFIG && command -v mypy >/dev/null 2>&1; then
      echo "  Running mypy (type check)..."
      if ! OUTPUT=$(mypy "$FILE_PATH" --ignore-missing-imports 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[mypy]\n${OUTPUT}\n"
      else
        echo "  mypy passed"
      fi
    fi
    ;;

  # Rust
  rs)
    # clippy: Rustの公式linter（cargoが必要）
    PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
    if [ -n "$PROJECT_ROOT" ] && [ -f "$PROJECT_ROOT/Cargo.toml" ]; then
      if command -v cargo >/dev/null 2>&1; then
        echo "  Running cargo clippy..."
        OUTPUT=$(cd "$PROJECT_ROOT" && cargo clippy --quiet 2>&1)
        # clippy の lint は既定で warn レベルで、指摘があっても終了コードは 0。
        # "^error" だけでは拾えるのが rustc のコンパイルエラー(cargo check でも
        # 落ちる)だけになり、clippy を走らせる当の目的である lint が 1 件も
        # 引っかからない。cppcheck 側と同じく警告レベルも失敗として扱う。
        if echo "$OUTPUT" | grep -qE "^(error|warning)"; then
          LINT_ERRORS="${LINT_ERRORS}[clippy]\n${OUTPUT}\n"
        else
          echo "  cargo clippy passed"
        fi
      fi
    else
      echo "  Cargo.toml not found, skipping clippy"
    fi
    ;;

  # Go
  #
  # 検査対象はファイルではなく「そのファイルが属するパッケージ」= 親ディレクトリ。
  # Go は識別子をパッケージ単位でしか解決できないため、単一ファイルを渡すと
  # 同一パッケージの兄弟ファイルで定義された識別子が軒並み `undefined` になる。
  # 実測: main.go が helper.go の helper() を呼ぶ 2 ファイル構成で
  #   go vet main.go -> exit 1 "undefined: helper"
  #   go vet -C <dir> . -> exit 0
  # つまり正しいコードに対して exit 2 を返し、エージェントに存在しないエラーの
  # 修正を指示していた。さらに悪いことに、コンパイル失敗が先に立つので vet 本来の
  # 指摘 (Printf の型不一致など) は表に出ないまま握り潰されていた。
  # 1 ファイルだけのパッケージ以外、事実上あらゆる Go プロジェクトで発症する。
  #
  # 副作用として、main.go を編集すると helper.go 由来の既存の指摘も出るように
  # なる。パッケージ単位の解析としては正しい挙動だが、単一ファイル時代とは
  # 見え方が変わる点は意図的なもの。
  go)
    GO_PKG_DIR=$(dirname "$FILE_PATH")
    # 解析可能かの判定は両ツールより先に、かつ一度だけ。「ツールチェインが
    # このディレクトリを見られない」と「見た結果 指摘が出た」を、出力の文言では
    # なくこの分岐で切り分ける。staticcheck も go ツールチェインを必要とする
    # (go 不在では `err: go command required, not found` を出すだけ) ので、
    # 両方まとめてここで止める。
    if ! command -v go >/dev/null 2>&1; then
      echo "  go vet / staticcheck skipped (go not found)"
    elif ! _go_dir_has_analyzable_package "$GO_PKG_DIR"; then
      echo "  go vet / staticcheck skipped (no Go package here that the toolchain can analyse)"
    else
      echo "  Running go vet..."
      # -C はツール自身に chdir させるので、このフックの cwd を汚さない。
      if ! OUTPUT=$(go vet -C "$GO_PKG_DIR" . 2>&1); then
        # -C 配下の出力はパッケージ相対 (main.go:6:14) になるため、
        # 受け取ったエージェントがファイルへ辿れるようディレクトリを添える。
        LINT_ERRORS="${LINT_ERRORS}[go vet] (in ${GO_PKG_DIR})\n${OUTPUT}\n"
      else
        echo "  go vet passed"
      fi

      # staticcheck: go vet より高度な解析
      # staticcheck に -C は無いのでサブシェルで cd する (親シェルの cwd は不変)。
      # -P は判定側と揃えるため必須: 論理 cd だと symlink 経由のディレクトリで
      # staticcheck が `warning: "." matched no packages` を出して rc=0 で返り、
      # 何も解析していないのに緑になる。
      if command -v staticcheck >/dev/null 2>&1; then
        echo "  Running staticcheck..."
        if ! OUTPUT=$(cd -P "$GO_PKG_DIR" && staticcheck . 2>&1); then
          LINT_ERRORS="${LINT_ERRORS}[staticcheck] (in ${GO_PKG_DIR})\n${OUTPUT}\n"
        else
          echo "  staticcheck passed"
        fi
      else
        echo "  staticcheck not found (optional)"
      fi
    fi
    ;;

  # Java
  java)
    # checkstyle: コーディング規約チェック
    if command -v checkstyle >/dev/null 2>&1; then
      echo "  Running checkstyle..."
      # プロジェクトにcheckstyle.xmlがあればそれを使用、なければGoogle規約。
      # 既定値は "google" ではなく "/google_checks.xml"。前者は解決できず
      # (`Could not find config XML file 'google'.` / exit 255)、この分岐は
      # 一度も検査していなかった。jar 同梱の設定は classpath リソースなので
      # 先頭スラッシュ付きで指定する。test_checkstyle_default_config_resolves。
      PROJECT_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)
      CONFIG="/google_checks.xml"
      [ -f "$PROJECT_ROOT/checkstyle.xml" ] && CONFIG="$PROJECT_ROOT/checkstyle.xml"
      # 終了コード非 0 は「起動できなかった」(設定が読めない・不正な引数)。
      # その旨のメッセージに [ERROR]/[WARN] は乗らないので、下の grep だけでは
      # 指摘ゼロと区別が付かず緑を返してしまう。ゲートが黙って無効化される
      # 経路なので、出力を見る前に潰す。
      if ! OUTPUT=$(checkstyle -c "$CONFIG" "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[checkstyle]\n${OUTPUT}\n"
      # 既定の google_checks.xml は severity=warning なので、指摘は [WARN] で
      # 出力され [ERROR] は現れない。[ERROR] だけを見ると、既定設定で拾えた
      # 指摘を 1 件残らず素通しすることになる。[INFO] は指摘ではないので除く。
      elif echo "$OUTPUT" | grep -qE "\[ERROR\]|\[WARN\]"; then
        LINT_ERRORS="${LINT_ERRORS}[checkstyle]\n${OUTPUT}\n"
      else
        echo "  checkstyle passed"
      fi
    else
      echo "  checkstyle not found"
    fi
    ;;

  # C / C++
  c | cpp | cc | cxx | h | hpp)
    if command -v cppcheck >/dev/null 2>&1; then
      echo "  Running cppcheck..."
      # --template で出力形式を固定する。cppcheck 1.x の既定は
      # `[file:line]: (severity) message` だったが 2.x で
      # `file:line:col: severity: message` に変わっており、括弧付きの綴りを
      # 探す下の grep は modern cppcheck に対して何一つマッチしていなかった
      # (= (error) の指摘ごと素通しし、常に緑)。既定に委ねるのが事故の原因な
      # ので、grep する形式はこちらで決める。tests の
      # test_cppcheck_template_and_matcher_agree がこの固定を守る。
      # --template-location も必須。--template だけ渡すと、指摘に付く補足
      # (nullPointer なら「Assignment 'p=0', assigned value is 0」等、修正に
      # 一番効く情報) が丸ごと落ちる。matcher には掛からない綴りなので誤検知
      # にはならず、LINT_ERRORS の文脈だけが増える。
      #
      # 終了コード非 0 は「起動できなかった」(不正な引数・ファイルが開けない)。
      # cppcheck は指摘があっても 0 で終わるので、非 0 は指摘ではなく事故。
      # そのメッセージ (`cppcheck: error: unrecognized command line option ...`)
      # は下の grep にかからないため、潰さないと「起動失敗 = 緑」になる。
      # 上の --template を打ち間違えた瞬間にゲートが黙って死ぬ経路でもある。
      if ! OUTPUT=$(cppcheck --enable=warning,style,performance,portability \
        --suppress=missingInclude \
        --template='{file}:{line}:{column}: ({severity}) {message} [{id}]' \
        --template-location='{file}:{line}:{column}: note: {info}' \
        "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[cppcheck]\n${OUTPUT}\n"
      # --enable で有効化した 4 カテゴリを漏れなく拾う。error/warning しか見て
      # いなかったため style/performance/portability の指摘は捨てられていた。
      # note 行は上の --template-location 側の綴りで出るため、ここには掛からない
      # (指摘本体だけが判定に効く)。
      elif echo "$OUTPUT" | grep -qE "\((error|warning|style|performance|portability)\)"; then
        LINT_ERRORS="${LINT_ERRORS}[cppcheck]\n${OUTPUT}\n"
      else
        echo "  cppcheck passed"
      fi
    else
      echo "  cppcheck not found"
    fi
    ;;

  # Ruby
  rb)
    # rubocop: フォーマットとlintを兼ねる（auto-format.shでは--auto-correctのみ実行済み）
    # ここでは修正できなかった残存エラーをCodexにフィードバック
    if command -v rubocop >/dev/null 2>&1; then
      echo "  Running rubocop (lint only)..."
      if ! OUTPUT=$(rubocop --no-color "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[rubocop]\n${OUTPUT}\n"
      else
        echo "  rubocop passed"
      fi
    else
      echo "  rubocop not found"
    fi
    ;;

  # PHP
  php)
    # phpstan: 型推論ベースの高精度静的解析
    if command -v phpstan >/dev/null 2>&1; then
      echo "  Running phpstan..."
      if ! OUTPUT=$(phpstan analyse "$FILE_PATH" --no-progress 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[phpstan]\n${OUTPUT}\n"
      else
        echo "  phpstan passed"
      fi
    # php -l: 構文チェックのみ（フォールバック）
    elif command -v php >/dev/null 2>&1; then
      echo "  Running php -l (syntax check)..."
      if ! OUTPUT=$(php -l "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[php syntax]\n${OUTPUT}\n"
      else
        echo "  php syntax OK"
      fi
    else
      echo "  phpstan / php not found"
    fi
    ;;

  # Shell scripts
  sh | bash)
    if command -v shellcheck >/dev/null 2>&1; then
      echo "  Running shellcheck..."
      if ! OUTPUT=$(shellcheck -x -P SCRIPTDIR "$FILE_PATH" 2>&1); then
        LINT_ERRORS="${LINT_ERRORS}[shellcheck]\n${OUTPUT}\n"
      else
        echo "  shellcheck passed"
      fi
    else
      echo "  shellcheck not found"
    fi
    ;;

  *)
    echo "  No linter configured for .$EXTENSION files"
    ;;
  esac

  if [ -n "$LINT_ERRORS" ]; then
    hook_log "$hook_log_file" "FAILED: $BASENAME"
    # `%b` ではなく `\n` だけを実改行へ戻す。区切りとしてリテラル `\n` を積む以上
    # 復元は要るが、`%b` は `\c` も解釈し「以降の出力を打ち切る」ため、linter が
    # 引用したソース行に `\c` が含まれるとそこから先の指摘が丸ごと消える。
    printf '%s' "${LINT_ERRORS//\\n/$'\n'}" >>"$hook_log_file"
    printf -v "$hook_out_var" '%s' "$LINT_ERRORS"
    return 1
  fi

  hook_log "$hook_log_file" "PASSED: $BASENAME"
  echo "All lint checks passed for $BASENAME"
  printf -v "$hook_out_var" '%s' ""
  return 0
}
