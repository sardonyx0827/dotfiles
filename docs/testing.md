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

Python フック（`.claude/hooks` / `.claude/mcp-servers` / `.codex/hooks`）、
`.claude/skills` 配下のスクリプト、`scripts/` の Python スクリプトは、テストが in-process で `exec` / import するため、
カバレッジを実測できます。
CI（`.github/workflows/ci.yml`）は `pytest-cov` でブランチカバレッジを測定し、
**90% を下回るとジョブが失敗**します（実測は約 97%、設定は `.coveragerc`）。
集計値は低い個別ファイルを平均で覆い隠すため、CI はファイル単位の 80% 床も別途
強制します。
テストジョブはこのほかに、`python3 scripts/gen_codex_agents.py --check`（生成対象の
`.codex/agents/*.toml` が `.claude/agents/*.md` から生成した内容と一致するか。手書きの
`codex-delegator.toml` は有効な TOML であることだけを確認）と、
`./install.sh --dry-run` のスモークテスト（`main()` 経由の配線を確認）も実行します。

> **カバレッジが測るのは Python だけです。** フックの実体はシェルにもあり
> （`lint.sh` / `auto-format.sh` / `format-then-lint.sh` / `stop-audit.sh` /
> `git-push-review.sh` / `bash-review-launcher.sh` と
> 共有ライブラリ `_hook_common.sh` / `_lint_common.sh` / `_format_common.sh` /
> `_push_review_common.sh`)、
> coverage.py はシェルを計装できません。これらはサブプロセス経由のブラック
> ボックステストで検証しており、上の数値には含まれません。

lint フックの契約は `tests/test_lint_hardening.py` が 1 項目ずつ固定します（言語ごとの振り分け表は
`tests/test_lint_and_format.py`、Stop の監査は `tests/test_stop_audit.py`、共有ヘルパーは
`tests/test_hook_common.py`）。`_lint_common.sh` の変更に合わせて、次を固定しています。

- linter が起動できなかったとき（pyenv の shim が無い、など）は指摘ではなく notice にし、exit 2 で返さない
- tsc は最寄りの tsconfig（solution 形式を含む）で走らせ、`-b` は使わない
- mypy の出力は編集したファイル分に絞る
- formatter はプロジェクトの設定を尊重し、設定が無いリポジトリでは既定のスタイルを押し付けない
- 時間制限を掛けるのは hadolint / `docker compose config` / tsc / mypy / clippy の 5 つだけで、ほかの linter は制限なしで走る。秒数は `HOOK_LINT_TIMEOUT_<ツール>` で変えられるが、正の数でない値（`00` `.` `1.2.3` `abc` `0`）は既定の秒数に戻す。時間切れで TERM を無視するプロセスは 2 秒後に KILL する
- clippy は編集したファイルに絞る。cargo が非 0 で終わり、出力のどこにも `path:line:col:` の診断が無いとき（manifest が読めない、registry に届かない等）は、何も検査していないので notice にとどめ「Lint Passed」も出さない。linter が 1 つも走らなかったファイルにも「Lint Passed」を出さない
- symlink の対象は「未導入」と誤記せず、専用のログを残して skip する
- モデルに返す lint の出力は行数とバイト数で切る（`HOOK_LINT_MAX_LINES` / `HOOK_LINT_MAX_BYTES`、Codex は合計にも `HOOK_LINT_TOTAL_MAX_*`）。正の整数でない値は既定値に戻す
- Dockerfile（hadolint）と compose ファイル（`docker compose config`）はファイル名で振り分ける

> **CI ランナーは `ubuntu-latest` 単独です（意図的）。** フックと `install.sh` は
> OS 依存分岐を差し込み口（`DEBIAN_VERSION_FILE`、OS 変数を強制する sourced 関数
> テスト等）経由で host 非依存に検証しているため、macOS/Windows ランナーを足しても
> 追加カバレッジは小さく CI コストに見合いません。macOS 実機での確認は手元の実行に
> 委ねています。

ローカルで測る場合:

```bash
pip install "pytest-cov==7.0.0"
python3 -m pytest \
  --cov=.claude/hooks --cov=.claude/mcp-servers --cov=.claude/skills \
  --cov=.codex/hooks --cov=scripts \
  --cov-report=term-missing --cov-fail-under=90
```

### 静的解析

CI は pytest に加えて `ruff`（lint / format）、`bandit`（medium 以上のセキュリティ
指摘）、`shellcheck`（全シェルスクリプト、`-x` で source 先も追跡）、`mypy`（型検査）、
`luacheck`（Neovim Lua ツリー）を実行します。mypy は設定ファイルを持たず既定値で
通ります。luacheck は Lua ツリーに対する唯一の自動検査なので **gating**（版は
1.2.0 に固定）です。

```bash
# .claude/skills/synced/ の除外は手元でだけ要る。install.sh が ~/.claude/skills を
# このディレクトリへリンクするため、Claude Code が同期するサードパーティのスキル
# (Python スクリプト入り) がここに置かれる。gitignore 済みで CI には存在しないが、
# 引数にディレクトリを渡すと ruff / mypy はその中まで検査し、無関係な指摘で落ちる。
# ruff は --exclude ではなく extend-exclude を使う (--exclude は既定の除外を置き換える。
# format は --extend-exclude を受け付けないので --config で渡す)。
ruff check --config 'extend-exclude = [".claude/skills/synced"]' .claude/hooks .claude/mcp-servers .claude/skills tests .codex/hooks scripts
ruff format --check --config 'extend-exclude = [".claude/skills/synced"]' .claude/hooks .claude/mcp-servers .claude/skills tests .codex/hooks scripts
# --no-site-packages は CI の再現に必須。CI の lint ジョブはリンター類 (ruff /
# bandit / mypy) しか入れず実行時依存 (mcp SDK 等) を入れないため、手元にだけ
# 入っている実行時依存が import を解決してしまい、ローカル緑・CI 赤という
# 食い違いが起きる。
mypy --no-site-packages --exclude '\.claude/skills/synced/' .claude/hooks .claude/mcp-servers .claude/skills scripts
# root を分けるのは同名の bash-review.py の重複を避けるため。MYPYPATH は共有
# モジュールの解決に必須（実体は .claude/hooks 側にしか無く、実行時の
# sys.path.insert を mypy は追えない）
MYPYPATH=.claude/hooks mypy --no-site-packages .codex/hooks
```

> `tests/` は mypy の対象外です。conftest とフック本体を `sys.path` 操作で読み込む
> 都合上、mypy のモジュール解決と噛み合わず、緑にするには実挙動と無関係なスタブ
> 整備が必要になるためです。

### mod（TypeScript）のテストと検査

`.claude/mods/*`（`bash-review-log` `doc-browser` `containers`）は TypeScript で、pytest の対象外です。
`tests/test_config_wiring.py` が見るのは配線の静的な検査だけなので、CI の `mods` ジョブが、
mod のテストの実行とマニフェスト・フックの検査を行う唯一の自動検査です。

- 3 つの mod それぞれに `claude plugin test .claude/mods/<mod>` と `claude plugin validate .claude/mods/<mod>` を
  実行します。1 つでも落ちればジョブが落ちます。
- Claude Code CLI は `CLAUDE_CODE_VERSION`（現在 `2.1.330`、Node.js 22 以上が要る）に固定し、版が合わなければその場で落とします。
  mod が使う API とテストキットは版ごとに動く早期アクセスの面で、validate の指摘も版で変わるため、
  新版が出た瞬間に無関係な理由で main が止まらないようにしています。上げるときは `CLAUDE_CODE_VERSION` を更新し、
  3 つの mod の test / validate が手元で通ることを確かめます。
- 認証もシークレットも要りません。test はモデルを呼ばず、validate は静的な検査なので、空の `HOME` と
  `CLAUDE_CONFIG_DIR` で走らせ、手元の設定や `~/.claude` の mod に左右されないようにしています。`--strict` は付けません。
- 型検査（`tsc`）は CI では行いません。型宣言は CLI が mod を読み込むたびに `.claude-plugin/types/` へ書き出す
  生成物で、`.gitignore` の対象（リポジトリには無い）だからです。

```bash
# 手元で CI と同じ検査をする
claude plugin test .claude/mods/containers
claude plugin validate .claude/mods/containers
```
