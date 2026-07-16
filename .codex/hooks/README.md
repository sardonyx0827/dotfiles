# Codex Hooks System

Codex CLI は Claude Code とほぼ同じ形のフック機構を持つ(`PreToolUse` /
`PostToolUse` / `Stop` / `SessionStart` / `SubagentStop` / `PreCompact` /
`PostCompact` / `UserPromptSubmit`)。ツール名も Claude 互換にマップされる
ため、`matcher` はそのまま流用できる。

| Codex の内部ツール | matcher に書く名前       |
| ------------------ | ------------------------ |
| `shell`            | `Bash`                   |
| `apply_patch`      | `Write\|Edit\|MultiEdit` |

設定は `~/.codex/hooks.json`(このリポジトリの `.codex/hooks.json.template`
が原本)。スクリプト実体は `~/.codex/hooks/*` から本リポジトリへのシンボリック
リンク。

## Current Hooks (in ~/.codex/hooks.json)

### PreToolUse

Matcher: `Bash`(実行順)

1. **bash-review** (`hooks/bash-review.py`):
   Bash コマンドを実行前にレビューする。ログは `~/.codex/logs/bash-review.log`。
2. **git-push-review** (`hooks/git-push-review.sh`):
   `git push` を検知し、対象コミットのサマリを添えてブロックする。

### PostToolUse

Matcher: `Write|Edit|MultiEdit`

- **lint** (`hooks/lint.sh`):
  変更されたファイルを静的解析し、問題があれば exit 2 + stderr で Codex に
  返して自動修正を促す。ログは `~/.codex/logs/lint.log`。

### Stop

実行順:

1. **auto-format** (`hooks/auto-format.sh`):
   作業ツリーの変更ファイルにフォーマッターを実行する。ログは
   `~/.codex/logs/format.log`。
   **Claude 版と違い PostToolUse ではなく Stop に置いている**(理由は後述)。
2. **stop-audit** (`hooks/stop-audit.sh`):
   デバッグ文(`console.log` / `debugger` / `breakpoint()` 等)の残留を監査し、
   見つかれば exit 2 でブロックする。`stop_hook_active` で無限ループを防ぐ。

## なぜ `.claude/hooks` と別実装なのか

同じ処理でも Claude と Codex で**要求される作法が違う**ため、単一のスクリプトに
統合できない。`.claude/hooks` 側がロジックの上流(正)で、`.codex/hooks` はそれを
Codex 向けに適応させた版。ロジックを変えるときは両方に反映する必要がある。

差分の軸は 4 つ:

### 1. 返却プロトコル

Codex は `permissionDecision: "ask"` や `{decision: "block"}` を解釈しない。
ブロックしたい場合は **exit 2 + stderr** を使う。

|                 | `.claude` 版                                                 | `.codex` 版     |
| --------------- | ------------------------------------------------------------ | --------------- |
| git-push-review | `{hookSpecificOutput: {permissionDecision: "ask"}}` + exit 0 | stderr + exit 2 |
| stop-audit      | `{decision: "block"}` + exit 0                               | stderr + exit 2 |

### 2. stdout は JSON として解釈される

Codex はフックの stdout を構造化出力として読むため、**平文を出すとフック自体が
失敗扱い**になる(実行はされるが `hook: PostToolUse Failed` と表示される)。
Claude は平文を許容する。そのため `.codex` 版は `exec 1>/dev/null` で進捗表示を
捨て、記録はログファイルにだけ残す。

### 3. 入力 payload の形

Claude の `Write`/`Edit` は `.tool_input.file_path` に単一のファイルパスを入れて
渡すが、Codex の `apply_patch` は**このキーを持たない**。実測した payload は:

```
tool_name  = "apply_patch"
tool_input = { "command": "*** Begin Patch\n*** Update File: sample.js\n..." }
```

ファイルパスは `command`(パッチ本文)の中の `*** Update File:` 等のマーカーから
しか取れない。

### 4. ログ出力先

`.codex` 版は `~/.codex/logs/` に出す(Claude と混在させない)。ruff 対応など
フォーマッター/リンターの構成にも差がある。

## なぜ auto-format だけ Stop なのか

**フォーマッターが Codex の編集そのものを壊すため。**

Codex の `apply_patch` は「このファイルは今こういう内容のはず」という前提で差分を
当てる。編集直後(PostToolUse)にフォーマッターがファイルを書き換えると、後続の
パッチが

```
apply_patch verification failed: Failed to find expected lines in <file>
```

で失敗する。Codex はリトライし、最終的にシェル経由でファイルを書くため
`Write|Edit|MultiEdit` にも掛からず、**結局未整形のまま残る**。

- 1 回の編集で終わるターンは無事だが、**複数回編集するターンで再現する**
- 実測: `DONE (formatted)` の 69 秒後にファイルが上書きされ、整形が消えた

そこで「Codex がもう編集しないと分かっている時点」= `Stop` まで整形を遅らせて
いる。lint はファイルを変更しないためこの競合が無く、PostToolUse のままで即時
フィードバックできる。

副作用として、Stop の payload にはファイルパスが無いため、対象は git の作業ツリー
差分から決めている(`stop-audit.sh` と同じ方針)。このため**作業中の無関係な変更も
整形対象になりうる**。

## 重要: hooks.json を変更したら再承認が必要

Codex にはフックの信頼ゲートがあり、**`hooks.json` を変更すると、対話セッションで
承認するまで全フックが黙って無効になる**。ヘッドレス実行(`codex exec` / MCP 経由)
では警告も出ず、ただフックが動かなくなる。

変更後は一度 `codex` を対話起動し、`Review hooks` → `Trust all and continue` で
承認すること。

**変更直後は動いているように見える**点に注意。app-server が旧設定をキャッシュ
しているため、実測では発火数が 8 → 4 → 0 と数分かけて減衰した。「変更したが
まだ動いているから大丈夫」と判断すると、後から黙って全停止する。

なおスクリプト実体(`hooks/*.sh`)の変更や、シンボリックリンクの張り替えでは再承認は
不要(`hooks.json` の内容が変わらないため)。整形ロジックを直すだけなら承認は要らず、
フックの掛け先(イベントや matcher)を変えるときだけ必要になる。

なおスクリプト実体(`hooks/*.sh`)の変更や、シンボリックリンクの張り替えでは再承認は
不要(`hooks.json` の内容が変わらないため)。

## インストール

`hooks.json` は `__HOME__` プレースホルダを含むテンプレートから生成する:

```bash
sed "s|__HOME__|$HOME|g" .codex/hooks.json.template > ~/.codex/hooks.json
```

スクリプトは `~/.codex/hooks/` から本リポジトリの `.codex/hooks/` へシンボリック
リンクを張る。

`.codex/hooks/` にある次の 4 つは、いずれも `.claude/hooks/` 側の実体への相対
シンボリックリンク (Claude 版と完全に同一のロジックなので、実体を 1 つにして
ドリフトを構造的に起こらなくしてある):

| ファイル                 | 中身                           | 読み込む側                             |
| ------------------------ | ------------------------------ | -------------------------------------- |
| `_bash_review_common.py` | 判定ロジック・レビュー呼び出し | `bash-review.py` が import             |
| `_hook_common.sh`        | `hook_log` / `hook_notify`     | `lint.sh` / `auto-format.sh` が source |
| `_lint_common.sh`        | 言語別 静的解析マトリクス      | `lint.sh` が source                    |
| `_format_common.sh`      | 言語別 フォーマッタマトリクス  | `auto-format.sh` が source             |

`~/.codex/hooks/` 経由で起動しても解決される。Python は sys.path[0] を realpath
で解決してリポジトリ内に入り、bash の `source` は OS がリンクを透過的に辿る。

`tests/test_hook_sync.py` が 4 つすべてについてリンクの形 (symlink であること /
相対であること / 実体に解決すること / 実際にロードできること) を固定する。
`core.symlinks=false` の clone ではリンクがテキストファイルとして展開されて壊れる
ため、各 wrapper は読み込み後に関数の存在を確認して落ちる (INSTALL_PLATFORM.md 参照)。
