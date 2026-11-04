import { describe, expect, mock, test } from "claude-code/testing";
import type { Engine } from "claude-code/testing";
import type { On } from "claude-code";

const HOME = "/home/tester";
const ROOT = "/work/repo";
const REPORTS = `${HOME}/.claude/reports`;
const PREVIEW = `${HOME}/.cache/claude-doc-browser`;
const RENDERER = `${HOME}/.claude/skills/session-report/render_report.py`;

const PANE_PROPS = {
  title: "doc-browser",
  isFocused: true,
  bodyColumns: 100,
  placement: "dock" as const,
  scroll: { offset: 0, bodyRows: 40 },
  view: {},
};

// ペインの状態行とページ行 (Text は key ではなく本文で探す)。
const STATUS = /^(開きました|開けません)/;
const PAGE = /ページ · \d+ 件/;

type Stat = { kind: "file" | "dir" | "other"; realPath?: string };

// テストの on は mod の下 (エンジンの位置) に座るので、mod が $ で呼ぶものに答える。
function world(
  on: On,
  options: {
    isRepo?: boolean;
    isPlaced?: boolean;
    lsFiles?: string;
    reports?: { name: string; kind?: "file" | "dir"; isLink?: boolean }[];
    stats?: Record<string, Stat>;
    renderExit?: number;
    renderStdout?: string;
    lsTruncated?: boolean;
    openDeny?: string;
  } = {},
) {
  mock.env(on, { HOME });
  mock.clock(on);
  const runs: string[][] = [];
  const opened: { id: string; focus?: boolean }[] = [];

  on("session.root", () => ({ value: ROOT }));
  on("session.cwd", () => ({ value: ROOT }));
  on("process.run", ($, e) => {
    const argv = [...e.argv];
    runs.push(argv);
    const ok = (stdout: string) => ({
      value: {
        exitCode: 0,
        stdout,
        stderr: "",
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    });
    if (argv[0] === "uname") return ok("Darwin\n");
    if (argv.includes("rev-parse")) {
      return options.isRepo === false
        ? {
            value: {
              exitCode: 128,
              stdout: "",
              stderr: "not a git repository",
              isStdoutTruncated: false,
              isStderrTruncated: false,
            },
          }
        : ok(`${ROOT}\n`);
    }
    if (argv.includes("ls-files")) {
      const stdout =
        options.lsFiles ??
        "README.md\0docs/guide.md\0.claude/skills/x/SKILL.md\0";
      return {
        value: {
          exitCode: 0,
          stdout,
          stderr: "",
          isStdoutTruncated: options.lsTruncated ?? false,
          isStderrTruncated: false,
        },
      };
    }
    if (argv[0] === "python3") {
      const out = argv[argv.indexOf("--out") + 1] ?? "";
      const md = argv[argv.length - 1] ?? "";
      const html = `${out}/${md.slice(md.lastIndexOf("/") + 1).replace(/\.md$/, ".html")}`;

      return {
        value: {
          exitCode: options.renderExit ?? 0,
          stdout: options.renderExit
            ? ""
            : (options.renderStdout ?? `${html}\n`),
          stderr: options.renderExit ? "boom" : "",
          isStdoutTruncated: false,
          isStderrTruncated: false,
        },
      };
    }

    return ok("");
  });
  on("fs.stat", ($, e) => {
    const stat = options.stats?.[e.path] ?? {
      kind: e.path === REPORTS || e.path.endsWith("/repo") ? "dir" : "file",
      realPath: e.path,
    };

    return {
      value: {
        kind: stat.kind,
        size: 0,
        mtimeMs: 1,
        isLink: false,
        ...(e.resolve ? { realPath: stat.realPath } : {}),
      },
    };
  });
  on("fs.list", ($, e) => {
    if (e.path === `${REPORTS}/repo`) {
      return {
        value: (
          options.reports ?? [
            { name: "20261104-103000-fix-login.md" },
            { name: "20261104-103000-fix-login.html" },
            { name: "20261104-110000-dossier-d0a13a87.html" },
          ]
        ).map((entry) => ({
          name: entry.name,
          kind: entry.kind ?? "file",
          size: 1,
          mtimeMs: 1,
          isLink: entry.isLink ?? false,
        })),
      };
    }
    if (e.path === REPORTS) {
      return {
        value: [
          { name: "repo", kind: "dir", size: 0, mtimeMs: 1, isLink: false },
          { name: "other", kind: "dir", size: 0, mtimeMs: 1, isLink: false },
        ],
      };
    }

    return { value: [] };
  });
  on("ui.open", ($, e) => {
    opened.push({ id: e.id, focus: e.focus });
    if (options.openDeny !== undefined) return { deny: options.openDeny };

    return {
      value:
        options.isPlaced === false
          ? { isPlaced: false, reason: "no surface places panes" }
          : { isPlaced: true },
    };
  });
  on("ui.close", () => ({ value: undefined }));
  on("ui.panes", () => ({ value: [] }));
  on("ui.status", () => ({ value: undefined }));
  on("command.register", ($, e) => ({ value: { command: e.name } }));

  const openRuns = () =>
    runs.filter((argv) => argv[0] === "open" || argv[0] === "xdg-open");
  const renderRuns = () => runs.filter((argv) => argv[0] === "python3");

  return { runs, opened, openRuns, renderRuns };
}

function run($: Engine, args = "") {
  return $.command.run({
    command: "doc-browser",
    args,
    origin: { kind: "composer" },
    presentation: { isFullscreen: true, columns: 120 },
  });
}

function mountPane($: Engine, surface: "terminal" | "desktop" = "terminal") {
  return $.ui.mount({
    plugin: "doc-browser",
    surface,
    component: "Pane",
    requestId: "doc-browser",
    props: PANE_PROPS,
  });
}

describe("/doc-browser", () => {
  test("toplevel で git ls-files を -z と fsmonitor 無効で実行し、ペインをフォーカス付きで開く", async ($, on) => {
    const w = world(on);

    const result = await run($);

    const ls = w.runs.find((argv) => argv.includes("ls-files"));
    expect(ls).toEqual([
      "git",
      "-c",
      "core.fsmonitor=false",
      "-C",
      ROOT,
      "ls-files",
      "-z",
      "--cached",
      "--others",
      "--exclude-standard",
      "--",
      "*.md",
    ]);
    expect(w.opened).toEqual([{ id: "doc-browser", focus: true }]);
    expect(result.text).toBe("repo  ドキュメント 2 · 設定系 1 · レポート 2");
  });

  test("リポジトリの外では docs は空で、レポートは no-repo から読む", async ($, on) => {
    world(on, { isRepo: false });

    const result = await run($);

    expect(result.text).toBe("no-repo  ドキュメント 0 · 設定系 0 · レポート 0");
  });

  test("ペインを置けない場所では番号付きの一覧を本文で返し、open N でその番号を開く", async ($, on) => {
    const w = world(on, { isPlaced: false });

    const listed = await run($, "reports");

    expect(listed.text?.split("\n")).toEqual([
      "repo  ドキュメント 2 · 設定系 1 · レポート 2",
      "1. dossier-d0a13a87 — 2026-11-04 11:00 · 詳細記録",
      "2. fix-login — 2026-11-04 10:30 · レポート",
    ]);

    const opened = await run($, "open 2");

    expect(w.openRuns()).toEqual([
      ["open", `${REPORTS}/repo/20261104-103000-fix-login.html`],
    ]);
    expect(opened.text).toBe("開きました: fix-login");
  });

  test("open N の番号が一覧にない、または引数が読めないときは使い方を返す", async ($, on) => {
    const w = world(on, { isPlaced: false });

    expect((await run($, "open 5")).text).toMatch(/^使い方/);
    expect((await run($, "open x")).text).toMatch(/^使い方/);
    expect(w.openRuns()).toEqual([]);
  });
});

describe("ペイン", () => {
  test("Markdown を押すと renderer で ~/.cache に描いてから開く", async ($, on) => {
    const w = world(on);
    await run($);
    const ui = await mountPane($);

    await ui.press({ key: "item:doc:docs/guide.md" });

    expect(w.renderRuns()).toEqual([
      [
        "python3",
        RENDERER,
        "--no-open",
        "--out",
        `${PREVIEW}/repo/docs`,
        "--base-dir",
        `${ROOT}/docs`,
        "--",
        `${ROOT}/docs/guide.md`,
      ],
    ]);
    expect(w.openRuns()).toEqual([["open", `${PREVIEW}/repo/docs/guide.html`]]);
    expect((await ui.find({ type: "Text", text: STATUS }))?.text).toBe(
      "開きました: guide.md",
    );
    await ui.unmount();
  });

  test("HTML のレポートは renderer を通さずそのまま開く", async ($, on) => {
    const w = world(on);
    await run($, "reports");
    const ui = await mountPane($);

    await ui.press({ key: "item:rep:repo/20261104-110000-dossier-d0a13a87" });

    expect(w.renderRuns()).toEqual([]);
    expect(w.openRuns()).toEqual([
      ["open", `${REPORTS}/repo/20261104-110000-dossier-d0a13a87.html`],
    ]);
    await ui.unmount();
  });

  test("リポジトリの外を指す symlink・ディレクトリ・拡張子違いは開かない", async ($, on) => {
    const w = world(on, {
      lsFiles: "evil.md\0dir.md\0",
      stats: {
        [`${ROOT}/evil.md`]: { kind: "file", realPath: "/etc/secret.md" },
        [`${ROOT}/dir.md`]: { kind: "dir", realPath: `${ROOT}/dir.md` },
      },
    });
    await run($);
    const ui = await mountPane($);

    await ui.press({ key: "item:doc:evil.md" });
    expect((await ui.find({ type: "Text", text: STATUS }))?.text).toMatch(
      /^開けません/,
    );
    await ui.press({ key: "item:doc:dir.md" });
    expect((await ui.find({ type: "Text", text: STATUS }))?.text).toMatch(
      /^開けません/,
    );

    expect(w.renderRuns()).toEqual([]);
    expect(w.openRuns()).toEqual([]);
    await ui.unmount();
  });

  test("renderer が失敗したらブラウザは開かず、理由を出す", async ($, on) => {
    const w = world(on, { renderExit: 1 });
    await run($);
    const ui = await mountPane($);

    await ui.press({ key: "item:doc:README.md" });

    expect(w.openRuns()).toEqual([]);
    const status = (await ui.find({ type: "Text", text: STATUS }))?.text;
    expect(status).toBe("開けません: README.md（描画に失敗しました）");
    expect(status).not.toContain("boom");
    await ui.unmount();
  });

  test("タブとページを切り替えられ、どの面でも描ける", async ($, on) => {
    const many = Array.from(
      { length: 12 },
      (_, i) => `docs/d${String(i).padStart(2, "0")}.md`,
    ).join("\0");
    world(on, { lsFiles: many });
    await run($);

    for (const surface of ["terminal", "desktop"] as const) {
      const ui = await mountPane($, surface);
      expect((await ui.find({ type: "Text", text: PAGE }))?.text).toBe(
        "ドキュメント · 1/2 ページ · 12 件",
      );
      await ui.press({ key: "next" });
      expect((await ui.find({ type: "Text", text: PAGE }))?.text).toBe(
        "ドキュメント · 2/2 ページ · 12 件",
      );
      await ui.press({ key: "tab-reports" });
      expect((await ui.find({ type: "Text", text: PAGE }))?.text).toBe(
        "レポート · 1/1 ページ · 2 件",
      );
      await ui.press({ key: "tab-docs" });
      await ui.unmount();
    }
  });

  test("全プロジェクトに切り替えると他のプロジェクトのレポートも読む", async ($, on) => {
    world(on);
    await run($, "reports");
    const ui = await mountPane($);

    await ui.press({ key: "all-projects" });

    expect((await ui.find({ type: "Text", text: PAGE }))?.text).toBe(
      "レポート（全プロジェクト） · 1/1 ページ · 2 件",
    );
    await ui.unmount();
  });
});

describe("セキュリティレビューの指摘", () => {
  test("レポート置き場の Markdown もプレビュー置き場に描き、レポート置き場には書かせない", async ($, on) => {
    const w = world(on, {
      reports: [{ name: "20261103-090000-only-markdown.md" }],
    });
    await run($, "reports");
    const ui = await mountPane($);

    await ui.press({ key: "item:rep:repo/20261103-090000-only-markdown" });

    expect(w.renderRuns()).toEqual([
      [
        "python3",
        RENDERER,
        "--no-open",
        "--out",
        `${PREVIEW}/_reports/repo`,
        "--base-dir",
        `${REPORTS}/repo`,
        "--",
        `${REPORTS}/repo/20261103-090000-only-markdown.md`,
      ],
    ]);
    expect(w.openRuns()).toEqual([
      ["open", `${PREVIEW}/_reports/repo/20261103-090000-only-markdown.html`],
    ]);
    await ui.unmount();
  });

  test("リポジトリの項目は .md しか開かない (.html を指す symlink は生のまま開かない)", async ($, on) => {
    const w = world(on, {
      lsFiles: "README.md\0",
      stats: {
        [`${ROOT}/README.md`]: {
          kind: "file",
          realPath: `${ROOT}/docs/x.html`,
        },
      },
    });
    await run($);
    const ui = await mountPane($);

    await ui.press({ key: "item:doc:README.md" });

    expect(w.renderRuns()).toEqual([]);
    expect(w.openRuns()).toEqual([]);
    expect((await ui.find({ type: "Text", text: STATUS }))?.text).toMatch(
      /^開けません/,
    );
    await ui.unmount();
  });

  test("描画結果のパスは renderer の出力ではなく、出力先と元の名前から決める", async ($, on) => {
    const w = world(on, { renderStdout: `${PREVIEW}/other/README.html\n` });
    await run($);
    const ui = await mountPane($);

    await ui.press({ key: "item:doc:README.md" });

    expect(w.openRuns()).toEqual([["open", `${PREVIEW}/repo/README.html`]]);
    await ui.unmount();
  });
});

describe("コードレビューの指摘", () => {
  test("git ls-files の出力が上限で切れたら、途中で切れた最後の名前は使わない", async ($, on) => {
    world(on, { lsFiles: "a.md\0b.m", lsTruncated: true, isPlaced: false });

    const result = await run($, "docs");

    expect(result.text?.split("\n").slice(1)).toEqual(["1. a.md"]);
  });

  test("ペインを開いたら、前回本文に返した番号の一覧は使えなくする", async ($, on) => {
    const options: { isPlaced?: boolean } = { isPlaced: false };
    const w = world(on, options);
    await run($, "reports");
    options.isPlaced = true;
    await run($);

    expect((await run($, "open 1")).text).toMatch(/^使い方/);
    expect(w.openRuns()).toEqual([]);
  });
});

describe("セキュリティ再確認の指摘", () => {
  test("想定外のエラーの詳細 (パスを含みうる) は本文に載せない", async ($, on) => {
    world(on, { openDeny: "EACCES: /Users/someone/secret/place" });

    const result = await run($);

    expect(result.text).toMatch(/^doc-browser:/);
    expect(result.text).not.toContain("/Users/someone");
  });
});
