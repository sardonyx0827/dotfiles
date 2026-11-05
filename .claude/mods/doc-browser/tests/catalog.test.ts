import { describe, expect, test } from "claude-code/testing";

import {
  DEFAULT_VIEW,
  PAGE_SIZE,
  displayText,
  docItems,
  formatListing,
  newestFirst,
  paginate,
  parseArgs,
  parseLsFiles,
  reportItems,
  visible,
} from "../hooks/catalog";
import type { DocView } from "../types";

const file = (
  name: string,
  extra: { isLink?: boolean; kind?: "file" | "dir" | "other" } = {},
) => ({
  name,
  kind: extra.kind ?? ("file" as const),
  isLink: extra.isLink ?? false,
});

describe("parseLsFiles", () => {
  test("NUL 区切りを分け、末尾の NUL と重複を落とす", () => {
    expect(
      parseLsFiles("README.md\0docs/a b.md\0docs/改行\nあり.md\0README.md\0"),
    ).toEqual(["README.md", "docs/a b.md", "docs/改行\nあり.md"]);
  });

  test("空の出力は空配列", () => {
    expect(parseLsFiles("")).toEqual([]);
  });
});

describe("docItems", () => {
  test("ドットで始まるディレクトリは config、それ以外は docs。直下のファイルを先に並べる", () => {
    const items = docItems([
      "docs/z.md",
      ".claude/skills/x/SKILL.md",
      "README.md",
      "docs/a.md",
      "INSTALL.md",
    ]);

    expect(items.map((i) => [i.tab, i.rel])).toEqual([
      ["docs", "INSTALL.md"],
      ["docs", "README.md"],
      ["docs", "docs/a.md"],
      ["docs", "docs/z.md"],
      ["config", ".claude/skills/x/SKILL.md"],
    ]);
    expect(items[2]).toMatchObject({
      id: "doc:docs/a.md",
      kind: "doc",
      root: "repo",
      label: "a.md",
      detail: "docs/",
    });
  });

  test("安全でない相対パスは一覧に入れない", () => {
    expect(
      docItems(["../x.md", "/abs.md", "a/../b.md", "ok.md"]).map((i) => i.rel),
    ).toEqual(["ok.md"]);
  });
});

describe("reportItems", () => {
  const entries = [
    file("20261104-103000-fix-login.md"),
    file("20261104-103000-fix-login.html"),
    file("20261103-090000-only-markdown.md"),
    file("20261104-110000-dossier-d0a13a87.html"),
    file("20261104-110000-dossier-d0a13a87.json"),
    file("20261104-110500-dossier-d0a13a87.share.html"),
    file("20261104-110500-dossier-d0a13a87.share.json"),
    file("20261104-111000-dossier-d0a13a87.artifact.html"),
    file("20261104-120000-linked.html", { isLink: true }),
    file("20261104-130000-a-dir.html", { kind: "dir" }),
    file("notes.txt"),
  ];

  test("session-report は md と html を 1 行に束ね、dossier と共有版を分け、新しい順に並べる", () => {
    const items = reportItems("dotfiles", entries, false);

    expect(items.map((i) => [i.kind, i.rel])).toEqual([
      ["dossier-share", "dotfiles/20261104-110500-dossier-d0a13a87.share.html"],
      ["dossier", "dotfiles/20261104-110000-dossier-d0a13a87.html"],
      ["report", "dotfiles/20261104-103000-fix-login.html"],
      ["report", "dotfiles/20261103-090000-only-markdown.md"],
    ]);
    expect(items[2]).toMatchObject({
      tab: "reports",
      root: "reports",
      label: "fix-login",
      detail: "2026-11-04 10:30 · レポート",
    });
  });

  test("全プロジェクト表示ではプロジェクト名を添える", () => {
    const [item] = reportItems("other", [file("20261101-000000-x.html")], true);
    expect(item?.detail).toBe("2026-11-01 00:00 · レポート · other");
  });

  test("codebase-survey の調査は md と html を 1 行に束ね、「調査」と表示する", () => {
    const items = reportItems(
      "dotfiles",
      [
        file("20261105-120000-survey-dotfiles.md"),
        file("20261105-120000-survey-dotfiles.html"),
        file("20261105-090000-survey-dotfiles-nvim.md"),
      ],
      false,
    );

    expect(items.map((i) => [i.kind, i.rel])).toEqual([
      ["survey", "dotfiles/20261105-120000-survey-dotfiles.html"],
      ["survey", "dotfiles/20261105-090000-survey-dotfiles-nvim.md"],
    ]);
    expect(items[0]).toMatchObject({
      tab: "reports",
      root: "reports",
      label: "survey-dotfiles",
      detail: "2026-11-05 12:00 · 調査",
    });
  });

  test("調査の共有版・Artifact 用の断片・manifest は出さない", () => {
    const items = reportItems(
      "dotfiles",
      [
        file("20261105-120000-survey-x.share.html"),
        file("20261105-120000-survey-x.share.md"),
        file("20261105-120000-survey-x.artifact.html"),
        file("20261105-120000-survey-x.json"),
      ],
      false,
    );

    expect(items).toEqual([]);
  });

  test("survey- に続く文字が英数字とハイフンだけでなければ、普通のレポートとして出す", () => {
    const items = reportItems(
      "dotfiles",
      [
        file("20261105-120000-survey-foo.nvim.html"),
        file("20261105-120001-survey-foo_bar.html"),
        file("20261105-120002-my-survey-x.html"),
        file("20261105-120003-survey-.html"),
      ],
      false,
    );

    expect(items.map((i) => i.kind)).toEqual([
      "report",
      "report",
      "report",
      "report",
    ]);
  });

  test("全プロジェクト表示の調査にもプロジェクト名を添え、他の種類と新しい順に並べる", () => {
    const items = reportItems(
      "other",
      [
        file("20261105-120000-survey-other.html"),
        file("20261105-130000-fix-it.html"),
        file("20261105-110000-dossier-abcd1234.html"),
      ],
      true,
    );

    expect(items.map((i) => i.kind)).toEqual(["report", "survey", "dossier"]);
    expect(items[1]?.detail).toBe("2026-11-05 12:00 · 調査 · other");
  });
});

describe("visible / paginate", () => {
  const items = docItems([
    "README.md",
    "docs/Setup.md",
    "docs/usage.md",
    ".claude/a.md",
  ]);

  test("タブと絞り込み語 (AND・大文字小文字無視) で絞る", () => {
    const view: DocView = {
      ...DEFAULT_VIEW,
      tab: "docs",
      filter: ["DOCS", "set"],
    };
    expect(visible(items, view).map((i) => i.rel)).toEqual(["docs/Setup.md"]);
  });

  test("1 ページ 9 件で、範囲外のページ番号は丸める", () => {
    const many = Array.from({ length: 20 }, (_, i) => i);
    expect(PAGE_SIZE).toBe(9);
    expect(paginate(many, 1)).toEqual({
      page: 1,
      pages: 3,
      slice: many.slice(9, 18),
    });
    expect(paginate(many, 99).page).toBe(2);
    expect(paginate([], 3)).toEqual({ page: 0, pages: 1, slice: [] });
  });
});

describe("parseArgs", () => {
  test("タブ・all・絞り込み語を読む", () => {
    expect(parseArgs("reports all login", DEFAULT_VIEW)).toEqual({
      kind: "view",
      view: { tab: "reports", page: 0, allProjects: true, filter: ["login"] },
    });
    expect(parseArgs("", DEFAULT_VIEW)).toEqual({
      kind: "view",
      view: DEFAULT_VIEW,
    });
  });

  test("open N は番号を返し、番号でなければ使い方", () => {
    expect(parseArgs("open 3", DEFAULT_VIEW)).toEqual({
      kind: "open",
      index: 3,
    });
    expect(parseArgs("open x", DEFAULT_VIEW)).toEqual({ kind: "usage" });
    expect(parseArgs("open 0", DEFAULT_VIEW)).toEqual({ kind: "usage" });
  });
});

describe("表示用の文字列", () => {
  test("制御文字と bidi を置き換え、長さを切る", () => {
    expect(displayText("a\u0007b\u202ec\nd", 100)).toBe(
      "a\uFFFDb\uFFFDc\uFFFDd",
    );
    expect(displayText("x".repeat(10), 5)).toBe("xxxx…");
  });

  test("ゼロ幅・BOM・タグ文字・アラビア文字マークなど不可視の書式文字も置き換える", () => {
    expect(displayText("a\u200bb\ufeffc\u{E0041}d\u061ce\u2060f", 100)).toBe(
      "a\uFFFDb\uFFFDc\uFFFDd\uFFFDe\uFFFDf",
    );
  });

  test("異体字セレクタ・ハングルの空白文字など、既定で無視される文字も置き換える", () => {
    expect(displayText("a\ufe0fb\u{E0100}c\u3164d", 100)).toBe(
      "a\uFFFDb\uFFFDc\uFFFDd",
    );
  });

  test("本文用の一覧は 20 件までで番号付き", () => {
    const many = docItems(
      Array.from({ length: 25 }, (_, i) => `d${String(i).padStart(2, "0")}.md`),
    );
    const lines = formatListing(many);
    expect(lines).toHaveLength(20);
    expect(lines[0]).toBe("1. d00.md");
    expect(lines[19]).toBe("20. d19.md");
  });
});

describe("newestFirst", () => {
  test("同じ時刻なら相対パスで並べ、順序が毎回同じになる", () => {
    const a = {
      ...reportItems("b", [file("20261104-100000-x.html")], true)[0]!,
    };
    const b = {
      ...reportItems("a", [file("20261104-100000-x.html")], true)[0]!,
    };
    const c = {
      ...reportItems("a", [file("20261105-000000-y.html")], true)[0]!,
    };
    expect([a, b, c].sort(newestFirst).map((i) => i.rel)).toEqual([
      "a/20261105-000000-y.html",
      "a/20261104-100000-x.html",
      "b/20261104-100000-x.html",
    ]);
  });
});
