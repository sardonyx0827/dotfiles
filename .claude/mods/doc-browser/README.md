# doc-browser

リポジトリの Markdown と、Claude が書いたセッションレポート（session-report・session-dossier）・
調査ドキュメント（codebase-survey）をペインに一覧し、選んだものをブラウザで開く Claude Code の mod です。

## 読み込み

`.claude/settings.json` の `env.CLAUDE_CODE_PLUGIN_DIRS` に `~/.claude/mods/doc-browser` が入っていれば、
セッション開始時に読み込まれます。設定を変えたときは Claude Code を再起動してください。

## 使い方

```text
/doc-browser                      ペインを開く（前回のタブ）
/doc-browser reports              レポートのタブで開く
/doc-browser reports all          全プロジェクトのレポートを出す
/doc-browser docs setup           パスに「setup」を含む文書だけに絞る
/doc-browser reports survey       パスに「survey」を含むレポートに絞る（調査ドキュメントはこれで拾える）
/doc-browser open 3               ペインを描けない環境で、本文の一覧の 3 番を開く
```

`open <番号>` 以外の引数は順不同です。`open` は先頭に置き、番号（1〜999）だけを続けます。

- タブ名は `docs`・`config`・`reports` のいずれかです。
- `all` を付けると、レポートを全プロジェクト分に広げます。
- それ以外の語は絞り込みに使います。パスに対する部分一致で、大文字小文字は区別せず、複数指定するとすべてを含むものに絞ります。

### タブ

| タブ         | 中身                                                                                                                    |
| ------------ | ----------------------------------------------------------------------------------------------------------------------- |
| ドキュメント | README・`docs/` など、パスの先頭がドットでない Markdown                                                                 |
| 設定系       | `.claude/` `.codex/` `.github/` など、パスの先頭がドットの Markdown                                                     |
| レポート     | `~/.claude/reports/<リポジトリ名>/` のセッションレポート・dossier・調査ドキュメント（新しい順）。dossier は共有版も含む |

リポジトリの Markdown は `git ls-files --cached --others --exclude-standard` で集めます。コミット済みのものに加え、
`.gitignore` で除外されていない未追跡のファイルも出ます。

### ペインの操作

| キー        | 動作                                                 |
| ----------- | ---------------------------------------------------- |
| `1`〜`9`    | その番号の項目をブラウザで開く                       |
| `d` `c` `s` | ドキュメント・設定系・レポートのタブに切り替える     |
| `n` `p`     | 次のページ・前のページ（1 ページ 9 件）              |
| `a`         | レポートのタブに移り、全プロジェクト分に広げる／戻す |
| `r`         | 一覧を読み直す                                       |
| `Esc`       | ペインを閉じる                                       |

- プロンプトが空ならペインは開いた時点でキーを受け取ります。プロンプトに文字を入力するとキーはプロンプトに戻るので、
  ペインに戻すときは `ctrl+x` → `Tab` を押します。
- マウスでボタンや項目を押せるのはフルスクリーン表示のときだけです。tmux の中では既定で通常表示になります。
- 開いた結果や開けなかった理由は、ペインの最下行に表示されます。

### 開き方

- レポートの HTML は加工せずにブラウザで開きます。レポート置き場（`~/.claude/reports/`）の中身を信頼する前提です。
  - 例外として、調査ドキュメントの HTML は `--block-remote` の CSP が付いているときだけそのまま開きます。付いて
    いなければ Markdown から `--block-remote` で描き直して開き、Markdown も無ければ開きません。
    フラグの付け忘れや古い HTML を拾うための検査で、手書きの HTML の偽造までは防ぎません。
- Markdown は session-report の `render_report.py`（サニタイズ付き）で HTML にしてから開きます。
  - 調査ドキュメント（codebase-survey）の Markdown は `--block-remote` を付けて描き、手元のファイル以外からの
    画像などの読み込みを CSP で止めます。README のバッジを出すため、それ以外の文書には付けません。
  - 出力先は既定で `~/.cache/claude-doc-browser/` です（`XDG_CACHE_HOME` が設定されていればその下）。
    リポジトリにもレポート置き場にも書き込みません。
  - README の `assets/*.svg` のような相対パスの画像は、元の場所を基点に表示されます。
- リポジトリから開くのは `.md` だけです。
- 開く直前に毎回、実体のパスを確かめます。許可した場所（リポジトリ・レポート置き場・プレビュー置き場）の外を指す symlink や、
  通常ファイルでないものは開きません。
- macOS では `open` で開きます。Linux では `systemd-run --user --collect --quiet --expand-environment=no -E <変数>=<値> … xdg-open <パス>` で、
  systemd のユーザーユニットに `xdg-open` を起動させ、ブラウザの終了を待ちません。
  - `--expand-environment=no` は、パスの `${VAR}` や `$$` を systemd に展開させないためです。付けないと、`$` を含む
    ファイル名で検証したのとは別のパスが渡ります。これを知らない systemd 254 より前では、下の「直接呼ぶ」経路になります。
  - `xdg-open` は、起動していないブラウザ（Firefox など）を開くとそれが終わるまで戻りません。直接呼ぶと 10 秒の打ち切りに
    かかり、ブラウザごと閉じられます（Android の Linux ターミナルで VM 内の Firefox を使うときに起きました）。
  - ユニットに渡す変数は `DISPLAY`・`WAYLAND_DISPLAY`・`XAUTHORITY`・`XDG_SESSION_TYPE`・`XDG_CURRENT_DESKTOP`・`BROWSER`
    のうち、値があるものだけです。ユーザーマネージャーの環境には画面の変数が入っていないことが多いためです。
  - `systemd-run` が無い、または失敗した（ユーザーマネージャーが無い WSL やコンテナなど）ときは `xdg-open` を直接呼びます。
    この経路では、ブラウザを先に起動しておかないと上の打ち切りにかかります。
  - `DISPLAY` も `WAYLAND_DISPLAY` も無い（headless や SSH）ときは、切り離さずに最初から `xdg-open` を直接呼び、
    失敗をそのまま「ブラウザを起動できませんでした」と伝えます。
  - 「開きました」は、ユニットの起動を引き受けたという意味です。ブラウザが実際に表示したかまでは確かめていません
    （`xdg-open` も、既定のアプリが失敗しても 0 を返すことがあります）。

### ペインを描けない環境

ペインを置けない環境（デスクトップアプリの一部など）では、コマンドの返り値に番号付きの一覧（最大 20 件）を返します。
`/doc-browser open <番号>` で、その番号の項目を開けます。この一覧はモデルも読むので、絶対パスは載せません。

## 開発

```bash
claude plugin test .claude/mods/doc-browser       # テスト（hooks/ の純粋ロジックとコマンド・ペイン）
claude plugin validate .claude/mods/doc-browser   # マニフェストとフックの検査
```

この mod はツールの実行可否には関与せず、それに関わるイベント（`tool.*` など）にはフックしません。
一方で、`git`・`uname`・`python3`（renderer）・`open` / `systemd-run` / `xdg-open` は Bash ツールを通さず、決まった引数でシェルを使わずに直接起動します。
そのため、これらは bash-review の審査の対象外です。
設計と安全上の前提は [docs/claude-architecture.md の「mod (関数フック)」](../../../docs/claude-architecture.md#mod-関数フック) にあります。
