---
name: codex-image-gen
description: Delegate raster / AI image asset generation to OpenAI Codex's built-in image_gen tool via the `mcp__codex__codex` tool, then place the results into a specified project directory. Use whenever a task needs a generated bitmap image written to disk — photos, illustrations, hero images, OG/OGP images, banners, product mockups, textures, sprites — e.g. building a site and needing hero/OGP/feature images in public/, or any request to "generate an image" / "画像を生成" / "画像を作成". The built-in path needs NO OPENAI_API_KEY. Not for SVG/vector/icon-set work that is better produced directly in code (use frontend patterns instead).
---

# Codex Image Generation Skill

**Purpose**: Produce bitmap image assets (photos, illustrations, hero images, OG images,
mockups, textures, sprites) by delegating to OpenAI Codex's **built-in `image_gen` tool**,
run through the `mcp__codex__codex` tool, and land the finished files in a caller-specified directory.

**Division of labor**:

- **Claude = art director** — decides which assets are needed and writes a precise spec
  (filename, purpose, dimensions, style, destination). Claude does NOT generate pixels.
- **Codex = generator** — runs `image_gen` (no API key), saves outputs, moves them to the
  destination, and reports the final paths.

Codex's `image_gen` lives in its system skill `~/.codex/skills/.system/imagegen/SKILL.md`.
The **built-in tool mode is preferred and requires no `OPENAI_API_KEY`** (proven working under
`sandbox: workspace-write`). A CLI fallback (`scripts/image_gen.py`) exists but needs
`OPENAI_API_KEY` and is only for true native transparency — see Transparency below.

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

Delegate via the **`mcp__codex__codex` MCP tool** — the supported path for this skill
(verified working 2026-07). Call it with:

- `prompt`: the image spec (template below)
- `sandbox`: `workspace-write` — lets Codex write the finished file into the project
- `approval-policy`: `never` — runs autonomously without an approval prompt
- `cwd`: the project root — so a relative destination resolves there

The built-in `image_gen` path needs no `OPENAI_API_KEY` and no open network access. The call runs
autonomously; Claude cannot intervene mid-run, so **finalize the spec first**.

This skill does not drive image generation through `codex exec` on the Bash tool; delegation goes
through the MCP tool above.

## Image spec prompt template

Pass a structured spec so results are deterministic. Fill every field.

```text
Generate the following images with the built-in image_gen tool and place them at the
destination. Do NOT use the CLI/API fallback.

Destination: <repo-relative-or-absolute dir, e.g. public/images/>
Shared style: <palette / tone / photoreal|illustration|3D / mood>

Assets (call image_gen once per asset — do NOT use n for distinct assets):
1. <filename.png> — use: <where it appears> / size: <WxH (aspect)> / content: <subject>
2. <filename.png> — use: <...>                 / size: <...>       / content: <...>

Requirements:
- Generate each at its target size and move/copy the final file into the destination
  (do NOT leave it under $CODEX_HOME/generated_images/...).
- Do NOT overwrite an existing file; if a name exists, save a -v2 sibling instead.
- After saving, report each file's absolute path, dimensions, and byte size.
- Web optimization: also emit a WebP version compressed to <~200KB where practical
  (use sharp / cwebp / equivalent).
```

## Rules that keep results reliable

1. **Always name the destination.** Codex's built-in tool saves to `$CODEX_HOME/generated_images/`
   first, then moves to the workspace only when a destination is given.
2. **One `image_gen` call per distinct asset.** `n` is for variants of a single prompt, not for
   different assets.
3. **Never overwrite** existing assets — request a versioned sibling (`hero-v2.png`).
4. **Require a report of saved paths** so Claude can verify placement.
5. **Ask for web optimization** in the same call — raw outputs run large (~1000px+, multiple MB).

## Transparency (the one API-key case)

- Simple opaque cutouts: built-in `image_gen` on a flat chroma-key background, then Codex removes
  it locally with `remove_chroma_key.py`. No API key.
- **True native transparency** (hair, fur, smoke, glass, soft shadows) requires the CLI fallback
  `gpt-image-1.5 --background transparent`, which needs `OPENAI_API_KEY`. **Ask the user before
  taking this path.**

## After Codex returns

1. Verify each reported path exists and matches the requested filename/size (Read or `ls`).
2. Wire the assets into the site/app (HTML/JSX references, `next/image`, OG meta tags, etc.).
3. If an asset missed the spec (wrong size, off-brand, leaked text), re-issue a single targeted
   `mcp__codex__codex` call with a one-line correction rather than regenerating the whole batch.
