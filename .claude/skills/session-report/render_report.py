#!/usr/bin/env python3
"""Render a markdown session report into one self-contained HTML page and open it.

Why this shape rather than a preview server:

The page is opened straight from `file://`, so it must not depend on anything
an opaque origin cannot do. That rules out ES modules -- Chrome fails the CORS
check on `file://` and every diagram would come out blank -- which is why both
libraries are loaded as *classic* scripts. That works because mermaid's
`dist/mermaid.min.js` ends with `globalThis["mermaid"] = ...` and marked ships
`marked.min.js` as a UMD bundle ending in `g["marked"]=f()`; neither needs a
module context. Do not "modernise" these tags to `type="module"`.

The markdown is embedded base64-encoded rather than inlined. Reports about
shell and web work routinely quote a closing script tag, and one such quote in
the prose would otherwise close the embedding element early and silently
truncate the rest of the page.

Rendering stays client-side (marked parses the same `.md` the page carries) so
the markdown file remains the single source of truth: it is greppable, diffable
and re-renderable, and the HTML is a disposable view of it.

Usage:
    python3 ~/.claude/skills/session-report/render_report.py REPORT.md
    python3 ~/.claude/skills/session-report/render_report.py REPORT.md --no-open
"""

from __future__ import annotations

import argparse
import base64
import html as html_mod
import platform
import re
import subprocess
import sys
from pathlib import Path

# Pinned to majors: both are loaded from a local file, so a surprise breaking
# change would show up as a blank report rather than a build failure.
#
# No SRI hash. Not a technical impossibility -- a consequence of choosing to
# float the major: `integrity` needs a fixed artifact, and these URLs
# deliberately do not resolve to one. The trade accepted here is that a
# compromised jsdelivr would run its payload on a local file:// page. Vendoring
# into ASSETS_DIR below removes the CDN from the picture entirely and is the
# recommended setup for anyone unwilling to take that trade.
CDN_ASSETS = {
    "mermaid": "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js",
    "marked": "https://cdn.jsdelivr.net/npm/marked@15/marked.min.js",
}

# Drop the same-named files here to make reports work with no network:
#   mkdir -p ~/.claude/assets
#   npx -y --package=mermaid@11 --package=marked@15 -c 'true'
#   cp "$(find ~/.npm/_npx -name mermaid.min.js -path '*/dist/*' | head -1)" ~/.claude/assets/
#   cp "$(find ~/.npm/_npx -name marked.min.js | head -1)" ~/.claude/assets/
ASSETS_DIR = Path.home() / ".claude/assets"

ASSET_FILENAMES = {"mermaid": "mermaid.min.js", "marked": "marked.min.js"}

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_ATX_RE = re.compile(r"^#\s+(.+?)\s*$")


def resolve_assets(assets_dir: Path = ASSETS_DIR) -> dict[str, str]:
    """Prefer a vendored copy of each library, else fall back to the CDN.

    Resolved per library rather than all-or-nothing: a half-populated assets
    directory should still buy you the half it has.
    """
    resolved = {}
    for key, cdn_url in CDN_ASSETS.items():
        local = assets_dir / ASSET_FILENAMES[key]
        resolved[key] = local.as_uri() if local.is_file() else cdn_url
    return resolved


def extract_title(markdown: str, fallback: str) -> str:
    """Return the first top-level heading, ignoring ones inside fenced blocks.

    A report that shows its own markdown source in a fence would otherwise be
    titled after the example rather than after itself.

    Per CommonMark, a fence closes only on the same character repeated at least
    as many times as the opener. Toggling on any fence-looking line instead
    would let a nested `~~~` (a heredoc marker inside a ``` block, say) reopen
    the document early and hand back a heading from the example.
    """
    opener: str | None = None
    for line in markdown.splitlines():
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if opener is None:
                opener = marker
            elif marker[0] == opener[0] and len(marker) >= len(opener):
                opener = None
            continue
        if opener is not None:
            continue
        heading = _ATX_RE.match(line)
        if heading:
            # `# Title #` is a closed ATX heading; the trailing run is syntax.
            return heading.group(1).rstrip("#").rstrip() or fallback
    return fallback


def build_html(markdown: str, title: str, assets: dict[str, str]) -> str:
    """Wrap `markdown` in the standalone viewer shell."""
    payload = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
    return (
        _TEMPLATE.replace("__TITLE__", html_mod.escape(title, quote=True))
        .replace("__MERMAID_SRC__", assets["mermaid"])
        .replace("__MARKED_SRC__", assets["marked"])
        .replace("__MD_B64__", payload)
    )


def render(
    md_path: Path, out_dir: Path | None = None, open_browser: bool = True
) -> Path:
    """Render `md_path` to HTML beside it (or into `out_dir`) and return the path."""
    markdown = md_path.read_text(encoding="utf-8")
    title = extract_title(markdown, md_path.stem)
    html = build_html(markdown, title, resolve_assets())

    target_dir = out_dir or md_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    html_path = target_dir / f"{md_path.stem}.html"
    html_path.write_text(html, encoding="utf-8")

    if open_browser:
        opener = "open" if platform.system() == "Darwin" else "xdg-open"
        # The rendered file is the part that matters, so nothing about opening
        # it may take the caller down. check=False covers a non-zero exit;
        # the except covers the opener not existing at all, which is the normal
        # case on a headless box (no xdg-open) and raises rather than exiting.
        try:
            subprocess.run([opener, str(html_path)], check=False)
        except OSError as exc:
            print(f"render_report: could not launch {opener}: {exc}", file=sys.stderr)
    return html_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("markdown", help="path to the report markdown")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: alongside the input)",
    )
    parser.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="write the page but do not open it",
    )
    args = parser.parse_args(argv)

    md_path = Path(args.markdown).expanduser()
    if not md_path.is_file():
        print(f"render_report: no such markdown file: {md_path}", file=sys.stderr)
        return 1

    print(render(md_path, out_dir=args.out, open_browser=args.open_browser))
    return 0


# Raw: the embedded JS carries regex escapes (`\s`), which Python would
# otherwise read as invalid string escapes and warn about at import time.
_TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d1d9e0;
    --surface: #f6f8fa; --accent: #0969da; --warn-bg: #fff8c5; --warn-fg: #7d4e00;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d;
      --surface: #151b23; --accent: #4493f8; --warn-bg: #2b2411; --warn-fg: #e3b341;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg); line-height: 1.7;
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif;
  }
  main { max-width: 56rem; margin: 0 auto; padding: 2.5rem 1.5rem 6rem; }
  h1, h2, h3 { line-height: 1.3; margin: 2.2rem 0 0.9rem; }
  h1 { font-size: 1.9rem; margin-top: 0; padding-bottom: .5rem; border-bottom: 1px solid var(--border); }
  h2 { font-size: 1.35rem; padding-bottom: .35rem; border-bottom: 1px solid var(--border); }
  h3 { font-size: 1.1rem; }
  p, li { overflow-wrap: anywhere; }
  a { color: var(--accent); }
  code {
    background: var(--surface); border: 1px solid var(--border); border-radius: 5px;
    padding: .1em .35em; font-size: .88em;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  pre { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; overflow-x: auto; }
  pre code { background: none; border: 0; padding: 0; font-size: .85rem; }
  table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; margin: 1rem 0; }
  th, td { border: 1px solid var(--border); padding: .5rem .75rem; text-align: left; }
  th { background: var(--surface); }
  tbody tr:nth-child(even) { background: color-mix(in srgb, var(--surface) 55%, transparent); }
  blockquote { margin: 1rem 0; padding: .1rem 1rem; border-left: 4px solid var(--border); color: var(--muted); }
  hr { border: 0; border-top: 1px solid var(--border); margin: 2rem 0; }
  .mermaid { margin: 1.5rem 0; text-align: center; overflow-x: auto; }
  .mermaid svg { max-width: 100%; height: auto; }
  #toc { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: .75rem 1rem; margin-bottom: 2rem; }
  #toc summary { cursor: pointer; font-weight: 600; }
  #toc ol { margin: .6rem 0 .2rem; padding-left: 1.4rem; }
  #toc a { text-decoration: none; }
  #toc a:hover { text-decoration: underline; }
  .banner {
    background: var(--warn-bg); color: var(--warn-fg); border: 1px solid currentColor;
    border-radius: 8px; padding: .7rem 1rem; margin-bottom: 1.5rem; font-size: .9rem;
  }
  footer { max-width: 56rem; margin: 0 auto; padding: 0 1.5rem 3rem; color: var(--muted); font-size: .8rem; }
</style>
</head>
<body>
<main>
  <div id="banner-slot"></div>
  <details id="toc" hidden><summary>目次</summary><ol></ol></details>
  <article id="content"></article>
</main>
<footer>rendered by render_report.py — 元データは同名の .md</footer>

<script src="__MARKED_SRC__"></script>
<script src="__MERMAID_SRC__"></script>
<script>
(function () {
  // Base64 rather than inline text: report prose quotes closing script tags.
  const MD_B64 = "__MD_B64__";
  const md = new TextDecoder().decode(Uint8Array.from(atob(MD_B64), (c) => c.charCodeAt(0)));
  const content = document.getElementById("content");

  function warn(message) {
    const el = document.createElement("div");
    el.className = "banner";
    el.textContent = message;
    document.getElementById("banner-slot").appendChild(el);
  }

  // A missing library must degrade to readable source, never to a blank page.
  if (typeof window.marked === "undefined") {
    warn("marked を読み込めませんでした（オフラインの可能性）。Markdown をそのまま表示しています。");
    const pre = document.createElement("pre");
    pre.textContent = md;
    content.appendChild(pre);
    return;
  }

  window.marked.setOptions({ gfm: true, breaks: false });

  // Reports quote tool output, web pages and diffs, so the markdown can carry
  // markup that was never meant to run. marked does not sanitize (it dropped
  // its sanitizer in v5), and assigning to innerHTML fires inline handlers
  // like <img onerror> immediately -- scrubbing afterwards would be too late.
  //
  // DOMParser gives an inert document with no browsing context: nothing loads,
  // no handler fires. Scrub there, then adopt the survivors into the page.
  const inert = new DOMParser().parseFromString(window.marked.parse(md), "text/html");

  // SVG animation elements are removed alongside the obvious offenders: an
  // <animate attributeName="href" values="javascript:..."> rewrites a link
  // that passed inspection, after it is already in the live document. Their
  // SVG-namespace names are case-sensitive to a type selector, unlike HTML's.
  inert.body
    .querySelectorAll("script, iframe, object, embed, form, link, meta, base, style, animate, animateTransform, animateMotion, set")
    .forEach((n) => n.remove());

  // Allowlist, not denylist. A blocklist of `javascript:|vbscript:|data:` is
  // defeated by "java&#9;script:" -- the parser turns the entity into a real
  // TAB, the regex stops matching, and the URL parser strips the TAB again on
  // navigation. Strip everything the URL parser ignores, then require a known
  // scheme or none at all (a relative URL cannot execute).
  function isSafeUrl(value) {
    const v = String(value).replace(/[\u0000-\u0020]/g, "").toLowerCase();
    if (/^(https?:|mailto:|tel:)/.test(v)) return true;
    return !/^[a-z][a-z0-9+.-]*:/.test(v);
  }

  const URL_ATTRS = /^(href|src|xlink:href|formaction|action|poster|data|background)$/;
  inert.body.querySelectorAll("*").forEach((el) => {
    Array.from(el.attributes).forEach((attr) => {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) {
        el.removeAttribute(attr.name);
      } else if (name === "srcset") {
        // Every candidate, not just the first: "ok.jpg 1x, javascript:x 2x".
        const candidates = attr.value.split(",");
        if (!candidates.every((c) => isSafeUrl(c.trim().split(/\s+/)[0] || ""))) {
          el.removeAttribute(attr.name);
        }
      } else if (URL_ATTRS.test(name) && !isSafeUrl(attr.value)) {
        el.removeAttribute(attr.name);
      }
    });
  });
  // Snapshot before inserting: childNodes is live, and insertion adopts each
  // node out of the inert body, so iterating it lazily would skip every other
  // node. replaceChildren performs the adoption itself.
  content.replaceChildren(...Array.from(inert.body.childNodes));

  // marked emits ```mermaid as <pre><code class="language-mermaid">, and escapes
  // the source into entities. textContent hands back the decoded original,
  // which is what mermaid needs.
  const blocks = content.querySelectorAll("pre > code.language-mermaid");
  if (blocks.length && typeof window.mermaid === "undefined") {
    warn("mermaid を読み込めませんでした（オフラインの可能性）。図の定義をコードとして表示しています。");
  } else if (blocks.length) {
    const nodes = [];
    blocks.forEach((code) => {
      const holder = document.createElement("div");
      holder.className = "mermaid";
      holder.textContent = code.textContent;
      code.parentElement.replaceWith(holder);
      nodes.push(holder);
    });
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    // securityLevel is stated, not inherited: mermaid builds its own SVG from
    // the same untrusted markdown and injects it downstream of the scrub above,
    // so this is the only thing standing between a diagram label and script
    // execution. Never leave it to the CDN build's default.
    window.mermaid.initialize({
      startOnLoad: false,
      theme: dark ? "dark" : "default",
      securityLevel: "strict",
    });
    window.mermaid.run({ nodes }).catch((err) => warn("mermaid の描画に失敗しました: " + err.message));
  }

  // A short report does not need navigation; a long one does.
  const headings = content.querySelectorAll("h2");
  if (headings.length >= 3) {
    const toc = document.getElementById("toc");
    const list = toc.querySelector("ol");
    headings.forEach((h, i) => {
      h.id = h.id || "section-" + i;
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "#" + h.id;
      a.textContent = h.textContent;
      li.appendChild(a);
      list.appendChild(li);
    });
    toc.hidden = false;
    toc.open = true;
  }
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
