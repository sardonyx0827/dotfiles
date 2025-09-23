#!/bin/bash
set -e

# Claude Code HooksのPostToolUse用自動フォーマットスクリプト
# 標準入力からJSON形式のデータを受け取り、ファイルパスを抽出してフォーマットを実行

# JSONからファイルパスを取得
FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)

# ファイルパスが取得できない場合は終了
if [ -z "$FILE_PATH" ]; then
    echo "No file path found in input" >&2
    exit 0
fi

# ファイルが存在しない場合は終了
if [ ! -f "$FILE_PATH" ]; then
    echo "File does not exist: $FILE_PATH" >&2
    exit 0
fi

# ファイル拡張子を取得
EXTENSION="${FILE_PATH##*.}"
BASENAME=$(basename "$FILE_PATH")

echo "🔧 Auto-formatting: $BASENAME"

# 拡張子に応じてフォーマッターを実行
case "$EXTENSION" in
    # JavaScript/TypeScript/JSON/CSS/HTML/Markdown
    js|jsx|ts|tsx|json|css|scss|less|html|htm|md|yaml|yml)
        if command -v prettier >/dev/null 2>&1; then
            echo "  → Running Prettier..."
            prettier --write "$FILE_PATH" 2>/dev/null && echo "  ✅ Prettier completed"
        else
            echo "  ⚠️  Prettier not found, skipping JS/TS formatting"
        fi
        ;;

    # Python
    py)
        # Black (フォーマッター)
        if command -v black >/dev/null 2>&1; then
            echo "  → Running Black..."
            black "$FILE_PATH" 2>/dev/null && echo "  ✅ Black completed"
        elif command -v autopep8 >/dev/null 2>&1; then
            echo "  → Running autopep8..."
            autopep8 --in-place "$FILE_PATH" 2>/dev/null && echo "  ✅ autopep8 completed"
        else
            echo "  ⚠️  No Python formatter found (black/autopep8)"
        fi

        # isort (import文のソート)
        if command -v isort >/dev/null 2>&1; then
            echo "  → Running isort..."
            isort "$FILE_PATH" 2>/dev/null && echo "  ✅ isort completed"
        fi
        ;;

    # Rust
    rs)
        if command -v rustfmt >/dev/null 2>&1; then
            echo "  → Running rustfmt..."
            rustfmt "$FILE_PATH" 2>/dev/null && echo "  ✅ rustfmt completed"
        elif command -v cargo >/dev/null 2>&1; then
            echo "  → Running cargo fmt..."
            (cd "$(dirname "$FILE_PATH")" && cargo fmt 2>/dev/null) && echo "  ✅ cargo fmt completed"
        else
            echo "  ⚠️  No Rust formatter found (rustfmt/cargo)"
        fi
        ;;

    # Go
    go)
        if command -v gofmt >/dev/null 2>&1; then
            echo "  → Running gofmt..."
            gofmt -w "$FILE_PATH" 2>/dev/null && echo "  ✅ gofmt completed"
        fi
        if command -v goimports >/dev/null 2>&1; then
            echo "  → Running goimports..."
            goimports -w "$FILE_PATH" 2>/dev/null && echo "  ✅ goimports completed"
        fi
        ;;

    # Java
    java)
        if command -v google-java-format >/dev/null 2>&1; then
            echo "  → Running google-java-format..."
            google-java-format --replace "$FILE_PATH" 2>/dev/null && echo "  ✅ google-java-format completed"
        else
            echo "  ⚠️  google-java-format not found"
        fi
        ;;

    # C/C++
    c|cpp|cc|cxx|h|hpp)
        if command -v clang-format >/dev/null 2>&1; then
            echo "  → Running clang-format..."
            clang-format -i "$FILE_PATH" 2>/dev/null && echo "  ✅ clang-format completed"
        else
            echo "  ⚠️  clang-format not found"
        fi
        ;;

    # Ruby
    rb)
        if command -v rubocop >/dev/null 2>&1; then
            echo "  → Running rubocop..."
            rubocop --auto-correct "$FILE_PATH" 2>/dev/null && echo "  ✅ rubocop completed"
        else
            echo "  ⚠️  rubocop not found"
        fi
        ;;

    # PHP
    php)
        if command -v php-cs-fixer >/dev/null 2>&1; then
            echo "  → Running php-cs-fixer..."
            php-cs-fixer fix "$FILE_PATH" 2>/dev/null && echo "  ✅ php-cs-fixer completed"
        else
            echo "  ⚠️  php-cs-fixer not found"
        fi
        ;;

    # Shell scripts
    sh|bash)
        if command -v shfmt >/dev/null 2>&1; then
            echo "  → Running shfmt..."
            shfmt -w "$FILE_PATH" 2>/dev/null && echo "  ✅ shfmt completed"
        else
            echo "  ⚠️  shfmt not found"
        fi
        ;;

    *)
        echo "  ℹ️  No formatter configured for .$EXTENSION files"
        ;;
esac

echo "🎉 Formatting completed for $BASENAME"
exit 0
