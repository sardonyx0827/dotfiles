---
name: request-harness
description: Use this skill when processing requests dropped into docs/requests/ (via /requests, or when the user asks to handle pending requests/依頼). Covers intake, ticket lifecycle, task-type routing (code, visual materials, research, writing, ops), execution, and delivery reports.
---

# Request Harness

Drive requests the user drops into `docs/requests/` to completion. Requests are free-form: chat excerpts, specs, memos, screenshots, PDFs. Deliverables are NOT limited to code — visual materials (slides, diagrams, one-page HTML), research reports, and documents are equally common.

## Directory Contract

`docs/requests/` is relative to the current repository root.

```
docs/requests/
├── README.md              # usage guide for humans (scaffolded on first run)
├── <request>              # DROP ZONE: any file or folder at root = one new request
├── in-progress/
│   └── <ticket-id>/
│       ├── <originals>    # moved as-is from the drop zone (never edit or delete)
│       ├── TICKET.md      # state: interpretation, plan, log — single source of truth
│       └── output/        # deliverables
└── done/
    └── <ticket-id>/       # completed tickets (+ REPORT.md)
```

- **Pick-up rule**: everything at `docs/requests/` root except `README.md`, `in-progress/`, `done/` is a new request. One file or one folder = one request.
- **Ticket ID**: `YYYYMMDD-<kebab-slug>` — slug is a short English summary (e.g. `20260704-sales-onboarding-slides`).
- **Scaffolding**: if `docs/requests/` does not exist, create the structure above and write `README.md` from the template at the end of this file.

## Ticket Format (TICKET.md)

Body in Japanese. Frontmatter:

```yaml
---
status: in-progress | needs-input | blocked | done
type: code | visual | research | writing | ops | mixed
created: YYYY-MM-DD
updated: YYYY-MM-DD
source: <original file/folder name>
---
```

Required sections:

| Section                          | Content                                                      |
| -------------------------------- | ------------------------------------------------------------ |
| `## 依頼の解釈`                  | Restate the request in your own words                        |
| `## 成果物 (Definition of Done)` | Concrete deliverables and acceptance criteria                |
| `## 前提・仮定`                  | Assumptions made where the request was ambiguous             |
| `## 計画`                        | Checklist of steps (`- [ ]`, check off as you go)            |
| `## 作業ログ`                    | Dated entries appended as work proceeds                      |
| `## 未解決の質問`                | Questions for the user (populate when status is needs-input) |

TICKET.md is the single source of truth: a fresh session must be able to resume any ticket from it alone. Update it as you work, not retroactively.

## Workflow

### 1. Scan

List new requests at root and tickets in `in-progress/`. Show the user a board (Japanese) before executing:

```
| チケット | 状態 | 種別 | 概要 |
```

### 2. Intake (per new request)

1. Read every dropped file (the Read tool handles images and PDFs).
2. Create `in-progress/<ticket-id>/`, move the originals in unchanged, create `output/`.
3. Write TICKET.md: interpretation, Definition of Done, plan.

### 3. Clarify

- Prefer proceeding with reasonable assumptions; record each one under 前提・仮定.
- Ask the user (mechanism per Runtime Notes) only when the answer would fundamentally change the deliverable (wrong deliverable type, irreversible actions, missing access/credentials).
- If blocked with no answer available: set `status: needs-input`, fill 未解決の質問, and move on to the next ticket instead of stalling.

### 4. Execute — route by type

| type     | Approach                                                                                           | Deliverable                                               |
| -------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| code     | Follow the global dev workflow (tdd-workflow skill, code-reviewer agent). Work in the repo itself. | Code changes; put a summary of touched paths in `output/` |
| visual   | Self-contained HTML/SVG per the guidelines below                                                   | Files in `output/`                                        |
| research | Web search/browse per Runtime Notes; always cite sources                                           | Markdown report in `output/`                              |
| writing  | Documents, translations, summaries                                                                 | Markdown (or HTML) in `output/`                           |
| ops      | Scripts, environment changes — confirm before anything destructive                                 | Script + usage notes in `output/`                         |

Execution layers follow the global rules (CLAUDE.md / AGENTS.md; Single by default). When 2+ pending tickets are independent, launch **request-worker** subagents in parallel (2–4, mechanism per Runtime Notes), one ticket each. Never parallelize tickets that touch the same files or both modify repo code — run those sequentially.

### 5. Deliver

1. Verify every item in the Definition of Done.
2. Write `REPORT.md` (Japanese) in the ticket folder:
   `# 完了報告: <題名>` with sections 成果物 (paths + one-line description) / 依頼の解釈と前提 / 実施内容 / 残課題・フォローアップ.
3. Set `status: done` and move the ticket folder to `done/`.
4. Summarize all processed tickets to the user with deliverable paths, and list any tickets left in needs-input.

**Reopen**: for follow-up fixes, move the folder back to `in-progress/`, append the new ask to 作業ログ, and continue.

## Visual Deliverable Guidelines

- One self-contained file: inline CSS/JS, no CDN or network dependencies — must render offline via `file://`.
- Diagrams: inline SVG preferred; keep the Mermaid source alongside when it helps future edits.
- Slides: a single HTML file, 16:9, arrow-key navigation.
- Japanese font stack: `-apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif`.
- Content language: Japanese unless the request says otherwise.
- Claude Code only: for a shareable preview, additionally publish via the Artifact tool — the file in `output/` remains the source of truth.

## Runtime Notes

This skill is shared between Claude Code (`~/.claude/skills/`) and Codex (`~/.codex/skills/`). Map capabilities as follows:

| Capability           | Claude Code                          | Codex                                           |
| -------------------- | ------------------------------------ | ----------------------------------------------- |
| Entry point          | `/requests` command                  | skill auto-activates (natural-language request) |
| Global rules         | CLAUDE.md                            | AGENTS.md                                       |
| Clarifying questions | AskUserQuestion tool                 | Ask directly in chat                            |
| Parallel tickets     | request-worker subagent (Agent tool) | request-worker agent (multi-agent feature)      |
| Web research         | claude-in-chrome / WebSearch         | `web_search`                                    |
| Shareable preview    | Artifact tool (optional)             | Skip — the file in `output/` is the deliverable |

If a capability is missing in the current runtime, fall back to sequential single-agent execution — the directory contract and ticket lifecycle never change.

## Safety

- Never edit or delete the user's original request files; moving them into the ticket folder is the only allowed operation.
- CLAUDE.md safety guards apply: confirm before destructive operations; keep secrets and personal information out of deliverables.
- A request that asks for something destructive or outward-facing (mass mail, production changes) always requires explicit user confirmation first.

## docs/requests/README.md Template

```markdown
# docs/requests — 依頼ボックス

雑務・依頼はこのディレクトリ直下にファイルまたはフォルダで置いてください。
`/requests` を実行すると Claude が取り込み、完了まで進めます。

## 置き方

- 形式は自由: `.md` / `.txt` のメモ、チャットのコピペ、仕様書、スクリーンショット、PDF など
- 関連ファイルが複数あるときは 1 フォルダにまとめて置く(1 フォルダ = 1 依頼)
- 欲しい成果物・期限・参考資料があれば書いておくと精度が上がります

## 流れ

1. 直下に依頼を置く → `/requests` 実行
2. Claude が `in-progress/<チケットID>/` に取り込み、TICKET.md(解釈・計画)を作成
3. 成果物は `output/` に、完了後は `done/<チケットID>/`(REPORT.md 付き)へ移動
4. 質問がある依頼は TICKET.md の「未解決の質問」に記載され、状態が needs-input になります

## コマンド

- `/requests` — 新規取り込み + 未完了チケットの処理
- `/requests status` — 状況一覧のみ表示
- `/requests <チケットID>` — 特定チケットのみ処理
```
