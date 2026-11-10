---
name: codex-image-gen
description: Delegate raster / AI image asset generation to OpenAI Codex's built-in image_gen tool via the `codex exec` CLI, then place the results into a specified project directory. Use whenever a task needs a generated bitmap image written to disk — photos, illustrations, hero images, OG/OGP images, banners, product mockups, textures, sprites — e.g. building a site and needing hero/OGP/feature images in public/, or any request to "generate an image" / "画像を生成" / "画像を作成". The built-in path needs NO OPENAI_API_KEY. Not for SVG/vector/icon-set work that is better produced directly in code (use frontend patterns instead).
---

# Codex Image Generation Skill

**Purpose**: Produce bitmap image assets (photos, illustrations, hero images, OG images,
mockups, textures, sprites) by delegating to OpenAI Codex's **built-in `image_gen` tool**,
run through the `codex exec` CLI, and land the finished files in a caller-specified directory.

**Division of labor**:

- **Claude = art director** — decides which assets are needed and writes a precise spec
  (filename, purpose, dimensions, style, destination). Claude does NOT generate pixels.
- **Codex = generator** — runs `image_gen` (no API key), saves outputs, moves them to the
  destination, and reports the final paths.

Codex's `image_gen` lives in its system skill `~/.codex/skills/.system/imagegen/SKILL.md`.
The **built-in tool mode is preferred and requires no `OPENAI_API_KEY`** (last verified 2026-11 with
Codex CLI 0.167 under `codex exec -s workspace-write`: a 256x256 PNG landed in the named
destination). A CLI fallback exists but needs `OPENAI_API_KEY` and is only for true native
transparency — see Transparency below.

## When to use

- A site/app build needs generated images placed on disk (e.g. `public/hero.png`, `og-image.png`)
- The user asks to "generate / create an image", "画像を生成/作成して", a photo, illustration,
  banner, mockup, texture, or sprite as a real bitmap file
- Multiple visual variants or a batch of distinct assets are needed

## When NOT to use

- Icons, logos, simple shapes, diagrams, or wireframes better produced as **SVG / HTML / CSS /
  canvas** in code — do that directly (frontend-patterns), do not generate a bitmap
- Editing an existing repo-native vector/icon system — extend it in code
- The deliverable is a chart/data-viz — use the dataviz approach, not image_gen

## Invocation path

Delegate through **`codex exec` on the Bash tool**. Codex CLI 0.154 removed its MCP-server mode, so
there is no Codex MCP tool. The command follows the codex-consultation skill's invocation contract
(prompt file, secret-scan gate, `EDITOR_AI_ONESHOT=1`, `-o` answer file, absolute paths written out
literally), with two differences: Codex writes files, and no follow-up turn is planned.

**Codex never writes into the project.** It generates into a new, empty directory in the
scratchpad; Claude verifies the files and copies them into the destination itself.

1. Create an empty output directory, `<scratchpad>/codex-image-<slug>-<n>/`, and write the image spec
   (template below, with `Destination: ./`) to `<scratchpad>/codex-image-<slug>-<n>.prompt.txt` with
   the Write tool.
2. Run it with the Bash tool, **`run_in_background: true`** (generation outlasts a foreground call):

   ```bash
   python3 ~/.claude/hooks/../../scripts/secret_scan.py < <prompt-file> && EDITOR_AI_ONESHOT=1 codex exec -s workspace-write -C <output-dir> --skip-git-repo-check --ephemeral -o <answer-file> - < <prompt-file> > <log-file> 2>&1
   ```

3. When the task notification arrives, check the exit status and Read `<answer-file>` (Codex's
   report of the saved paths), verify the files (After Codex returns, below), then copy them into
   the project destination with `cp` — never overwriting an existing file.

Why an empty scratch directory rather than `-C <project-root>`:

- `-s workspace-write` makes the whole `-C` tree writable, and the sandbox may have network access
  (`[sandbox_workspace_write] network_access` in `.codex/config.toml.template`). Run in the project,
  Codex would also read the project's `AGENTS.md`, so a prompt-injection planted there could
  rewrite `.claude/` / `.codex/` settings or send data out. An empty directory gives it nothing to
  read and nothing else to write.
- The bash-review hook enforces this: `-s workspace-write` with a `-C` outside the session
  scratchpad, or `--add-dir`, goes to the user for confirmation.
- `--ephemeral` keeps the run out of `~/.codex/sessions`; a correction is a fresh, small run.
- `EDITOR_AI_ONESHOT=1` stops Codex's Stop hooks from running on the run's working tree.

The built-in `image_gen` path needs no `OPENAI_API_KEY`. The run is autonomous; Claude cannot
intervene mid-run, so **finalize the spec first**.

## Image spec prompt template

Pass a structured spec so results are deterministic. Fill every field.

```text
Generate the following images with the built-in image_gen tool and place them at the
destination. Do NOT use the CLI/API fallback.

Destination: ./ (the current working directory — an empty scratch directory)
Shared style: <palette / tone / photoreal|illustration|3D / mood>

Assets (call image_gen once per asset — do NOT use n for distinct assets):
1. <filename.png> — use: <where it appears> / size: <WxH (aspect)> / content: <subject>
2. <filename.png> — use: <...>                 / size: <...>       / content: <...>

Requirements:
- Generate each at its target size and move/copy the final file into the destination
  (do NOT leave it under $CODEX_HOME/generated_images/...).
- Write nothing outside the current working directory.
- After saving, report each file's absolute path, dimensions, and byte size.
- Web optimization: also emit a WebP version compressed to <~200KB where practical
  (use sharp / cwebp / equivalent).
```

## Rules that keep results reliable

1. **Always name the destination** (`./`, the scratch directory). Codex's built-in tool saves to
   `$CODEX_HOME/generated_images/` first, then moves to the workspace only when a destination is
   given.
2. **One `image_gen` call per distinct asset.** `n` is for variants of a single prompt, not for
   different assets.
3. **Never overwrite** existing assets — when copying into the project, pick a versioned sibling
   (`hero-v2.png`) if the name is taken.
4. **Require a report of saved paths** so Claude can verify placement.
5. **Ask for web optimization** in the same call — raw outputs run large (~1000px+, multiple MB).

## Transparency (the one API-key case)

- Simple opaque cutouts: built-in `image_gen` on a flat chroma-key background, then Codex removes
  it locally with `remove_chroma_key.py`. No API key.
- **True native transparency** (hair, fur, smoke, glass, soft shadows) requires the CLI fallback
  `gpt-image-1.5 --background transparent`, which needs `OPENAI_API_KEY`. **Ask the user before
  taking this path.** The CLI is Codex's own, at
  `$CODEX_HOME/skills/.system/imagegen/scripts/image_gen.py` (default `CODEX_HOME` is
  `~/.codex`); it is untracked here because Codex manages that tree — see
  `.codex/skills/.system/imagegen/references/cli.md` for the invocation.

## After Codex returns

1. Verify each reported path exists and matches the requested filename/size (Read or `ls`).
2. Copy the files from the scratch directory into the project destination (`cp`, no overwrite).
3. Wire the assets into the site/app (HTML/JSX references, `next/image`, OG meta tags, etc.).
4. If an asset missed the spec (wrong size, off-brand, leaked text), re-issue a single targeted
   `codex exec` run with a one-line correction rather than regenerating the whole batch.
