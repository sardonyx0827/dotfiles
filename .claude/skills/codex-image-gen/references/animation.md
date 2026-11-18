# Animation and video (codex-image-gen reference)

Read this when Step 0 in `SKILL.md` routes a request to **animation** or **video**. The Codex side
does not change: Codex still generates **one still image** (a spritesheet) through the invocation in
`SKILL.md`. Claude slices and encodes it locally. Slicing and encoding is deterministic processing,
not image generation.

**Prerequisites:** ImageMagick 7 (`magick`), ffmpeg and ffprobe, and libwebp (`img2webp`, `cwebp`).
`install.sh` does not install them. Check with `command -v magick ffmpeg ffprobe img2webp cwebp`.
If one is missing, tell the user (e.g. `brew install imagemagick ffmpeg webp`) instead of installing
it yourself.

## Status (verified 2026-11-18, Codex CLI 0.170.0, ChatGPT login)

| Option                                       | Status                                                                                                                                                                                                                                                                                       |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spritesheet → local encode (GIF / MP4 / ...) | Works. Tested on one Codex-generated 2x2 sheet of a flat bouncing ball: the floor line and ball position lined up across all four frames. That proves flat art only; characters, hands, faces and liquids drift more. The commands below were run as written on a 1254px copy of that sheet. |
| Native GIF (`gif` option of `imagegen`)      | Dormant: the backend rejects it on this login. See the last section.                                                                                                                                                                                                                         |
| Generative video                             | Not available in the current Codex setup. See "Video requests".                                                                                                                                                                                                                              |

## Frame budget

- **Start at 4–6 frames, cap at 9 normally, use 16 only as an exception.** More cells mean smaller
  cells and more drift between them. The cap is a working rule, not an API limit. Weigh it against
  the cell resolution the destination needs and how complex the motion is. A motion that needs
  more distinct poses than that is a video request (see Video requests).
- **Use a grid whose cell count equals the frame count, with no empty cells:** 4 → 2x2, 6 → 3x2,
  8 → 4x2, 9 → 3x3. An empty cell would be encoded as a blank frame.
- The raw built-in output measured 1254x1254 for a square request. **That size is not guaranteed.**
  Only the square case was tested; a non-square sheet's raw size is unverified. Measure the actual
  sheet before slicing. At 1254px: 2x2 ≈ 627px cells, 3x3 ≈ 418px, 4x4 ≈ 313px.
- Frame count alone does not make motion smooth. Extra cells help only when each one is a real
  in-between pose, and duplicated frames never help.
- With square cells, the sheet aspect is cols : rows (3x2 → 3:2 landscape). For non-square cells it
  is (cols × cell width) : (rows × cell height). Prefer square cells and crop afterwards, unless the
  destination dictates otherwise.

## Spritesheet spec (the Codex prompt)

Use this in place of the still template in `SKILL.md`. Everything else in the invocation stays the
same: a new empty scratch directory, the secret scan, `-s workspace-write`, and `--ephemeral`.

```text
Generate ONE still image with the built-in image_gen tool (do NOT set the `gif` option) and
place it at the destination. Do NOT use the CLI/API fallback.

Destination: ./ (the current working directory — an empty scratch directory)

Asset:
1. <name>-sheet.png — use: animation spritesheet that will be sliced into frames
   Sheet aspect: <cols>:<rows> (<landscape|portrait|square>), square cells
   Grid: exactly <cols> columns x <rows> rows = <N> equal cells, every cell filled,
         read left-to-right, top-to-bottom
   Frames: 1: <phase> / 2: <phase> / ... / <N>: <phase> (frame <N> leads back into frame 1)
   Fixed in every cell: camera, framing, background, lighting, subject scale, ground-line position
   Avoid: borders, gutters, grid lines, frame numbers, labels, any text, watermark
   Style: <palette / tone / medium>

Requirements:
- Keep the raw resolution: do NOT resize, crop or convert the image, and do not create a WebP.
- Move/copy the file into the destination (do NOT leave it only under
  $CODEX_HOME/generated_images/...).
- Write nothing outside the current working directory.
- Report the file's absolute path, pixel dimensions and byte size.
```

## Slice and encode (Claude, inside the scratch directory)

- **Make a new working directory for every attempt** inside the run's scratch directory. Copy the
  sheet into it as `sheet.png`. Copy only the finished files into the project with `cp -n` (see
  After Codex returns in `SKILL.md`).
- **Run steps 1–3 in one Bash call.** Shell variables and the working directory do not survive
  between calls, so the first line `cd`s to the absolute path and stops if the sheet is missing.
  `mkdir frames` (without `-p`) also stops on a reused directory, so stale frames cannot mix in.
- Set the grid (`COLS`/`ROWS`) and the frame duration in ms (`D`) on the second line. `FR` is
  `1000/D` as an ffmpeg rational. `png:` pins ImageMagick's decoder to PNG instead of sniffing
  the content of a file Codex wrote.

1. **Measure and slice into equal, even-sized frames.**
   - Equal sizes keep the animation from jittering.
   - Even sizes are needed because H.264 with `yuv420p` rejects odd dimensions: a 1254px 2x2
     sheet gives 627px cells, and that encode fails.
   - So round each cell size down to even, resize the sheet to an exact multiple of it, then crop.
     The resize changes each axis by at most 2 × grid − 1 px (6px for a 1254px 4x4 sheet).
   - The tiles come out in row-major order. `%02d` keeps the glob order correct up to 100 frames.

   ```bash
   cd "<work-dir>" || exit 1; [ -f sheet.png ] || exit 1
   COLS=2; ROWS=2; D=250; FR="1000/$D"; mkdir frames || exit 1
   SW=$(magick identify -format '%w' png:sheet.png); SH=$(magick identify -format '%h' png:sheet.png)
   CW=$(( SW / COLS / 2 * 2 )); CH=$(( SH / ROWS / 2 * 2 ))
   magick png:sheet.png -resize "$((CW*COLS))x$((CH*ROWS))!" -crop "${CW}x${CH}" +repage frames/f_%02d.png
   ```

   If the destination needs smaller frames, downscale every frame to one fixed even size.

2. **Encode. Keep only the lines for the formats you deliver.** The two GIF lines are
   alternatives: both write `out.gif`.
   - **Timing** comes from the input rate: `-framerate`, or `-delay` in GIF's 1/100 s units.
   - **Do not add `-r 30`.** Keep the input rate in the output. Resampling to 30 fps snaps every
     frame to a 1/30 s grid: 250 ms frames alternate between 233 and 267 ms, frames shorter than
     about 33 ms are dropped, and the motion gets no smoother.
   - **The last frame** keeps its full duration when the input rate is kept: 4 frames at 250 ms
     make 1.0 s. A concat-demuxer input would drop the last frame's duration.
   - **`-y`** only matters when you re-encode in the same directory, for example switching to the
     palette GIF after QA.

   ```bash
   # GIF, flat art: ImageMagick quantization is fine. D must be a multiple of 10 ms.
   magick -delay $((D/10)) -loop 0 frames/f_*.png -layers Optimize out.gif
   # GIF, photo-like or gradients (alternative to the line above): a palette avoids banding.
   ffmpeg -nostdin -y -loglevel error -framerate "$FR" -i frames/f_%02d.png -vf "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3" -loop 0 out.gif
   # MP4 (H.264). It plays once; looping is the player's job (<video loop>).
   ffmpeg -nostdin -y -loglevel error -framerate "$FR" -i frames/f_%02d.png -c:v libx264 -pix_fmt yuv420p -crf 23 -movflags +faststart out.mp4
   # WebM (VP9), optional alongside MP4.
   ffmpeg -nostdin -y -loglevel error -framerate "$FR" -i frames/f_%02d.png -c:v libvpx-vp9 -b:v 0 -crf 35 -pix_fmt yuv420p out.webm
   # Animated WebP, usually the smallest. -d takes ms.
   img2webp -loop 0 -d "$D" -lossy -q 75 frames/f_*.png -o out.webp
   # Poster still for <video>.
   cwebp -q 80 frames/f_00.png -o poster.webp
   ```

3. **Check the encode.**
   - **Frame count:** `magick identify out.gif | wc -l` for GIF/WebP (one line per frame). For
     MP4/WebM, `ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 out.mp4`.
   - **Duration:** `ffprobe -v error -show_entries format=duration -of csv=p=0 out.mp4`.
   - **Byte size:** check it against the destination.
   - **Look at it:** Claude cannot watch an animation, so look at a contact strip instead. Make one
     with `magick frames/f_*.png +append -resize 1600x strip.png` and Read it.

## Delivery format

These are recommendations. Adjust for transparency, size and where it will play.

| Destination                         | Format                              | Notes                                                                                                                                                                                                                                                      |
| ----------------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| README / GitHub issue or PR / Slack | GIF                                 | Autoplays everywhere. Largest of the options, so keep it short and small                                                                                                                                                                                   |
| Web page                            | MP4 (H.264) + poster, WebM optional | `<video autoplay loop muted playsinline poster="...">`. Give the user a way to pause, and show the poster instead under `prefers-reduced-motion: reduce`                                                                                                   |
| Size-critical web asset             | Animated WebP                       | Usually the smallest. Supported by current browsers                                                                                                                                                                                                        |
| Needs transparency                  | Animated WebP                       | Built-in output is opaque, so get alpha first through the Transparency section in `SKILL.md` (chroma-key removal, or the API-key path with the user's consent). GIF has only 1-bit alpha (hard edges), H.264 MP4 has none, and WebM alpha is untested here |

## QA before copying into the project

Check the contact strip and the encoded file for:

- Missing or duplicated phases; the order matches the spec
- Changes in subject shape or size between frames
- Background or ground line shifting between frames
- Flicker, clipped edges, and leaked gutters, grid lines or frame numbers
- The loop seam: whether the last frame flows back into the first
- GIF banding or colour shift (switch to the palette path if so)
- File size suitable for the destination

To fix a miss, re-run with a targeted correction in a **new** empty scratch directory, at most two
retries (three generations in all). Then stop and report the best result to the user along with
what is still off. QA catches problems; it does not guarantee a result for subjects that drift.

## Video requests

Say this up front: **generative video is not available in the current Codex setup.** The Sora 2
models and the OpenAI Videos API were shut down on 2026-09-24, and no one-to-one replacement exists.
Codex CLI has no video tool or feature flag. Then offer:

1. **A short loop:** the animation path above, delivered as MP4.
2. **Stills turned into video:** generate the stills through the normal flow, then use ffmpeg.
   - **Slow zoom (Ken Burns):** give `zoompan` a single input image and set `d` to the total number
     of output frames.
   - **Aspect:** crop to the output aspect first, because `zoompan` stretches a mismatched aspect.
     Generate the still at the output aspect so the crop does not cut the subject.
   - **Crossfade:** both inputs to `xfade` must have the same size.
   - **Paths:** run in the same kind of fresh working directory, `cd`ed by absolute path.

   ```bash
   # 4 s at 30 fps (d=120), 16:9 output from one still.
   ffmpeg -nostdin -y -loglevel error -i still.png -vf "scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440,zoompan=z='min(zoom+0.0015,1.2)':d=120:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=30,format=yuv420p" -c:v libx264 -crf 23 -movflags +faststart kenburns.mp4
   # Crossfade two stills (3 s each, 1 s fade).
   ffmpeg -nostdin -y -loglevel error -loop 1 -framerate 30 -t 3 -i a.png -loop 1 -framerate 30 -t 3 -i b.png -filter_complex "[0:v][1:v]xfade=transition=fade:duration=1:offset=2,format=yuv420p" -c:v libx264 -crf 23 -movflags +faststart crossfade.mp4
   ```

3. **Code-native motion** (CSS / SVG / canvas / Remotion) when the motion is UI, typography or a
   diagram. See frontend-patterns.

Do not promise camera moves through a scene, character performance, lip sync or narration.

## Native GIF (dormant — prose only on purpose)

This section has no runnable command, on purpose. `tests/test_bash_review.py` checks only the
`codex exec` lines in the fences of `SKILL.md` against the bash-review hook's allowlist. A fenced
command here would escape that check, and `--enable` is not on the allowlist anyway.

- **What exists:** Since Codex CLI rust-v0.168.0, the built-in `imagegen` tool accepts a `gif`
  option. `{}` gives 9 frames × 200 ms; `frame_count` takes 2–16 and `frame_duration_ms` takes
  20–2000. It is gated by the feature flag `features.image_generation_animation`, which is under
  development and off by default. The model sees only a still preview of the result. Editing a GIF
  means passing an equal-cell spritesheet through `referenced_image_paths`, not the GIF itself.
- **Status on 2026-11-18:** With ChatGPT login, the backend rejects every request with
  `400 {"detail":"GIF output is not enabled for this request."}`. This is reported in
  openai/codex#59890, which is still open.
- **Policy:** Do not attempt native GIF by default. Probe it only when the user explicitly asks
  for a re-check.
  - The probe adds `--enable image_generation_animation`. The bash-review hook stops it at a
    confirmation dialog, and the user approves it there.
  - This is the one exception to the codex-consultation skill's rule against adding `--enable`. Never
    add it to get an ordinary run through.
- **Bringing it back:**
  - The condition is a successful native GIF on this CLI version, with this auth method, on this
    account. #59890 closing is not enough by itself.
  - Wiring it into the flow then needs the allowlist in `codex_flag_ask_reason`
    (`.claude/hooks/_bash_review_common.py`) to accept that exact flag value, with tests.
  - That is a hook and permission change, so it is a security-reviewer trigger and its own
    decision.
