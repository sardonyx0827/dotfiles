import { describe, expect, test } from "claude-code/testing";

import {
  BLOCK_REMOTE_CSP_META,
  RENDERED_HEAD,
  hasBlockRemoteCsp,
  isInside,
  isSafeRel,
  openArgv,
  openerFor,
  previewDir,
  renderArgv,
} from "../hooks/paths";

describe("isSafeRel", () => {
  test("絶対パス・..・NUL・バックスラッシュ・空を拒否する", () => {
    for (const bad of [
      "",
      "/etc/passwd",
      "../x.md",
      "a/../b.md",
      "a/..",
      "a\0b.md",
      "a\\b.md",
      "a\nb.md",
      "a\u0001.md",
    ]) {
      expect(isSafeRel(bad)).toBe(false);
    }
    for (const good of [
      "README.md",
      "docs/日本語.md",
      "-dash.md",
      "a/.hidden/b.md",
    ]) {
      expect(isSafeRel(good)).toBe(true);
    }
  });
});

describe("isInside", () => {
  test("前方一致の罠 (/r2) と根そのものは内側ではない", () => {
    expect(isInside("/r/a.md", "/r")).toBe(true);
    expect(isInside("/r/a.md", "/r/")).toBe(true);
    expect(isInside("/r2/a.md", "/r")).toBe(false);
    expect(isInside("/r", "/r")).toBe(false);
  });
});

describe("previewDir", () => {
  test("同名ファイルでもディレクトリ構造を写すのでぶつからない", () => {
    expect(previewDir("/c/doc-browser", "dotfiles", "README.md")).toBe(
      "/c/doc-browser/dotfiles",
    );
    expect(
      previewDir("/c/doc-browser", "dotfiles", ".claude/agents/README.md"),
    ).toBe("/c/doc-browser/dotfiles/.claude/agents");
  });
});

describe("hasBlockRemoteCsp", () => {
  const rest =
    '<meta name="viewport" content="x">\n<title>t</title>\n<style>a{}</style>\n</head><body><script src="x.js"></script></body></html>';

  test("render_report.py --block-remote が書いた先頭と一致すれば真", () => {
    expect(
      hasBlockRemoteCsp(`${RENDERED_HEAD}${BLOCK_REMOTE_CSP_META}\n${rest}`),
    ).toBe(true);
  });

  test("CSP が無い・緩い・コメントの中・後ろにあるものは偽", () => {
    const weak =
      '<meta http-equiv="Content-Security-Policy" content="img-src *">';
    expect(hasBlockRemoteCsp(`${RENDERED_HEAD}${rest}`)).toBe(false);
    expect(hasBlockRemoteCsp(`${RENDERED_HEAD}${weak}\n${rest}`)).toBe(false);
    expect(
      hasBlockRemoteCsp(
        `${RENDERED_HEAD}<!-- ${BLOCK_REMOTE_CSP_META} -->\n${rest}`,
      ),
    ).toBe(false);
    expect(
      hasBlockRemoteCsp(
        `${RENDERED_HEAD}<SCRIPT src="x.js"></SCRIPT>\n${BLOCK_REMOTE_CSP_META}\n${rest}`,
      ),
    ).toBe(false);
    expect(hasBlockRemoteCsp("")).toBe(false);
  });
});

describe("argv", () => {
  test("renderer は python3 で、ブラウザを開かせず、md のディレクトリを基点にし、-- の後に md を渡す", () => {
    expect(
      renderArgv(
        "/h/.claude/skills/session-report/render_report.py",
        "/c/out",
        "/r/a.md",
      ),
    ).toEqual([
      "python3",
      "/h/.claude/skills/session-report/render_report.py",
      "--no-open",
      "--out",
      "/c/out",
      "--base-dir",
      "/r",
      "--",
      "/r/a.md",
    ]);
  });

  test("blockRemote なら --block-remote を -- より前に付ける", () => {
    expect(
      renderArgv(
        "/h/.claude/skills/session-report/render_report.py",
        "/c/out",
        "/r/a.md",
        { blockRemote: true },
      ),
    ).toEqual([
      "python3",
      "/h/.claude/skills/session-report/render_report.py",
      "--no-open",
      "--out",
      "/c/out",
      "--base-dir",
      "/r",
      "--block-remote",
      "--",
      "/r/a.md",
    ]);
  });

  test("opener には絶対パスだけを渡す", () => {
    expect(openArgv("open", "/r/a.html")).toEqual(["open", "/r/a.html"]);
    expect(() => openArgv("open", "a.html")).toThrow();
  });

  test("Darwin は open、それ以外は xdg-open", () => {
    expect(openerFor("Darwin\n")).toBe("open");
    expect(openerFor("Linux")).toBe("xdg-open");
  });
});
