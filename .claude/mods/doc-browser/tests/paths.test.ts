import { describe, expect, test } from "claude-code/testing";

import {
  BLOCK_REMOTE_CSP_META,
  RENDERED_HEAD,
  hasBlockRemoteCsp,
  isInside,
  isSafeRel,
  openArgv,
  openCandidates,
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

describe("openCandidates", () => {
  test("open は 1 通りだけで、環境変数は渡さない", () => {
    expect(openCandidates("open", "/r/a.html", { DISPLAY: ":0" })).toEqual([
      ["open", "/r/a.html"],
    ]);
  });

  test("xdg-open は systemd-run で切り離す形を先に、直接呼ぶ形を後に返す", () => {
    expect(
      openCandidates("xdg-open", "/r/a.html", {
        WAYLAND_DISPLAY: "wayland-0",
        DISPLAY: ":0",
      }),
    ).toEqual([
      [
        "systemd-run",
        "--user",
        "--collect",
        "--quiet",
        "--expand-environment=no",
        "-E",
        "DISPLAY=:0",
        "-E",
        "WAYLAND_DISPLAY=wayland-0",
        "xdg-open",
        "/r/a.html",
      ],
      ["xdg-open", "/r/a.html"],
    ]);
  });

  test("空や未設定の変数と、一覧に無い変数はユニットに渡さない", () => {
    const [detached] = openCandidates("xdg-open", "/r/a.html", {
      DISPLAY: ":0",
      WAYLAND_DISPLAY: "",
      BROWSER: undefined,
      HOME: "/home/tester",
    });

    expect(detached).toEqual([
      "systemd-run",
      "--user",
      "--collect",
      "--quiet",
      "--expand-environment=no",
      "-E",
      "DISPLAY=:0",
      "xdg-open",
      "/r/a.html",
    ]);
  });

  test("パスの $ を systemd に展開させない ($ を含むファイル名でも検証したパスのまま渡す)", () => {
    const path = "/r/a${HOME}b$$c.html";
    const [detached] = openCandidates("xdg-open", path, { DISPLAY: ":0" });

    expect(detached).toContain("--expand-environment=no");
    expect(detached?.slice(-2)).toEqual(["xdg-open", path]);
  });

  test("画面の変数が無ければ切り離さず、xdg-open を直接呼ぶ形だけを返す", () => {
    expect(
      openCandidates("xdg-open", "/r/a.html", {
        DISPLAY: "",
        XAUTHORITY: "/home/tester/.Xauthority",
        BROWSER: "w3m",
      }),
    ).toEqual([["xdg-open", "/r/a.html"]]);
    expect(
      openCandidates("xdg-open", "/r/a.html", { WAYLAND_DISPLAY: "wayland-0" }),
    ).toHaveLength(2);
  });

  test("相対パスはどの形でも受け付けない", () => {
    expect(() => openCandidates("xdg-open", "a.html", {})).toThrow();
    expect(() => openCandidates("open", "a.html", {})).toThrow();
  });
});
