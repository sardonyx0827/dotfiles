# Image Asset Generation

Role separation (single source of truth):

- **This rule**: triggers and delegation protocol only
- **`codex-image-gen` skill**: full workflow, prompt template, save-path rules, caveats

Generated bitmap images (photos, illustrations, hero/OG images, banners, mockups, textures,
sprites) are produced by delegating to **OpenAI Codex's built-in `image_gen` tool** via the
`codex exec` CLI on the Bash tool. The built-in path needs **no `OPENAI_API_KEY`**. Claude acts as art
director (writes the spec and places the files); Codex generates them into a scratch directory.

## Triggers

| Situation                                                                                     | Action                                                                                                 |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| A task needs a generated bitmap image written to disk (site `public/` assets, OG image, etc.) | Follow the **codex-image-gen** skill                                                                   |
| The user asks to generate/create an image, photo, illustration, banner, mockup, or texture    | Follow the **codex-image-gen** skill                                                                   |
| Multiple visual variants or a batch of distinct image assets are needed                       | Follow the **codex-image-gen** skill                                                                   |
| A short animated loop of generated imagery is needed (GIF, animated WebP, looping MP4/WebM)   | Follow the **codex-image-gen** skill — Step 0 routes it to the spritesheet path                        |
| The user asks for a video                                                                     | Follow the **codex-image-gen** skill — Step 0: generative video is unavailable; offer its alternatives |

Do NOT trigger for icons, logos, simple shapes, diagrams, wireframes, charts, or UI motion
(loaders, transitions, animated typography) that are better built as SVG / HTML / CSS / canvas in
code — produce those directly (frontend-patterns / dataviz).

## Protocol

1. Build a structured image spec: destination dir, and per asset the filename, purpose, size, and
   style (use the template in the `codex-image-gen` skill).
2. Delegate to Codex with `codex exec -s workspace-write -C <empty scratch dir> --ephemeral`, behind
   the secret-scan gate and in the background — the exact command is in the `codex-image-gen` skill
   (built-in `image_gen`, no API key). Codex never writes into the project. Request one `image_gen`
   call per distinct asset and a report of saved paths.
3. Verify each reported path exists and matches the requested filename/size. For an animation,
   Claude slices and encodes the spritesheet in the scratch directory first (the skill's
   `references/animation.md`). Copy only the finished files into the destination without
   overwriting, then wire the assets into the project.

## Guardrails

- **API key only for true transparency.** The built-in path needs no key. True native transparency
  (`gpt-image-1.5 --background transparent`) needs `OPENAI_API_KEY` — **ask the user first**.
- **No silent overwrite.** Never overwrite an existing asset; request a `-v2` sibling.
- **Autonomous run.** `codex exec` is non-interactive and cannot be interrupted mid-run — finalize
  the spec before invoking.
- **Optimize for web.** Ask Codex to emit compressed WebP versions of stills; raw outputs are
  large. A spritesheet stays raw — Claude encodes the animation from it.
- **No native GIF by default.** Codex's `gif` option is rejected by the backend on ChatGPT login
  (openai/codex#59890) and needs `--enable`, which the bash-review hook sends to the user. Use the
  spritesheet path; probe native GIF only when the user asks.
