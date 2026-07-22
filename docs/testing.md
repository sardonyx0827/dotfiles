# テストと静的解析

このドキュメントは [README.md](../README.md) から分離した、テスト / 静的解析 / CI の詳細です。

## テスト

`tests/` に pytest ベースのテストスイートがあります。Python フック
(`.claude/hooks/*.py` など) は stdin・外部 API・通知をモックした上で直接実行し、
シェルスクリプト (`install.sh`、各フック、statusline など) はサブプロセスとして
一時 HOME + スタブ PATH 環境で実行します。実通知・実 API コール・実ログへの
書き込みは発生しません。

```bash
# 全テストを実行
python3 -m pytest

# 特定ファイルのみ
python3 -m pytest tests/test_bash_review.py -v
```

Python フック（`.claude/hooks` / `.claude/mcp-servers` / `.codex/hooks`）と
`scripts/` の Python スクリプトは、テストが in-process で `exec` / import するため、
カバレッジを実測できます。
CI（`.github/workflows/ci.yml`）は `pytest-cov` でブランチカバレッジを測定し、
**90% を下回るとジョブが失敗**します（実測は約 97%、設定は `.coveragerc`）。
集計値は低い個別ファイルを平均で覆い隠すため、CI はファイル単位の 80% 床も別途
強制します。

> **カバレッジが測るのは Python だけです。** フックの実体はシェルにもあり
> （`lint.sh` / `auto-format.sh` / `stop-audit.sh` / `git-push-review.sh` と
> 共有ライブラリ `_hook_common.sh` / `_lint_common.sh` / `_format_common.sh`)、
> coverage.py はシェルを計装できません。これらはサブプロセス経由のブラック
> ボックステストで検証しており、上の数値には含まれません。

> **CI ランナーは `ubuntu-latest` 単独です（意図的）。** フックと `install.sh` は
> OS 依存分岐を差し込み口（`DEBIAN_VERSION_FILE`、OS 変数を強制する sourced 関数
> テスト等）経由で host 非依存に検証しているため、macOS/Windows ランナーを足しても
> 追加カバレッジは小さく CI コストに見合いません。macOS 実機での確認は手元の実行に
> 委ねています。

ローカルで測る場合:

```bash
pip install "pytest-cov==7.0.0"
python3 -m pytest \
  --cov=.claude/hooks --cov=.claude/mcp-servers --cov=.codex/hooks --cov=scripts \
  --cov-report=term-missing --cov-fail-under=90
```

### 静的解析

CI は pytest に加えて `ruff`（lint / format）、`bandit`（medium 以上のセキュリティ
指摘）、`shellcheck`（全シェルスクリプト、`-x` で source 先も追跡）、`mypy`（型検査）
を実行します。mypy は設定ファイルを持たず既定値で通ります。

```bash
ruff check .claude/hooks .claude/mcp-servers tests .codex/hooks scripts
# --no-site-packages は CI の再現に必須。CI の lint ジョブはリンター類 (ruff /
# bandit / mypy) しか入れず実行時依存 (mcp SDK 等) を入れないため、手元にだけ
# 入っている実行時依存が import を解決してしまい、ローカル緑・CI 赤という
# 食い違いが起きる。
mypy --no-site-packages .claude/hooks .claude/mcp-servers scripts
mypy --no-site-packages .codex/hooks   # 同名モジュールの重複を避け root を分ける
```

> `tests/` は mypy の対象外です。conftest とフック本体を `sys.path` 操作で読み込む
> 都合上、mypy のモジュール解決と噛み合わず、緑にするには実挙動と無関係なスタブ
> 整備が必要になるためです。
