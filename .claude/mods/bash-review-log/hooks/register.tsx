import { atom, read, update } from "claude-code";
import type { EngineInterface, Register } from "claude-code";

import type { BashReviewLogEntry, BashReviewLogView } from "../types";
import type { Detail } from "./log";
import {
  DEFAULT_VIEW,
  FULL_MAX,
  TYPES,
  attachDetails,
  commandOf,
  countDecisions,
  describeView,
  displayText,
  formatEntry,
  labelText,
  oneLineText,
  parseArgs,
  parseDetail,
  parseLog,
  selectEntries,
  summarize,
  toggleType,
} from "./log";

// bash-review (PreToolUse の settings hook) が書くログを読んで見せるだけの mod。
// tool.call / tool.check などツールの可否に関わるイベントには一切フックしない:
// mod は settings hook の前後で判定を差し替えられるため、触れると bash-review /
// git-push-review / permissions.ask を素通りさせる余地が生まれる
// (tests/test_config_wiring.py が静的に検査している)。

const COMMAND = "bash-review-log";
const PANE = "bash-review-log";
const TITLE = "bash-review log";
const USAGE = `使い方: /${COMMAND} [all|allow|ask|deny|error|flagged ...] [件数 1-500] [full|short]`;
const LOG_RELATIVE = ".claude/logs/bash-review.log";
// bash-review.py の log_dir。サマリーで 80 字に切られたコマンドの全文はここにある。
const DETAIL_DIR = "/tmp/claude_hooks/logs/PreToolUse/Bash/bash-review";
const DETAIL_NAME = /^bash_cmd_(\d+)_\d+\.log$/;
// サマリーの件数に足して読む詳細ログの本数 (サマリーに残らなかった実行の分)。
const DETAIL_SLACK = 100;
const DETAIL_BATCH = 50;
// 詳細ログ 1 本の上限。通常は数 KB で、これを超えるものは読まない。
const DETAIL_MAX_BYTES = 256 * 1024;
// ペインを描けない場所で本文 (モデルも読む) に載せる件数の上限。
const MAX_TEXT_ENTRIES = 20;
// 横に並べたときに望むペインの幅 (利用者が動かした幅が優先される)。
const PANE_COLUMNS = 100;
// ペインを開いている間、ログの更新を拾う間隔。
const POLL_MS = 2000;

const DECISION_COLOR: Record<string, string> = {
  ALLOW: "green",
  ASK: "yellow",
  DENY: "red",
};
const PRIMARY = { variant: "primary" } as const;

const entriesAtom = atom(
  { plugin: "bash-review-log", key: "entries" } as const,
  [] as BashReviewLogEntry[],
);
const viewAtom = atom(
  { plugin: "bash-review-log", key: "view" } as const,
  DEFAULT_VIEW as BashReviewLogView,
);
const errorAtom = atom(
  { plugin: "bash-review-log", key: "error" } as const,
  null as string | null,
);

// モジュール変数はホットリロードで初期化される。ペインの開閉は session.start
// (リロード時にも再発火する) で $.ui.panes() から取り直す。
let isOpen = false;
let lastMtimeMs = -1;
// 詳細ログは書かれた後に変わらないので、読めた結果をファイル名で覚えておく
// (null は「読めたが Tool Input が使えない」= 伏せ字など)。
const detailCache = new Map<string, Detail | null>();
// 2 秒ごとの再読込と、ボタン・コマンドからの読み込みを重ねないための共有。
let inFlight:
  | Promise<{ entries: BashReviewLogEntry[] } | { error: string }>
  | undefined;

async function logPath($: EngineInterface): Promise<string> {
  const home = await $.env.get("HOME");
  if (home === undefined || home === "") throw new Error("HOME is not set");

  return `${home}/${LOG_RELATIVE}`;
}

// ファイル名の time_ns (レビュー開始時刻) の順。桁数をそろえてから文字列で比べる。
function byStartTime(a: string, b: string): number {
  const left = (DETAIL_NAME.exec(a)?.[1] ?? "").padStart(24, "0");
  const right = (DETAIL_NAME.exec(b)?.[1] ?? "").padStart(24, "0");

  return left < right ? -1 : left > right ? 1 : 0;
}

// 新しい方から count 本の詳細ログを古い順で返す。読めないものは飛ばす。
async function readDetails(
  $: EngineInterface,
  count: number,
): Promise<Detail[]> {
  let names: string[];
  try {
    // /tmp は誰でも書けるので、symlink・FIFO・巨大なファイルは読まない。
    names = (await $.fs.list(DETAIL_DIR))
      .filter(
        (entry) =>
          entry.kind === "file" &&
          !entry.isLink &&
          entry.size <= DETAIL_MAX_BYTES &&
          DETAIL_NAME.test(entry.name),
      )
      .map((entry) => entry.name);
  } catch {
    // 再起動で /tmp が空になった直後など。切れたコマンドのまま見せる。
    return [];
  }
  const recent = names.sort(byStartTime).slice(-count);
  const kept = new Set(recent);
  for (const name of detailCache.keys()) {
    if (!kept.has(name)) detailCache.delete(name);
  }
  const unread = recent.filter((name) => !detailCache.has(name));
  for (let i = 0; i < unread.length; i += DETAIL_BATCH) {
    await Promise.all(
      unread.slice(i, i + DETAIL_BATCH).map(async (name) => {
        try {
          const text = await $.fs.read(`${DETAIL_DIR}/${name}`);
          detailCache.set(name, parseDetail(text) ?? null);
        } catch {
          // prune_dir() が一覧の直後に消したものなど。覚えずに次回また試す。
        }
      }),
    );
  }

  return recent.flatMap((name) => {
    const detail = detailCache.get(name);

    return detail ? [detail] : [];
  });
}

// ログを読み直して state に入れる。読み込み中に呼ばれたら、その結果を待つ。
async function load(
  $: EngineInterface,
): Promise<{ entries: BashReviewLogEntry[] } | { error: string }> {
  inFlight ??= reload($).finally(() => {
    inFlight = undefined;
  });

  return inFlight;
}

// 読めなければ error に理由を残す。
async function reload(
  $: EngineInterface,
): Promise<{ entries: BashReviewLogEntry[] } | { error: string }> {
  let path = `~/${LOG_RELATIVE}`;
  try {
    path = await logPath($);
    const { mtimeMs } = await $.fs.stat(path);
    const parsed = parseLog(await $.fs.read(path));
    const details = await readDetails($, parsed.length + DETAIL_SLACK);
    const entries = attachDetails(parsed, details);
    lastMtimeMs = mtimeMs;
    await update($, entriesAtom, () => entries);
    await update($, errorAtom, () => null);

    return { entries };
  } catch {
    const error = `bash-review: ログを読めませんでした (${path})`;
    await update($, errorAtom, () => error);

    return { error };
  }
}

async function refreshIfChanged($: EngineInterface): Promise<void> {
  if (!isOpen) return;
  try {
    const { mtimeMs } = await $.fs.stat(await logPath($));
    if (mtimeMs !== lastMtimeMs) await load($);
  } catch {
    // ローテーションの差し替え中などで一瞬見えないだけなら、次の周期で拾う。
  }
}

export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    // ここで投げるとセッションの開始 (next) まで止めてしまうので、握って続ける。
    try {
      await $.command.register({
        name: COMMAND,
        description:
          "bash-review の判定ログをペインで表示 (種別・件数・全文表示を引数で指定)",
        argumentHint: "[all|allow|ask|deny|error|flagged] [件数] [full|short]",
        immediate: true,
      });
      isOpen = (await $.ui.panes()).some((pane) => pane.id === PANE);
    } catch (error) {
      $.ui.log(`bash-review-log: session.start failed: ${String(error)}`, {
        to: "debug",
      });
    }
    $.clock.every(POLL_MS, () => void refreshIfChanged($));

    return next(e);
  });

  on("command.run", { command: COMMAND }, async ($, e) => {
    // 型の上では string だが、別プラグインが $.command.run を args なしで呼ぶと空になる。
    const view = parseArgs(e.args ?? "");
    if (view === undefined) return { text: USAGE };
    await update($, viewAtom, () => view);

    const loaded = await load($);
    if ("error" in loaded) return { text: loaded.error };

    const { entries } = loaded;
    const heading = `${summarize(entries)}（表示: ${describeView(view)}）`;
    const opened = await $.ui.open({
      id: PANE,
      title: TITLE,
      columns: PANE_COLUMNS,
    });
    isOpen = true;
    if (opened.isPlaced) return { text: heading };

    // ペインを描けない場所では、条件に合う分を本文で返す。本文はモデルも読むので
    // 件数を絞り、コマンドはサマリーの 80 字まで (formatEntry) にとどめる。
    const shown = selectEntries(
      entries,
      view.types,
      Math.min(view.limit, MAX_TEXT_ENTRIES),
    );

    return { text: [heading, ...shown.map(formatEntry)].join("\n") };
  });

  on("ui.close", { id: PANE }, ($, e, next) => {
    isOpen = false;

    return next(e);
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e);
    const entries = await read($, entriesAtom);
    const view = await read($, viewAtom);
    const error = await read($, errorAtom);
    const counts = countDecisions(entries);
    const matched = selectEntries(entries, view.types, entries.length);
    const shown = matched.slice(0, view.limit);
    const wrap = view.isFull ? "wrap" : "truncate-end";

    return (
      <Box flexDirection="column">
        <Box flexDirection="row" flexWrap="wrap" columnGap={2}>
          <Text bold>{entries.length} 件</Text>
          <Text color="green">ALLOW {counts.allow}</Text>
          <Text color="yellow">ASK {counts.ask}</Text>
          <Text color="red">DENY {counts.deny}</Text>
          <Text color="magenta">ERROR {counts.error}</Text>
        </Box>
        <Box flexDirection="row" flexWrap="wrap" columnGap={1}>
          <Button
            key="type-all"
            hotkey="0"
            label="すべて"
            {...(view.types.length === 0 ? PRIMARY : {})}
            onPress={() =>
              update($, viewAtom, (current) => ({ ...current, types: [] }))
            }
          />
          {TYPES.map((type, index) => (
            <Button
              key={`type-${type}`}
              hotkey={String(index + 1)}
              label={type}
              {...(view.types.includes(type) ? PRIMARY : {})}
              onPress={() =>
                update($, viewAtom, (current) => toggleType(current, type))
              }
            />
          ))}
          <Button
            key="full"
            hotkey="w"
            label={view.isFull ? "1 行で表示" : "全文を表示"}
            onPress={() =>
              update($, viewAtom, (current) => ({
                ...current,
                isFull: !current.isFull,
              }))
            }
          />
          <Button
            key="reload"
            hotkey="r"
            label="再読込"
            onPress={() => void load($)}
          />
          <Button
            key="close"
            role="dismiss"
            label="閉じる"
            onPress={() => void $.ui.close({ id: PANE })}
          />
        </Box>
        <Text dimColor>
          {describeView(view)} — 該当 {matched.length} 件中 {shown.length}{" "}
          件を表示
        </Text>
        {error !== null && <Text color="red">{error}</Text>}
        {error === null && shown.length === 0 && (
          <Text dimColor>該当する判定はありません</Text>
        )}
        {shown.map((entry) => (
          <Box flexDirection="column" marginTop={1}>
            <Text wrap="truncate-end">
              <Text dimColor>{entry.at.slice(5)} </Text>
              <Text bold color={DECISION_COLOR[entry.decision] ?? "white"}>
                {labelText(entry.decision)}
              </Text>
              <Text dimColor> {labelText(entry.stage)}</Text>
              {entry.description === undefined
                ? ""
                : `  ${oneLineText(entry.description)}`}
            </Text>
            <Text wrap={wrap}>
              {"$ "}
              {view.isFull
                ? displayText(commandOf(entry), FULL_MAX)
                : oneLineText(commandOf(entry))}
            </Text>
            <Text dimColor wrap={wrap}>
              {"↳ "}
              {view.isFull
                ? displayText(entry.reason, FULL_MAX)
                : oneLineText(entry.reason)}
            </Text>
          </Box>
        ))}
      </Box>
    );
  });
};
