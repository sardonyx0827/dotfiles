---
name: session-report
description: Summarize what Claude Code actually did into a markdown + mermaid report and open it in the browser. Use when the user asks to wrap up, summarize, or visualize the work ("まとめて", "レポートにして", "何をやったか整理して", "summarize this session"), and offer it unprompted after a long multi-step task whose result is hard to reconstruct from terminal scrollback. Not for single-question answers or one-file edits.
---

# Session Report

Terminal scrollback is a bad medium for a finished piece of work: it is linear,
unscannable, and the reasoning is buried between tool calls. This skill turns a
completed stretch of work into one browser page the user can read top-to-bottom
in a minute — and, crucially, one that answers **why**, not just **what**.

The deliverable is a markdown file plus a self-contained HTML rendering of it.
The markdown is the source of truth; the HTML is a disposable view.

## When to run

| Situation                                                                                                  | Action                                                       |
| ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| User asks to summarize / wrap up / visualize the work                                                      | Run this skill                                               |
| A multi-step task just finished: several files touched, or decisions were made that the user did not watch | Offer it in one line, then run if accepted                   |
| A single question, a one-line edit, or work the user watched step by step                                  | **Do not run** — the normal reply is already cheaper to read |

Never run this mid-task. A report on unfinished work is a second thing to keep
track of, which is the opposite of the point.

## Output location

```
~/.claude/reports/<project-slug>/<YYYYMMDD-HHMMSS>-<slug>.md
~/.claude/reports/<project-slug>/<YYYYMMDD-HHMMSS>-<slug>.html
```

- `<project-slug>`: the repository directory name (`basename` of the git toplevel), or `no-repo` outside one.
- `<slug>`: 2–4 English kebab-case words describing the work (`add-session-report-skill`).
- Deliberately **outside** the user's repository: reports are session artifacts, not project files, and must never show up in `git status` or need a `.gitignore` entry.

Get the timestamp from `date +%Y%m%d-%H%M%S` — never guess it.

## Procedure

1. **Collect evidence before writing.** Do not reconstruct the session from memory:
   - `git status --short` and `git diff --stat` for what actually changed
   - `git log --oneline <base>..HEAD` if commits were made
   - the real output of any test / lint / build run this session
2. **Write the markdown** to the path above, following the structure below.
3. **Render and open:**
   ```bash
   python3 ~/.claude/skills/session-report/render_report.py <path-to-md>
   ```
   The script writes the `.html` beside the `.md` and opens it in the default
   browser, then prints the HTML path. Pass `--no-open` to write it without
   opening (use this when re-rendering after a correction — the user already
   has the tab open and only needs to reload), or `--out DIR` to place the
   page somewhere other than next to the markdown.
4. **Report back in one or two lines** with the `.md` path. Do not restate the
   report's contents in chat — that would defeat the purpose.

## Report structure

Keep the whole thing under roughly 150 lines. A report that takes as long to
read as the scrollback has failed.

```markdown
# <一行で「何をしたか」>

> **TL;DR** — 3 行以内。読者がここで読むのをやめても困らない内容にする。

## 変更点

| ファイル          | 種別 | 要点               |
| ----------------- | ---- | ------------------ |
| `path/to/file.py` | 新規 | 何のために足したか |

## 全体像

<mermaid — 下記のポリシーに従う。不要なら節ごと省く>

## やったこと

時系列。各項目は「作業 → 結果」の 1〜2 行。ツールの実行ログは貼らない。

## 判断と根拠

**この節がこのレポートの中心**。採用した選択肢・却下した選択肢・決め手になった事実を書く。
ユーザーが見ていなかった意思決定ほど厚く書く。

## 検証結果

実際に走らせたコマンドとその結果のみ。走らせていないものは「未実施」と書く。

## 未完了 / 既知の制約

無いなら「なし」と明記する。空欄にしない。

## 次のアクション

ユーザーが判断すべきこと / こちらが続けられること を分けて書く。
```

Write the report in Japanese, per the language policy in `CLAUDE.md`.

## Mermaid policy

A decorative flowchart _raises_ cognitive load. A diagram earns its place only
when it encodes a relationship the prose cannot state compactly.

- **At most 2 diagrams per report. Zero is a perfectly good answer.**
- Every node must correspond to something real — an actual file, an actual step,
  an actual decision. No invented boxes to fill the canvas.
- Use one of these shapes; do not invent new ones:

| Shape                          | Use when                                            | Sketch                                                |
| ------------------------------ | --------------------------------------------------- | ----------------------------------------------------- | ---- | ------------------ |
| `flowchart LR`                 | 3+ changed files with a real call/data relationship | `A[skill] --> B[render_report.py] --> C[report.html]` |
| `flowchart TD` with `{}` nodes | the work had genuine branch points                  | `A{file:// で ESM は?} -->                            | 不可 | B[classic script]` |
| `sequenceDiagram`              | the change is about a call/message order            | actor per component                                   |

Skip the diagram entirely when the work was linear, touched 1–2 files, or is
fully described by the 変更点 table.

Quote fenced blocks as ` ```mermaid `. Labels containing `(`, `)`, `:` or
`,` must be wrapped in double quotes — `A["render_report.py (classic script)"]`
— otherwise the parse fails and the page shows the source instead of a diagram.

## Rendering constraints (do not "modernise" these)

The page is opened from `file://`, where Chrome refuses ES-module imports. Both
libraries are therefore loaded as **classic** scripts, which works because
mermaid's `dist/mermaid.min.js` ends in `globalThis["mermaid"] = ...` and
marked's `marked.min.js` is a UMD bundle. Switching either tag to
`type="module"` or to an `.esm.` URL renders every diagram blank.

By default the libraries come from the CDN, so the first render of a report
needs network. To make reports work offline, vendor the two files once:

```bash
mkdir -p ~/.claude/assets
npx -y --package=mermaid@11 --package=marked@15 -c 'true'
cp "$(find ~/.npm/_npx -name mermaid.min.js -path '*/dist/*' | head -1)" ~/.claude/assets/
cp "$(find ~/.npm/_npx -name marked.min.js | head -1)" ~/.claude/assets/
```

`render_report.py` prefers `~/.claude/assets/` per library and falls back to the
CDN for whichever is missing. If a library fails to load at view time the page
degrades to readable markdown source with a banner — never to a blank page.

## Permission

`python3` is not in `permissions.allow`, so step 3 prompts every time unless the
user adds:

```json
"Bash(python3 ~/.claude/skills/session-report/render_report.py:*)"
```

The script opens the browser itself via `subprocess`, so no separate
`Bash(open:*)` entry is needed.
