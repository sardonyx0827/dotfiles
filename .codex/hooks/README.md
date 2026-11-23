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

1. **bash-review** (`hooks/bash-review.py`、起動は `hooks/bash-review-launcher.sh` 経由):
   Bash コマンドを実行前にレビューする。ログは `~/.codex/logs/bash-review.log`。
   hooks.json は素の `python3` ではなくランチャーを呼ぶ: python3 の不在・
   フックファイルの欠落・本体のクラッシュ (0/2 以外の終了) はフックのエラー
   扱い = fail-open になるため、ランチャーがそれらを exit 2 + stderr の
   ブロックへ変換する (正常系の exit 0 / exit 2 は素通し)。
   ランチャーはフックを root 所有のシステムの python3 で `-I -S` で動かし、PATH・
   pyenv・`.python-version` には影響されない (候補と条件・外す環境変数は Claude 側と
   同じで、`.claude/hooks/README.md` の bash-review の項)。条件を満たす候補が無ければ
   「信頼できるシステム python3 が無い」でブロックし、PATH の python3 には落ちない。
   hooks.json の command はどれも PATH の `bash` ではなく `/bin/bash -p` で起動し、
   フックの中から起動する bash も `/bin/bash -p` にする (`-p` で `$BASH_ENV` / `$ENV`
   のファイルと、環境から入る関数を読まない。最上位の `-p` は子の bash に届かない。
   理由は `.claude/hooks/README.md` の bash-review の項)。
   ハーネスへの書き込みの判定とサンドボックスの段は、コマンドが走るディレクトリを cwd
   として使う。シェルツールの `tool_input.workdir` があればそれ (相対パスはセッションの
   cwd と join する)。`~` を含む・空・NUL・文字列でない workdir は「分からない」
   (`HARNESS_CWD_UNKNOWN`) として渡し、相対の書き込みはブロック (人間が実行)、csb の
   実体は確かめない。workdir が無い (null を含む) ときはセッションの cwd のまま。
   Codex は Bash のコマンドを `zsh -lc` で動かすので、コマンドのたびに `.zshenv` と
   `.zprofile` を読む。シェルの層 (関数・alias・PATH) は csb の実体の確認の外にあり、
   その対策はシェルの起動ファイルの保護 (Claude の settings の Edit の ask と、Bash の
   ハーネスのパス) で、Codex のセッションにも同じファイルとして効く。
   解析そのものの前に入力サイズを確かめ、`tool_input` の JSON が 32 KiB を
   超えるか、コマンド置換 (`$(`・`<(`・`>(`・バッククォート 1 組) が 32 個を
   超えるときは解析も外部レビューもせず exit 2 でブロックする。入れ子の `$(` の
   解析コストが二乗で伸び、フックが時間切れになるのを防ぐため。
   サンドボックス CLI (`csb`) の段は Claude 側と同じ (`.claude/hooks/README.md` の
   **The sandbox CLI stage**)。csb の実体の確認 (`~/.local/bin/csb` が固定のラッパーか)
   も、確認できないときに要約の子プロセスを起動しないことも同じ。Codex には ask も
   deny の区別も無いので、Claude で ask になるもの (実体を確かめた境界の
   create / net on / apply / image build、迂回形、実体を確かめられない正規形) も、deny になるもの
   (実体を確かめられない境界、要約が表示枠に収まらない境界) も、exit 2 + stderr の
   ブロックで返し、人間が実行する。静的な許可は exit 0 (無出力)。
   Codex の PreToolUse フックは、時間切れになると fail-open になる (コマンドはそのまま
   走る)。この機械の Codex 0.171.0 (tag `rust-v0.171.0`) には、失敗で止める設定が無い:
   handler の `onFailure` は main の a1afa122 (#62831) で入ったもので、このリリースには
   含まれない。0.171.0 の `HookHandlerConfig::Command` に `on_failure` は無く、
   `pre_tool_use.rs` の失敗の分岐は状態を Failed にするだけで止めない。handler の未知の
   キーは無視される (`deny_unknown_fields` は最上位の `HooksFile` だけ) ので、書いても
   効かないまま fail-open になる。そのためこの template には `onFailure` を書いていない
   (`onFailure` の入った版になったら、bash-review と git-push-review にだけ `"block"` を
   足す)。`timeout` を書かないと 600 秒になる
   (openai/codex の `codex-rs/hooks/src/engine/discovery.rs` の
   `normalize_command_hook`: `timeout_sec.unwrap_or(600)`。キーは
   `codex-rs/protocol/src/hooks.rs` の `#[serde(default, rename = "timeout")]`)。
   短くすると、遅いレビューが打ち切られて fail-open になるだけなので、bash-review と
   git-push-review の `timeout` は既定と同じ 600 秒を明示する (Claude 側の 180 秒より
   長い。test_config_wiring で固定)。フック自身の時間 (サンドボックスの段は 5 秒、外部
   レビューは Gemini 30 秒 + Codex 60 秒) はその内側に収まる。
   残る穴 (同じ uid のモデルによる状態の偽造、PTY は人の証明にならない、難読化の
   残余、シェルの層とスナップショット、ユーザーが書ける場所の実行体 (Homebrew の git・
   Docker.app・`~/.npm-global` の codex など)、開発用 repo とライブのハーネスの分離) は
   `.claude/hooks/README.md` の同じ節に書いてある。
2. **git-push-review** (`hooks/git-push-review.sh`):
   `git push` を検知し、対象コミットのサマリを添えてブロックする。サマリは
   push が実際に走るリポジトリから取る(`git -C <dir>` と、push より前の
   リテラルな `cd <dir>` の両方を追う)。変数・`cd -`・`~user`・`pushd` /
   `popd`・サブシェル・`cd` を含むコマンド中のヒアドキュメント (`<<`) やコメント (`#`)・
   複数の `cd`・オプションや余分な引数付きの `cd`・`..` を含む行き先・
   クォートされた `~`・実在しない行き先・コマンド中のタブ/改行以外の制御文字
   (DEL を含む)・`cd` の行き先や `-C` の値に含まれる制御文字・
   `--git-dir` / `--work-tree` / `GIT_DIR` / `GIT_WORK_TREE` の指定・
   フックの環境かコマンド中に `CDPATH` があるときの素の相対 `cd` などで
   確実に決められないときは、サマリの代わりに注記を出す(別リポジトリの
   サマリは無いより悪い)。要約先は push 自身の `-C` だけで決め、同じコマンド中の
   別の git 呼び出しの `-C` には引きずられない。
   サマリの前に push の宛先(remote と refspec)を出し、`--force` / `-f` /
   `--force-with-lease` / `--force-if-includes` / `+` refspec / `--delete` / `-d` /
   `:dst` refspec / `--mirror` / `--all` / `--tags` / `--prune` は `WARNING` 行で
   強調する。refspec の source が現在ブランチでない場合や `--all` 等で送る ref が
   変わる場合は、下のコミット一覧が実際に送られるものとは限らないと注記する。
   引数を信頼できる形で読めないとき(eval / `sh -c` / `watch` / `--exec` の中、
   展開を含む refspec、未知のオプション、4000 文字超、制御文字)は推測せず注記に
   する。URL の userinfo は伏せる。
   `git … push` の語順を取らない push (`git subtree push`、`git send-pack` /
   `git http-push`、alias 経由の `git -c alias.p=push p` / `git config alias.ps push`)
   も、要約の代わりに注記を付けてブロックする。4000 文字超の経路でもバックスラッシュと
   行継続を畳むので、`git pu\sh` は隠れない(詳細は `.claude/hooks/README.md`)。
   検知は、コメント(語頭の `#`)・ヒアドキュメント本文・`$'...'` の中の偶発的な
   アポストロフィが偽のクォートを開いて後続行の push を隠すことがない。終端行の
   無い `<<`(算術シフトの `$((1<<2))` 等)はヒアドキュメントとみなさず、コードで
   ある本文の行継続(`\` + 改行)はシェルと同じく畳む。空白を含まないクォート語
   (`git "push"` / `"git" push`)も 1 語として扱い(`then` / `{` / `!` の後や
   `timeout 5` 等のラッパー越しでも同じ)、文字列を
   実行する `git submodule foreach` / `git rebase --exec`(`-x`)/ `watch` の中の
   push も検知する(`submodule foreach` は各サブモジュールの中で走るので要約は
   諦めて注記にする)。
   インタプリタのコードの中の push もブロックする(`python3 -c "subprocess.run(['git','push'])"`、
   `node -e`、`ruby -e`、`perl -e`、`deno eval`、`bun -e`、標準入力から渡すコード =
   ヒアドキュメント・ヒアストリング・パイプ)。argv のリスト(`['git', '-C', d, 'push']`。
   オプションの値は名前・リテラル・`os.path.join(a, b)` のような呼び出し・`sys.argv[1]` の
   ような添字・`+` の連結でよく、`+` でつないだリストも数える)と文字列(`os.system("git push")`)の
   両方を読む。`git` と `push` の間にクォートしたサブコマンドがあるもの(`['git','stash','push']`)
   は push ではない。コードが `chdir()` や `-C` で移りうるので、要約の代わりに
   「対象のリポジトリを決められない」注記を付ける。ヒアドキュメントの本文も含めてコマンド全体を
   読む(インタプリタに渡すヒアドキュメントは実際に push できる経路なので外せない)ため、そうした
   コードを引用するだけのヒアドキュメント(`git commit -m "$(cat <<'EOF' … EOF)"`、
   `git commit -F - <<'EOF'`、`cat > notes.md <<'EOF'`)もブロックする。この余分な確認は
   受け入れている(push を隠すことはない)。
   検知できない形(そのまま確認なしで走る)は `.claude/hooks/README.md` の Known gaps にある。
   インタプリタのコードの中で組み立てた push(`'pu' + 'sh'`、`[*GIT, 'push']`、
   `cmd.append('push')`)、括弧が 3 段入れ子のオプションの値、複数行の文字列の中で `#` から
   始まる行(インタプリタの検査はそれをコメントとして落とし、そこに載った push も一緒に落ちる)、
   awk の `system()`、TypeScript のランナー(`tsx`、`ts-node -e`)、コードに続けて書いた
   オプション(`python3 -cprint(1)`)など。

### PostToolUse

Matcher: `Write|Edit|MultiEdit`

- **lint** (`hooks/lint.sh`):
  変更されたファイルを静的解析し、問題があれば exit 2 + stderr で Codex に
  返して自動修正を促す。ログは `~/.codex/logs/lint.log`。
  実際に linter が走って指摘が無かったファイルだけを「Lint Passed」に数える(`hook_lint_file` が残す
  `HOOK_LINT_RAN` が 0 のファイル = 対象外拡張子・未導入・起動失敗・時間切れ・compose の環境エラー・
  cargo が非 0 で終わり `path:line:col:` の診断が 1 つも無い clippy (notice) などは数えない)。通知は、数えたファイルが
  1 件ならそのファイル名を出し、2 件以上なら「N 件」とだけ出す。

  linter の呼び出しに時間制限を掛けるのは hadolint / `docker compose config` / tsc / mypy / clippy の 5 つだけで、
  ほかは制限なしで走る。既定は `HOOK_LINT_TIMEOUT_<NAME>`(秒)で上書きできる: `HADOLINT` 20、`COMPOSE` 15、`TSC` 45、
  `MYPY` 30、`CLIPPY` 45。正の数でない値(`0` / `0.0` を含む)はそのツールの既定の秒数に戻す(「0 で制限なし」は無い)。
  時間切れは TERM から始め、TERM を無視するプロセスは 2 秒後に KILL する。
  時間切れはログに `NOTICE` を残すだけで、指摘にはしない。モデルに返す出力は 1 ファイルあたり
  `HOOK_LINT_MAX_LINES`(既定 200 行)と `HOOK_LINT_MAX_BYTES`(既定 16384 バイト)で切り、末尾に切った旨を足す。
  1 回の `apply_patch` で最大 50 ファイルを検査するので、合計にも `HOOK_LINT_TOTAL_MAX_LINES`(既定 400 行)と
  `HOOK_LINT_TOTAL_MAX_BYTES`(既定 32768 バイト)を掛ける。この 4 つは、正の整数でない値を既定値に戻す。

  Dockerfile と compose ファイルは拡張子ではなくファイル名で決める。`Dockerfile`、`Dockerfile.*`、`*.Dockerfile`、
  `Containerfile` は hadolint に(`*.dockerignore` は Dockerfile ではない)、`compose.y(a)ml`、`compose.*.y(a)ml` と
  `docker-compose` 系は `docker compose -f <ファイル> config -q --no-interpolate`(ファイルのあるディレクトリで実行)に回す。
  hadolint は cwd の設定しか読まないので、編集ファイルから git root まで遡って最も近い `.hadolint.yaml` /
  `.hadolint.yml` を `--config` で渡す。環境の問題(`env_file` が無い、デーモンに繋がらない、compose プラグインが無い、
  認証ヘルパー、context)は指摘ではなく notice にする。base 以外(`compose.override.yml` など)は単体では不完全なので、
  「image も build も無い」「未定義の service・volume・network・secret・config」は指摘にしない。`hadolint` か `docker` が無いときは検査せず、
  「Lint Passed」も出さない。

### Stop

実行順:

1. **auto-format** (`hooks/auto-format.sh`):
   作業ツリーの変更ファイルにフォーマッターを実行する。ログは
   `~/.codex/logs/format.log`。
   formatter はプロジェクトの設定を尊重する(作業中のあらゆるリポジトリで走るので、他人のコードを既定のスタイルで書き換えない)。
   prettier は、編集したファイルから git root まで遡って設定(`.prettierrc*`、`prettier.config.*`、`package.json` の
   `"prettier"` キー。devDependencies に prettier があるだけでは設定とみなさない)が見つかったときだけ走る。
   clang-format は `.clang-format` / `_clang-format` が見つかったときだけ走る。shfmt は、`.editorconfig` が
   シェルスクリプトのインデント(`indent_style` / `indent_size` / `tab_width`)を決めているときはスタイルフラグを渡さず、
   無いときは `-i 2` で走る。symlink の対象は整形しない(formatter が symlink の置き換えを拒否するうえ、
   リンク先が別のリポジトリのファイルかもしれないため)。整形しなかった理由は `format.log` の `DONE` 行に残る。
   **Claude 版と違い PostToolUse ではなく Stop に置いている**(理由は後述)。
   `EDITOR_AI_ONESHOT` が立っていれば何もしない — 対象を git 差分から決める
   以上、1 ファイルも編集しない生成専用の呼び出しでは整形候補が全部ユーザの
   未コミットの変更になる(実測で prettier がセミコロンを足した)。
2. **stop-audit** (`hooks/stop-audit.sh`):
   デバッグ文(`console.log` / `debugger` / `breakpoint()` 等)の残留を監査し、
   見つかれば exit 2 でブロックする。`stop_hook_active` で無限ループを防ぐ。
   報告は量を限る(1 ファイル 5 件、一致行は 200 バイトまで、20 ファイルと残りの件数)。
   巨大な 1 行でフックが分単位で止まるのを防ぐため。
   最初のコミット前(`HEAD` がまだ無い)は、`HEAD` との差分の代わりに
   ステージ済みのファイルを監査する。この「変更ファイルの選び方」は
   `_hook_common.sh` の `hook_git_changed_files` に一本化してあり、
   `auto-format.sh` と `lint.sh` の git 差分フォールバックも同じ関数を使う
   (以前は Codex の 2 つだけ `HEAD` 前提で、最初のコミット前のステージ済み
   ファイルを黙って飛ばしていた)。そのヘルパーを読めなければ監査を諦めて
   exit 0 で抜ける(jq 不在と同じ fail-open)。
   監査するのは `HEAD` に対して作業ツリーが**追加した行**だけ(`_hook_common.sh` の `hook_git_added_lines`)。
   `HEAD` に無いファイル(未追跡・新規ステージ・最初のコミット前の全ファイル)は全行が追加なので全体を見る。
   `git mv` した移動先は、移動元との差分だけを監査する(ステージしていない `mv` と、類似度 50% 未満の書き換えは、ファイル全体を走査する)。
   コミット済みの正当な `console.log` が 1 つあるだけで、そのファイルが dirty の間は毎ターン block してしまうのを避けるため。
   対象は `.js` `.jsx` `.ts` `.tsx` `.mjs` `.cjs` `.mts` `.cts` `.vue` と Python の `.py`。
   `hook_git_added_lines` が読めないときも、`hook_git_changed_files` と同じく監査を諦めて exit 0 で抜ける。
   `EDITOR_AI_ONESHOT` が立っていれば監査そのものを飛ばす — vim / nvim の AI
   機能が `codex exec` / `claude -p` を叩くときに付ける目印。あれは生成専用の
   一発呼び出しで、監査対象はエージェントが書いたコードではなくユーザ自身の
   未コミットの変更なので、ブロックすると作業ツリーを勝手に直したうえで生成物
   の代わりに「削除しました」という文章が返ってくる。

bash-review がレビューのために起動する入れ子の `codex exec` にも同じ
`EDITOR_AI_ONESHOT` を付けているため、そのレビュー実行では上の 2 つとも動かない。

## なぜ `.claude/hooks` と別実装なのか

同じ処理でも Claude と Codex で**要求される作法が違う**ため、単一のスクリプトに
統合できない。`.claude/hooks` 側がロジックの上流(正)で、`.codex/hooks` はそれを
Codex 向けに適応させた版。ロジックを変えるときは両方に反映する必要がある。

差分の軸は 4 つ:

### 1. 返却プロトコル

Codex の PreToolUse フックは `permissionDecision` のうち **`allow` / `deny` は解釈
する**が、**`ask` は未サポート**で、返すとフックがエラー扱い
(`PreToolUse hook returned unsupported permissionDecision:ask`) になり **fail-open**
する(実測 Codex 0.144.5)。`{decision: "block"}` も解釈しない。Claude の `ask`
相当 ―― 人間に確認を求めて停止するプリミティブ ―― が Codex には無いため、
**ブロックは一律 exit 2 + stderr(fail-closed)** で表明する。

> 補足(実測): `permissionDecision: "deny"` の JSON は権威的に効き、Codex は
> 迂回しない。ただし内部の `ask` 判定まで `deny` に潰すと意味が変わる(人間が
> 承認する余地を消す)うえ、exit 2 に対する明確な利点も無いため採用していない。
> なお exit 2 はブロックとしては忠告フィードバック的で、Codex は元コマンドを
> 実行しない代わりに単純化した別コマンドへ自律的に置き換えて目的達成を図りうる
> (各コマンドは個別に再審査される)。この自律リトライを抑えるため、`.codex` 版の
> ブロック理由には「回避を試みず人間に報告せよ」という指示を添えている
> (`bash-review.py` の `_AGENT_BLOCK_DIRECTIVE`)。**人間による意図レビューが必須の
> 作業は Claude 側で回すこと。**

|                                              | `.claude` 版                                                 | `.codex` 版     |
| -------------------------------------------- | ------------------------------------------------------------ | --------------- |
| git-push-review                              | `{hookSpecificOutput: {permissionDecision: "ask"}}` + exit 0 | stderr + exit 2 |
| stop-audit                                   | `{decision: "block"}` + exit 0                               | stderr + exit 2 |
| bash-review-launcher (起動不能/クラッシュ時) | `ask` JSON + exit 0                                          | stderr + exit 2 |

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
承認するまで全フックが黙って無効になる**。ヘッドレス実行(`codex exec`)
では警告も出ず、ただフックが動かなくなる。

変更後は一度 `codex` を対話起動し、`Review hooks` → `Trust all and continue` で
承認すること。

**変更直後は動いているように見える**点に注意。app-server が旧設定をキャッシュ
しているため、実測では発火数が 8 → 4 → 0 と数分かけて減衰した。「変更したが
まだ動いているから大丈夫」と判断すると、後から黙って全停止する。

なおスクリプト実体(`hooks/*.sh`)の変更や、シンボリックリンクの張り替えでは再承認は
不要(`hooks.json` の内容が変わらないため)。整形ロジックを直すだけなら承認は要らず、
フックの掛け先(イベントや matcher)を変えるときだけ必要になる。

## インストール

`hooks.json` は `__HOME__` プレースホルダを含むテンプレートから生成する:

```bash
sed "s|__HOME__|$HOME|g" .codex/hooks.json.template > ~/.codex/hooks.json
```

この sed は `$HOME` を無加工で埋め込むため、`$HOME` に `'` `"` `\` `&` `|` の
いずれかを含む環境では壊れた `hooks.json` になる。`./install.sh` はシェルの引用・
JSON・sed の 3 層をエスケープして生成するので、そうした環境ではそちらを使う。

スクリプトは `~/.codex/hooks/` から本リポジトリの `.codex/hooks/` へシンボリック
リンクを張る。

次の 6 つは Claude 版と完全に同一のロジックなので、実体を 1 つだけ持ってドリフトを
構造的に起こらなくしてある。**その 1 つは `.claude/hooks/` 側にあり、この
`.codex/hooks/` には存在しない** (下記参照):

| ファイル                 | 中身                                                                                                                                                                         | 読み込む側                                               |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `_bash_review_common.py` | 判定ロジック・レビュー呼び出し                                                                                                                                               | `bash-review.py` が import                               |
| `_hook_common.sh`        | `hook_log` / `hook_notify` / `hook_find_nearest_bin` / `hook_git_changed_files` / `hook_find_nearest_file` / `hook_git_added_lines` / `hook_cap_output` / `hook_run_timeout` | `lint.sh` / `auto-format.sh` / `stop-audit.sh` が source |
| `_lint_common.sh`        | 言語別 静的解析マトリクス                                                                                                                                                    | `lint.sh` が source                                      |
| `_format_common.sh`      | 言語別 フォーマッタマトリクス                                                                                                                                                | `auto-format.sh` が source                               |
| `_push_review_common.sh` | git push の検知と確認文の組み立て                                                                                                                                            | `git-push-review.sh` が source                           |
| `_sandbox_gate.py`       | サンドボックス CLI (csb) の契約 (名前・プロファイルと照合・argv の解析とフックの分類・パッチと create の計画・確認の要約・`~/.local/bin/csb` のラッパー)                     | `bash-review.py` と `claude_sandbox.py` が import        |

`bash-review.py` のサンドボックスの部分 (csb の実体の確認・要約の子プロセス・段) は
両入口がそれぞれ持つ写しで、`# >>> shared:` / `# <<< shared:` の印で囲んである。
囲んだ中身がバイト単位で一致すること (段の emit と終了コードの 3 行だけは違ってよい) は
`tests/test_bash_review_sandbox.py` が固定する。

**この `.codex/hooks/` には複製も symlink も置かない**。代わりに読み込む側が
自分の物理パスから `../../.claude/hooks` を解決して直接読む (`bash-review.py` は
`os.path.realpath(__file__)`、`lint.sh` / `auto-format.sh` / `stop-audit.sh` /
`git-push-review.sh` は `cd -P`)。
つまり上の表のファイル名でこのディレクトリを探しても見つからない。

以前はここに相対 symlink を置いていた。実体が 1 つという性質は同じだが、
`core.symlinks=false` (Git for Windows の既定) の clone では git が symlink を
「リンク先パスを書いたテキストファイル」として展開するため、import / `source` が
そのパス文字列を読んで壊れる。リンクを持たない今の形なら、その clone でも作業
ツリーが他と同一になる。

物理解決である点が要: `install.sh` は `~/.codex/hooks` → `<repo>/.codex/hooks` を
張るため、論理解決だと `../../.claude/hooks` が `~/.claude/hooks` を指し、
`install.sh` がそちらも張っているという偶然にぶら下がってしまう。

`tests/test_hook_sync.py` が不変条件を固定する: `.codex` 側に複製もリンクも無い
こと、`.codex/hooks` 配下に mode 120000 の追跡エントリが 1 つも無いこと、そして
symlink 化されたフックディレクトリ経由でも共有ヘルパーが実際に読めること。
解決に失敗した場合に備え、共有ファイルを読む wrapper (`lint.sh` / `auto-format.sh`)
は読み込み後に `declare -F` で関数の存在を確認し、未定義なら stderr に理由を出して
exit 0 で抜ける (fail-open。確認箇所は各 wrapper の `SHARED_DIR` 直後)。

`git-push-review.sh` だけは Claude 版・Codex 版とも薄いアダプタで、検知と要約の本体は
すべて `_push_review_common.sh` にある (アダプタは入力の取り出しと出力の形 =
exit 2 + stderr だけを持つ)。ライブラリを読めないときの扱いが `lint.sh` /
`auto-format.sh` と逆な点に注意: あちらは fail-open だが、こちらは push 手前の
ゲートなので「読めない」を「push ではない」と同一視せず、固定文のブロック
(exit 2 + stderr に `could not load`) に倒す。詳細は `.claude/hooks/README.md`。
