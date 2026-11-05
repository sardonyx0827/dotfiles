// 一覧の組み立て (純粋): git ls-files とレポート置き場の一覧を DocItem にし、
// タブ・絞り込み・ページに切り分け、引数を読む。I/O は register.tsx が持つ。

import type { DocItem, DocKind, DocTab, DocView } from "../types";
import { isSafeRel } from "./paths";

export const PAGE_SIZE = 9;
// ペインを置けない場所で本文 (モデルも読む) に載せる件数の上限。
export const MAX_TEXT_ITEMS = 20;
export const LABEL_MAX = 200;
export const TABS: readonly DocTab[] = ["docs", "config", "reports"];
export const TAB_LABEL: Record<DocTab, string> = {
  docs: "ドキュメント",
  config: "設定系",
  reports: "レポート",
};
export const DEFAULT_VIEW: DocView = {
  tab: "docs",
  page: 0,
  allProjects: false,
  filter: [],
};

const KIND_LABEL: Record<DocKind, string> = {
  doc: "",
  report: "レポート",
  dossier: "詳細記録",
  "dossier-share": "詳細記録（共有版）",
  survey: "調査",
};

// <YYYYMMDD>-<HHMMSS>-<slug>[.share|.artifact].<md|html|json>
// session-report と codebase-survey は md と html を、session-dossier は html と json を書く。
const REPORT_NAME =
  /^(\d{8})-(\d{6})-(.+?)(\.share|\.artifact)?\.(md|html|json)$/;
const DOSSIER_SLUG = /^dossier-[0-9A-Za-z-]+$/;
// codebase-survey の命名規則。session-dossier の dossier_scope.py の _SURVEY_MARKDOWN と
// 同じ形なので、変えるときは両方を揃える。
const SURVEY_SLUG = /^survey-[0-9A-Za-z-]+$/;

// 制御文字 (Cc)・書式文字 (Cf: ゼロ幅、BOM、bidi の書き換え、タグ文字)・既定で無視される
// 文字 (異体字セレクタ、ハングルの空白文字など) と行区切り。
// ファイル名に混ぜると表示を偽ったり、モデルが読む本文に不可視の文を埋めたりできる。
const UNSAFE_CHARS =
  /[\p{Cc}\p{Cf}\p{Default_Ignorable_Code_Point}\u2028\u2029]/gu;

export function displayText(text: string, max: number): string {
  const clean = text.replace(UNSAFE_CHARS, "\uFFFD");
  const chars = Array.from(clean);

  return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : clean;
}

export function parseLsFiles(stdout: string): string[] {
  return [...new Set(stdout.split("\0").filter((name) => name !== ""))];
}

function baseName(rel: string): string {
  return rel.slice(rel.lastIndexOf("/") + 1);
}

function dirName(rel: string): string {
  const slash = rel.lastIndexOf("/");

  return slash === -1 ? "" : rel.slice(0, slash + 1);
}

function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function docItems(rels: string[]): DocItem[] {
  const items: DocItem[] = rels.filter(isSafeRel).map((rel) => ({
    id: `doc:${rel}`,
    kind: "doc",
    tab: rel.startsWith(".") ? "config" : "docs",
    label: displayText(baseName(rel), LABEL_MAX),
    detail: displayText(dirName(rel), LABEL_MAX),
    root: "repo",
    rel,
  }));
  const rank = (item: DocItem) =>
    `${TABS.indexOf(item.tab)}${item.rel.includes("/") ? 1 : 0}`;

  return items.sort(
    (a, b) => compare(rank(a), rank(b)) || compare(a.rel, b.rel),
  );
}

export type Listed = {
  name: string;
  kind: "file" | "dir" | "other";
  isLink: boolean;
};

type ReportGroup = {
  stamp: string;
  slug: string;
  kind: DocKind;
  html?: string;
  md?: string;
};

function stampText(stamp: string): string {
  const [d = "", t = ""] = stamp.split("-");

  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)} ${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

// project はレポート置き場直下のディレクトリ名。entries はその中の一覧。
export function reportItems(
  project: string,
  entries: Listed[],
  allProjects: boolean,
): DocItem[] {
  if (!isSafeRel(project) || project.includes("/")) return [];
  const groups = new Map<string, ReportGroup>();
  for (const entry of entries) {
    // symlink とファイル以外 (ディレクトリ・FIFO) は開かない。
    if (entry.kind !== "file" || entry.isLink) continue;
    const match = REPORT_NAME.exec(entry.name);
    if (match === null) continue;
    const [, date = "", time = "", slug = "", suffix, ext] = match;
    const isDossier = DOSSIER_SLUG.test(slug);
    // manifest と、Artifact に載せるための断片 (単体では読むページでない) は出さない。
    if (ext === "json" || suffix === ".artifact") continue;
    if (suffix === ".share" && !isDossier) continue;
    const kind: DocKind = isDossier
      ? suffix === ".share"
        ? "dossier-share"
        : "dossier"
      : SURVEY_SLUG.test(slug)
        ? "survey"
        : "report";
    const key = `${date}-${time}-${slug}${suffix ?? ""}`;
    const group: ReportGroup = groups.get(key) ?? {
      stamp: `${date}-${time}`,
      slug,
      kind,
    };
    if (ext === "html") group.html = entry.name;
    else if (!isDossier) group.md = entry.name;
    groups.set(key, group);
  }

  const items: DocItem[] = [];
  for (const [key, group] of groups) {
    const name = group.html ?? group.md;
    if (name === undefined) continue;
    const parts = [stampText(group.stamp), KIND_LABEL[group.kind]];
    if (allProjects) parts.push(project);
    items.push({
      id: `rep:${project}/${key}`,
      kind: group.kind,
      tab: "reports",
      label: displayText(group.slug, LABEL_MAX),
      detail: displayText(parts.join(" · "), LABEL_MAX),
      root: "reports",
      rel: `${project}/${name}`,
      stamp: group.stamp,
    });
  }

  return items.sort(newestFirst);
}

// 新しい順。同じ時刻なら相対パスで並べ、毎回同じ順序にする。
export function newestFirst(a: DocItem, b: DocItem): number {
  return compare(b.stamp ?? "", a.stamp ?? "") || compare(a.rel, b.rel);
}

export function visible(items: DocItem[], view: DocView): DocItem[] {
  const words = view.filter.map((word) => word.toLowerCase());

  return items.filter(
    (item) =>
      item.tab === view.tab &&
      words.every((word) => item.rel.toLowerCase().includes(word)),
  );
}

export function paginate<T>(
  items: T[],
  page: number,
): { page: number; pages: number; slice: T[] } {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const current = Math.min(Math.max(0, page), pages - 1);

  return {
    page: current,
    pages,
    slice: items.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE),
  };
}

export type Parsed =
  | { kind: "view"; view: DocView }
  | { kind: "open"; index: number }
  | { kind: "usage" };

export function parseArgs(args: string, current: DocView): Parsed {
  const words = args
    .trim()
    .split(/\s+/)
    .filter((word) => word !== "");
  if (words[0]?.toLowerCase() === "open") {
    const number = words[1];

    return words.length === 2 &&
      number !== undefined &&
      /^[1-9]\d{0,2}$/.test(number)
      ? { kind: "open", index: Number(number) }
      : { kind: "usage" };
  }
  const view: DocView = {
    tab: current.tab,
    page: 0,
    allProjects: false,
    filter: [],
  };
  for (const word of words) {
    const lower = word.toLowerCase();
    if ((TABS as readonly string[]).includes(lower)) view.tab = lower as DocTab;
    else if (lower === "all") view.allProjects = true;
    else view.filter.push(word);
  }

  return { kind: "view", view };
}

export function formatListing(items: DocItem[]): string[] {
  return items
    .slice(0, MAX_TEXT_ITEMS)
    .map(
      (item, index) =>
        `${index + 1}. ${item.label}${item.detail === "" ? "" : ` — ${item.detail}`}`,
    );
}

export function summarize(items: DocItem[], project: string): string {
  const count = (tab: DocTab) =>
    items.filter((item) => item.tab === tab).length;

  return `${project}  ${TABS.map((tab) => `${TAB_LABEL[tab]} ${count(tab)}`).join(" · ")}`;
}
