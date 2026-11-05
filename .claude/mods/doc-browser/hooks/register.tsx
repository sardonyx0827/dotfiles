import { atom, read, update } from "claude-code";
import type { EngineInterface, Register } from "claude-code";

import type { DocContext, DocItem, DocStatus, DocView } from "../types";
import {
  DEFAULT_VIEW,
  MAX_TEXT_ITEMS,
  TABS,
  TAB_LABEL,
  displayText,
  docItems,
  formatListing,
  newestFirst,
  paginate,
  parseArgs,
  parseLsFiles,
  reportItems,
  summarize,
  visible,
} from "./catalog";
import type { Listed } from "./catalog";
import {
  extensionOf,
  isInside,
  isSafeRel,
  joinPath,
  lastLine,
  openArgv,
  openerFor,
  previewDir,
  renderArgv,
} from "./paths";

// リポジトリの Markdown と、session-report / session-dossier / codebase-survey が
// 書いたレポートを一覧し、選んだものをブラウザで開くだけの mod。ツールの実行可否に関わる
// イベント (tool.call / tool.check など) には一切フックしない: mod は settings
// hook の前後で判定を差し替えられるため、触れると bash-review などを素通りさせる
// 余地が生まれる (tests/test_config_wiring.py が静的に検査している)。
//
// 開く直前に毎回、項目の相対パスを根に結び直して realPath を取り、
// 「許可した根の下にある通常ファイルで、拡張子が .md / .html」であることを
// 確かめる。外部コマンドはシェルを通さず argv で、絶対パスだけを渡す。

const COMMAND = "doc-browser";
const PANE = "doc-browser";
const TITLE = "doc-browser";
const USAGE = `使い方: /${COMMAND} [docs|config|reports] [all] [絞り込み語 ...] | open <番号>`;
const PANE_COLUMNS = 100;
const GIT_TIMEOUT_MS = 10_000;
const RENDER_TIMEOUT_MS = 60_000;
const OPEN_TIMEOUT_MS = 10_000;
const PRIMARY = { variant: "primary" } as const;

const itemsAtom = atom(
  { plugin: "doc-browser", key: "items" } as const,
  [] as DocItem[],
);
const viewAtom = atom(
  { plugin: "doc-browser", key: "view" } as const,
  DEFAULT_VIEW as DocView,
);
const contextAtom = atom(
  { plugin: "doc-browser", key: "context" } as const,
  null as DocContext | null,
);
const statusAtom = atom(
  { plugin: "doc-browser", key: "status" } as const,
  null as DocStatus | null,
);
const listingAtom = atom(
  { plugin: "doc-browser", key: "lastListing" } as const,
  [] as string[],
);

// モジュール変数はホットリロードで初期化される。opener 名は uname から引き直せる。
let opener: "open" | "xdg-open" | undefined;

function baseName(path: string): string {
  return path
    .slice(path.replace(/\/+$/, "").lastIndexOf("/") + 1)
    .replace(/\/+$/, "");
}

async function realPathOf(
  $: EngineInterface,
  path: string,
): Promise<string | undefined> {
  try {
    return (await $.fs.stat(path, { resolve: true })).realPath;
  } catch {
    return undefined;
  }
}

async function resolveContext($: EngineInterface): Promise<DocContext> {
  const home = await $.env.get("HOME");
  if (home === undefined || home === "") throw new Error("HOME is not set");
  const configDir = (await $.env.get("CLAUDE_CONFIG_DIR")) || `${home}/.claude`;
  const cacheDir = (await $.env.get("XDG_CACHE_HOME")) || `${home}/.cache`;
  const sessionRoot = await $.session.root();
  let repoRoot: string | null = null;
  try {
    const rev = await $.process.run(
      [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-C",
        sessionRoot,
        "rev-parse",
        "--show-toplevel",
      ],
      { timeoutMs: GIT_TIMEOUT_MS },
    );
    const top = rev.exitCode === 0 ? rev.stdout.trim() : "";
    repoRoot = top === "" ? null : ((await realPathOf($, top)) ?? null);
  } catch {
    repoRoot = null;
  }

  return {
    repoRoot,
    project: repoRoot === null ? "no-repo" : baseName(repoRoot),
    reportsRoot: `${configDir}/reports`,
    previewRoot: `${cacheDir}/claude-doc-browser`,
    renderer: `${configDir}/skills/session-report/render_report.py`,
  };
}

async function listDocs(
  $: EngineInterface,
  ctx: DocContext,
): Promise<DocItem[]> {
  if (ctx.repoRoot === null) return [];
  // fsmonitor は .git/config から任意のコマンドを起動できるので切る。
  // -z で改行や引用符を含む名前も崩さず受け取る。
  const ls = await $.process.run(
    [
      "git",
      "-c",
      "core.fsmonitor=false",
      "-C",
      ctx.repoRoot,
      "ls-files",
      "-z",
      "--cached",
      "--others",
      "--exclude-standard",
      "--",
      "*.md",
    ],
    { timeoutMs: GIT_TIMEOUT_MS },
  );

  if (ls.exitCode !== 0) return [];
  // 出力が上限で切れたら、最後の NUL より後ろは途中で切れた名前なので捨てる。
  const stdout = ls.isStdoutTruncated
    ? ls.stdout.slice(0, ls.stdout.lastIndexOf("\0") + 1)
    : ls.stdout;

  return docItems(parseLsFiles(stdout));
}

async function listDir($: EngineInterface, path: string): Promise<Listed[]> {
  try {
    return (await $.fs.list(path)).map((entry) => ({
      name: entry.name,
      kind: entry.kind,
      isLink: entry.isLink,
    }));
  } catch {
    // レポート置き場がまだ無いなど。空として扱う。
    return [];
  }
}

async function listReports(
  $: EngineInterface,
  ctx: DocContext,
  allProjects: boolean,
): Promise<DocItem[]> {
  if (!allProjects) {
    return reportItems(
      ctx.project,
      await listDir($, joinPath(ctx.reportsRoot, ctx.project)),
      false,
    );
  }
  const projects = (await listDir($, ctx.reportsRoot)).filter(
    (entry) => entry.kind === "dir" && !entry.isLink,
  );
  const items: DocItem[] = [];
  for (const project of projects) {
    items.push(
      ...reportItems(
        project.name,
        await listDir($, joinPath(ctx.reportsRoot, project.name)),
        true,
      ),
    );
  }

  return items.sort(newestFirst);
}

async function reload($: EngineInterface, view: DocView): Promise<DocItem[]> {
  const ctx = await resolveContext($);
  const items = [
    ...(await listDocs($, ctx)),
    ...(await listReports($, ctx, view.allProjects)),
  ];
  await update($, contextAtom, () => ctx);
  await update($, itemsAtom, () => items);

  return items;
}

async function openerName($: EngineInterface): Promise<"open" | "xdg-open"> {
  if (opener === undefined) {
    const uname = await $.process.run(["uname", "-s"], {
      timeoutMs: OPEN_TIMEOUT_MS,
    });
    opener = openerFor(uname.stdout);
  }

  return opener;
}

type Outcome = { ok: true; text: string } | { ok: false; text: string };

function refuse(item: DocItem, reason: string): Outcome {
  return {
    ok: false,
    text: `開けません: ${item.label}（${displayText(reason, 200)}）`,
  };
}

// path の realPath が root の下の通常ファイルで、拡張子が ext ならその realPath。
async function checked(
  $: EngineInterface,
  path: string,
  root: string,
  ext: readonly string[],
): Promise<string | undefined> {
  let stat;
  try {
    stat = await $.fs.stat(path, { resolve: true });
  } catch {
    return undefined;
  }
  const real = stat.realPath;
  if (real === undefined || stat.kind !== "file") return undefined;
  if (!isInside(real, root) || !ext.includes(extensionOf(real)))
    return undefined;

  return real;
}

async function launch(
  $: EngineInterface,
  item: DocItem,
  path: string,
): Promise<Outcome> {
  const result = await $.process.run(openArgv(await openerName($), path), {
    timeoutMs: OPEN_TIMEOUT_MS,
  });

  // 失敗文はモデルも読むので、絶対パス (ホームのユーザー名を含む) は載せない。
  return result.exitCode === 0
    ? { ok: true, text: `開きました: ${item.label}` }
    : refuse(item, "ブラウザを起動できませんでした");
}

// プレビュー置き場の realPath。初回はまだ無いので、親 (~/.cache) を解決して足す。
async function previewRealPath(
  $: EngineInterface,
  previewRoot: string,
): Promise<string> {
  const own = await realPathOf($, previewRoot);
  if (own !== undefined) return own;
  const slash = previewRoot.lastIndexOf("/");
  const parent = await realPathOf($, previewRoot.slice(0, slash));

  return parent === undefined
    ? previewRoot
    : joinPath(parent, previewRoot.slice(slash + 1));
}

// 根ごとに開いてよい拡張子。リポジトリの .html は生のまま開くとスクリプトが動く
// ので、Markdown だけを renderer (サニタイズ付き) に通して開く。
const OPENABLE: Record<DocItem["root"], readonly string[]> = {
  repo: [".md"],
  reports: [".md", ".html"],
};

async function openItem($: EngineInterface, item: DocItem): Promise<Outcome> {
  const ctx = await read($, contextAtom);
  if (ctx === null || !isSafeRel(item.rel))
    return refuse(item, "一覧を読み直してください");
  const root =
    item.root === "repo"
      ? ctx.repoRoot
      : ((await realPathOf($, ctx.reportsRoot)) ?? null);
  if (root === null) return refuse(item, "置き場所が見つかりません");
  const real = await checked(
    $,
    joinPath(root, item.rel),
    root,
    OPENABLE[item.root],
  );
  if (real === undefined)
    return refuse(item, "許可した場所の外か、開けない種類のファイルです");
  if (extensionOf(real) === ".html") return launch($, item, real);

  // Markdown は session-report の renderer で HTML にしてから開く。出力はどちらの
  // 根の文書もプレビュー置き場 (~/.cache) に書く: リポジトリの git status を
  // 汚さず、レポート置き場に置かれた symlink を辿って別のファイルを書き換える
  // こともない。
  const preview = await previewRealPath($, ctx.previewRoot);
  if (
    ctx.repoRoot !== null &&
    (preview === ctx.repoRoot || isInside(preview, ctx.repoRoot))
  ) {
    return refuse(item, "プレビューの置き場所がリポジトリの中にあります");
  }
  const outDir =
    item.root === "repo"
      ? previewDir(ctx.previewRoot, ctx.project, item.rel)
      : joinPath(
          joinPath(ctx.previewRoot, "_reports"),
          item.rel.slice(0, item.rel.lastIndexOf("/")),
        );
  let rendered;
  try {
    rendered = await $.process.run(renderArgv(ctx.renderer, outDir, real), {
      timeoutMs: RENDER_TIMEOUT_MS,
    });
  } catch (error) {
    // 詳細 (パスを含みうる) はデバッグログへ。本文とペインには固定の文言だけ。
    $.ui.log(`doc-browser: render failed: ${String(error)}`, { to: "debug" });
    return refuse(item, "描画できませんでした");
  }
  if (rendered.exitCode !== 0) {
    $.ui.log(`doc-browser: render failed: ${lastLine(rendered.stderr)}`, {
      to: "debug",
    });
    return refuse(item, "描画に失敗しました");
  }
  // renderer は <出力先>/<元の名前>.html に書く。標準出力は信用せず、ここで組み立てる。
  const stem = real.slice(real.lastIndexOf("/") + 1).replace(/\.md$/i, "");
  const html = await checked($, joinPath(outDir, `${stem}.html`), preview, [
    ".html",
  ]);
  if (html === undefined) return refuse(item, "描画結果が見つかりません");

  return launch($, item, html);
}

async function openAndReport(
  $: EngineInterface,
  item: DocItem,
): Promise<Outcome> {
  let outcome: Outcome;
  try {
    outcome = await openItem($, item);
  } catch (error) {
    // 詳細 (パスを含みうる) はデバッグログへ。本文とペインには固定の文言だけ。
    $.ui.log(`doc-browser: open failed: ${String(error)}`, { to: "debug" });
    outcome = refuse(item, "想定外のエラーが起きました");
  }
  await update($, statusAtom, () => ({
    kind: outcome.ok ? "info" : "error",
    text: outcome.text,
  }));

  return outcome;
}

async function turnPage($: EngineInterface, delta: number): Promise<void> {
  const items = await read($, itemsAtom);
  await update($, viewAtom, (view) => ({
    ...view,
    page: paginate(visible(items, view), view.page + delta).page,
  }));
}

async function runCommand(
  $: EngineInterface,
  args: string,
): Promise<{ text: string }> {
  const parsed = parseArgs(args, await read($, viewAtom));
  if (parsed.kind === "usage") return { text: USAGE };
  if (parsed.kind === "open") {
    const id = (await read($, listingAtom))[parsed.index - 1];
    const item = (await read($, itemsAtom)).find(
      (candidate) => candidate.id === id,
    );
    if (item === undefined) return { text: USAGE };

    return { text: (await openAndReport($, item)).text };
  }

  let items: DocItem[];
  try {
    items = await reload($, parsed.view);
  } catch (error) {
    $.ui.log(`doc-browser: listing failed: ${String(error)}`, { to: "debug" });
    return {
      text: "doc-browser: 一覧を作れませんでした（詳細はデバッグログ）",
    };
  }
  await update($, viewAtom, () => parsed.view);
  await update($, statusAtom, () => null);
  const ctx = await read($, contextAtom);
  const heading = displayText(summarize(items, ctx?.project ?? "no-repo"), 200);
  const opened = await $.ui.open({
    id: PANE,
    title: TITLE,
    columns: PANE_COLUMNS,
    focus: true,
    closeOnEscape: true,
  });
  if (opened.isPlaced) {
    // 前回本文に返した番号は、この一覧とは対応しないので使えなくする。
    await update($, listingAtom, () => []);

    return { text: heading };
  }

  // ペインを描けない場所では番号付きの一覧を本文で返す。本文はモデルも読むので
  // 件数を絞り、open N がずれないよう返した並びを覚えておく。
  const shown = visible(items, parsed.view).slice(0, MAX_TEXT_ITEMS);
  await update($, listingAtom, () => shown.map((item) => item.id));

  return { text: [heading, ...formatListing(shown)].join("\n") };
}

export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    // ここで投げるとセッションの開始 (next) まで止めてしまうので、握って続ける。
    try {
      await $.command.register({
        name: COMMAND,
        description:
          "リポジトリの Markdown とセッションレポートを一覧し、選んだものをブラウザで開く",
        argumentHint: "[docs|config|reports] [all] [絞り込み語] | open <番号>",
        immediate: true,
      });
    } catch (error) {
      $.ui.log(`doc-browser: session.start failed: ${String(error)}`, {
        to: "debug",
      });
    }

    return next(e);
  });

  on("command.run", { command: COMMAND }, async ($, e) => {
    // 投げたまま返すとコマンドの結果が届かないので、理由を本文にして返す。
    // e.args は型の上では string だが、別プラグインが args なしで呼ぶと空になる。
    try {
      return await runCommand($, e.args ?? "");
    } catch (error) {
      $.ui.log(`doc-browser: command failed: ${String(error)}`, {
        to: "debug",
      });
      return { text: "doc-browser: 失敗しました（詳細はデバッグログ）" };
    }
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e);
    const items = await read($, itemsAtom);
    const view = await read($, viewAtom);
    const ctx = await read($, contextAtom);
    const status = await read($, statusAtom);
    const matched = visible(items, view);
    const { page, pages, slice } = paginate(matched, view.page);
    const scope =
      view.tab === "reports" && view.allProjects ? "（全プロジェクト）" : "";
    const filter =
      view.filter.length === 0 ? "" : ` · 絞り込み: ${view.filter.join(" ")}`;

    return (
      <Box flexDirection="column">
        <Text bold>
          {displayText(summarize(items, ctx?.project ?? "no-repo"), 200)}
        </Text>
        <Box flexDirection="row" flexWrap="wrap" columnGap={1}>
          {TABS.map((tab) => (
            <Button
              key={`tab-${tab}`}
              hotkey={tab === "docs" ? "d" : tab === "config" ? "c" : "s"}
              label={TAB_LABEL[tab]}
              {...(view.tab === tab ? PRIMARY : {})}
              onPress={() =>
                update($, viewAtom, (current) => ({ ...current, tab, page: 0 }))
              }
            />
          ))}
          <Button
            key="all-projects"
            hotkey="a"
            label="全プロジェクト"
            {...(view.allProjects ? PRIMARY : {})}
            onPress={async () => {
              const next = {
                ...view,
                tab: "reports" as const,
                page: 0,
                allProjects: !view.allProjects,
              };
              await reload($, next);
              await update($, viewAtom, () => next);
            }}
          />
          <Button
            key="reload"
            hotkey="r"
            label="再読込"
            onPress={() => reload($, view)}
          />
          <Button
            key="close"
            role="dismiss"
            label="閉じる"
            onPress={() => void $.ui.close({ id: PANE })}
          />
        </Box>
        <Text dimColor>
          {`${TAB_LABEL[view.tab]}${scope} · ${page + 1}/${pages} ページ · ${matched.length} 件${displayText(filter, 200)}`}
        </Text>
        {slice.length === 0 && <Text dimColor>該当する文書はありません</Text>}
        {slice.map((item, index) => (
          <Button
            key={`item:${item.id}`}
            plain
            hotkey={String(index + 1)}
            onPress={() => openAndReport($, item)}
          >
            {item.label} <Text dimColor>{item.detail}</Text>
          </Button>
        ))}
        <Box flexDirection="row" columnGap={1}>
          <Button
            key="prev"
            hotkey="p"
            label="前へ"
            onPress={() => turnPage($, -1)}
          />
          <Button
            key="next"
            hotkey="n"
            label="次へ"
            onPress={() => turnPage($, 1)}
          />
        </Box>
        {status !== null && (
          <Text color={status.kind === "error" ? "red" : "green"}>
            {displayText(status.text, 400)}
          </Text>
        )}
      </Box>
    );
  });
};
