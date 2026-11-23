import { atom, read, update } from "claude-code";
import type { EngineInterface, Register } from "claude-code";

import type { BashReviewLogEntry, BashReviewLogView } from "../types";
import type { Detail, TimedDetail } from "./log";
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
// メッセージに出す表記 (絶対パスはユーザー名を含む)。
const LOG_SHOWN = `~/${LOG_RELATIVE}`;
// bash-review.py の log_dir。サマリーで 80 字に切られたコマンドの全文はここにある。
const DETAIL_DIR = "/tmp/claude_hooks/logs/PreToolUse/Bash/bash-review";
const DETAIL_NAME = /^bash_cmd_(\d+)_\d+\.log$/;
// DETAIL_DIR とその上の階層 (フックの ensure_private_dir が検査するのと同じ): どれも
// 自分の所有する実ディレクトリでなければ、置かれたものを信用しない。
const DETAIL_LEVELS = [
  "/tmp/claude_hooks",
  "/tmp/claude_hooks/logs",
  "/tmp/claude_hooks/logs/PreToolUse",
  "/tmp/claude_hooks/logs/PreToolUse/Bash",
  DETAIL_DIR,
];
// サマリーの件数に足して読む詳細ログの本数 (サマリーに残らなかった実行の分)。
const DETAIL_SLACK = 100;
const DETAIL_BATCH = 50;
// 詳細ログ 1 本の上限。通常は数 KB で、これを超えるものは読まない。
const DETAIL_MAX_BYTES = 256 * 1024;
// find / id の待ち時間。
const PROCESS_TIMEOUT_MS = 5_000;
// 詳細ログの読み込み全体の上限。超えたら全文なしで表示する。
const DETAIL_TIMEOUT_MS = 5_000;
// ログの読み込み全体の上限。詰まった読み込みが inFlight を握り続けて、更新を止めないための保険。
// フックの持ち時間 (1 回の呼び出しで 10 秒) より短くして、コマンドの返り値が届くようにする。
const RELOAD_TIMEOUT_MS = 9_000;
// ペインを描けない場所で本文 (モデルも読む) に載せる件数の上限。
const MAX_TEXT_ENTRIES = 20;
// 横に並べたときに望むペインの幅 (利用者が動かした幅が優先される)。
const PANE_COLUMNS = 100;
// ペインを開いている間、ログの更新を拾う間隔。
const POLL_MS = 2000;

// Map で引く: ログ由来の decision が "constructor" や "__proto__" でも、
// Object.prototype の関数を color に渡さない。
const DECISION_COLOR = new Map<string, string>([
  ["ALLOW", "green"],
  ["ASK", "yellow"],
  ["DENY", "red"],
]);
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
// 全文を引けなかった理由 (ログ自体は読めている)。
const noticeAtom = atom(
  { plugin: "bash-review-log", key: "notice" } as const,
  null as string | null,
);

// モジュール変数はホットリロードで初期化される。ペインの開閉は session.start
// (リロード時にも再発火する) で $.ui.panes() から取り直す。
let isOpen = false;
let lastMtimeMs = -1;
// ペインを開いている間だけ動く更新のタイマー。
let poll: { cancel: () => void } | undefined;
// 詳細ログは書かれた後に変わらないので、読めた結果をファイル名で覚えておく
// (detail が null は「読めたが Tool Input が使えない」= 伏せ字など)。書きかけのファイルが
// 空や途中までで読まれても、書き終わると size と mtime が変わるので、同じ値のときだけ使う。
const detailCache = new Map<
  string,
  { size: number; mtimeMs: number; detail: Detail | null }
>();
// 読み始めて、まだ終わっていない詳細ログ。終わらないもの (差し替えられた FIFO など)
// を、更新のたびに読み直して積み上げない。
const pendingReads = new Set<string>();
// 自分の uid (id -u)。セッション中は変わらない。
let myUid: string | undefined;
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

const NOTICE_UNTRUSTED =
  "全文を引けません: 詳細ログの置き場が、自分の所有する実ディレクトリではありません";
const NOTICE_UNVERIFIED =
  "全文を引けません: 詳細ログの所有者を確かめられませんでした";
const NOTICE_TIMEOUT =
  "全文を引けません: 詳細ログの読み込みが時間内に終わりませんでした";

type Within<T> = { done: true; value: T } | { done: false };

// work が ms 以内に終わらなければ { done: false } で先へ進む。work は止められないので、
// 呼び出し側は終わらなかった仕事が残り続けても困らないように作る。
function within<T>(
  $: EngineInterface,
  ms: number,
  work: Promise<T>,
): Promise<Within<T>> {
  return new Promise((resolve, reject) => {
    const timer = $.clock.after(ms, () => resolve({ done: false }));
    work.then(
      (value) => {
        timer.cancel();
        resolve({ done: true, value });
      },
      (error: unknown) => {
        timer.cancel();
        reject(error);
      },
    );
  });
}

// find は symlink を辿らない (-P が既定) ので、-type で実体の種別が、-user で所有者が絞れる。
// 検査と読み出しの間に差し替えられる余地は残る (README)。読み出しは範囲付きなので、
// FIFO に差し替えられても塞がらず、拒まれる。
async function findOwned(
  $: EngineInterface,
  args: readonly string[],
): Promise<string[] | undefined> {
  const found = await $.process.run(["find", ...args], {
    timeoutMs: PROCESS_TIMEOUT_MS,
  });

  return found.exitCode === 0
    ? found.stdout.split("\n").filter(Boolean)
    : undefined;
}

async function currentUid($: EngineInterface): Promise<string | undefined> {
  if (myUid === undefined) {
    const id = await $.process.run(["id", "-u"], {
      timeoutMs: PROCESS_TIMEOUT_MS,
    });
    const text = id.stdout.trim();
    if (id.exitCode === 0 && /^\d+$/.test(text)) myUid = text;
  }

  return myUid;
}

type Trusted = { ok: true; names: Set<string> } | { ok: false; notice: string };

// 置き場の各階層が自分の所有する実ディレクトリで、そこにあるファイルのうち
// 自分の所有する通常ファイルの名前を返す。確かめられなければ使わない。
async function trustedNames($: EngineInterface): Promise<Trusted> {
  try {
    const uid = await currentUid($);
    if (uid === undefined) return { ok: false, notice: NOTICE_UNVERIFIED };
    const dirs = await findOwned($, [
      ...DETAIL_LEVELS,
      "-maxdepth",
      "0",
      "-type",
      "d",
      "-user",
      uid,
    ]);
    if (dirs === undefined) return { ok: false, notice: NOTICE_UNVERIFIED };
    if (!DETAIL_LEVELS.every((level) => dirs.includes(level)))
      return { ok: false, notice: NOTICE_UNTRUSTED };
    const files = await findOwned($, [
      DETAIL_DIR,
      "-mindepth",
      "1",
      "-maxdepth",
      "1",
      "-type",
      "f",
      "-user",
      uid,
      "-name",
      "bash_cmd_*.log",
    ]);
    if (files === undefined) return { ok: false, notice: NOTICE_UNVERIFIED };
    const prefix = `${DETAIL_DIR}/`;

    return {
      ok: true,
      names: new Set(
        files
          .filter((path) => path.startsWith(prefix))
          .map((path) => path.slice(prefix.length)),
      ),
    };
  } catch {
    // process が使えない・find が無い、など。
    return { ok: false, notice: NOTICE_UNVERIFIED };
  }
}

async function readOne(
  $: EngineInterface,
  name: string,
  size: number,
  mtimeMs: number,
): Promise<void> {
  pendingReads.add(name);
  try {
    // 範囲付きの read は、通常ファイル以外 (FIFO・ソケットなど) では何も読まずに拒まれる。
    // 範囲なしだと、一覧のあとで FIFO に差し替えられたときに、書き手が現れるまで塞がる。
    const text = await $.fs.read(`${DETAIL_DIR}/${name}`, {
      offset: 0,
      length: DETAIL_MAX_BYTES,
    });
    detailCache.set(name, { size, mtimeMs, detail: parseDetail(text) ?? null });
    pendingReads.delete(name);
  } catch {
    // prune_dir() が一覧の直後に消したものなど。覚えずに次回また試す。
    detailCache.delete(name);
    pendingReads.delete(name);
  }
}

// 新しい方から count 本の詳細ログを古い順で返す。読めないものは飛ばす。
// notice は、置き場を信用できなくて全文を引かなかった理由。
async function readDetails(
  $: EngineInterface,
  count: number,
): Promise<{ details: TimedDetail[]; notice: string | null }> {
  let listed;
  try {
    listed = await $.fs.list(DETAIL_DIR);
  } catch {
    // 再起動で /tmp が空になった直後など。切れたコマンドのまま見せる。
    return { details: [], notice: null };
  }
  const trusted = await trustedNames($);
  if (!trusted.ok) {
    detailCache.clear();

    return { details: [], notice: trusted.notice };
  }
  // /tmp は誰でも書けるので、自分の所有でないもの・symlink・通常ファイルでないもの・
  // 巨大なファイルは読まない。
  const usable = new Map(
    listed
      .filter(
        (entry) =>
          entry.kind === "file" &&
          !entry.isLink &&
          entry.size <= DETAIL_MAX_BYTES &&
          DETAIL_NAME.test(entry.name) &&
          trusted.names.has(entry.name),
      )
      .map((entry) => [entry.name, entry] as const),
  );
  const recent = [...usable.keys()].sort(byStartTime).slice(-count);
  const kept = new Set(recent);
  for (const name of detailCache.keys()) {
    if (!kept.has(name)) detailCache.delete(name);
  }
  const unread = recent.filter((name) => {
    const entry = usable.get(name);
    const cached = detailCache.get(name);

    return (
      entry !== undefined &&
      !pendingReads.has(name) &&
      (cached === undefined ||
        cached.size !== entry.size ||
        cached.mtimeMs !== entry.mtimeMs)
    );
  });
  for (let i = 0; i < unread.length; i += DETAIL_BATCH) {
    await Promise.all(
      unread.slice(i, i + DETAIL_BATCH).map((name) => {
        const entry = usable.get(name);

        return entry === undefined
          ? undefined
          : readOne($, name, entry.size, entry.mtimeMs);
      }),
    );
  }

  return {
    details: recent.flatMap((name) => {
      const cached = detailCache.get(name);

      return cached?.detail
        ? [{ ...cached.detail, mtimeMs: cached.mtimeMs, name }]
        : [];
    }),
    notice: null,
  };
}

// ログを読み直して state に入れる。読み込み中に呼ばれたら、その結果を待つ。
async function load(
  $: EngineInterface,
): Promise<{ entries: BashReviewLogEntry[] } | { error: string }> {
  // 読み込みが終わらないと inFlight が塞がったまま、以後の更新がすべてそれを待つ。
  inFlight ??= within($, RELOAD_TIMEOUT_MS, reload($))
    .then(async (result) => {
      if (result.done) return result.value;
      const error = `bash-review: ログの読み込みが時間内に終わりませんでした (${LOG_SHOWN})`;
      await update($, errorAtom, () => error);

      return { error };
    })
    .finally(() => {
      inFlight = undefined;
    });

  return inFlight;
}

// reload の世代。load() は時間切れの読み込みを見限って次の読み込みを始めるが、見限った
// 読み込みは止められず、あとから終わる。そのとき、その間に済んだ新しい読み込みの結果
// (内容も、エラーも) を、古い内容で上書きしないよう、世代が最新のものだけが state に書く。
let reloadGeneration = 0;

// 読めなければ error に理由を残す。
async function reload(
  $: EngineInterface,
): Promise<{ entries: BashReviewLogEntry[] } | { error: string }> {
  reloadGeneration += 1;
  const generation = reloadGeneration;
  // 見限られたあとの読み込みの返り値は、誰も待っていない。
  const isStale = () => generation !== reloadGeneration;
  try {
    const path = await logPath($);
    const { mtimeMs } = await $.fs.stat(path);
    const parsed = parseLog(await $.fs.read(path));
    // 詳細ログが読めなくても (終わらない・信用できない)、サマリーだけで表示する。
    const read = await within(
      $,
      DETAIL_TIMEOUT_MS,
      readDetails($, parsed.length + DETAIL_SLACK),
    );
    const { details, notice } = read.done
      ? read.value
      : { details: [], notice: NOTICE_TIMEOUT };
    const entries = attachDetails(parsed, details);
    if (isStale()) return { entries };
    lastMtimeMs = mtimeMs;
    await update($, entriesAtom, () => entries);
    if (isStale()) return { entries };
    await update($, errorAtom, () => null);
    if (isStale()) return { entries };
    await update($, noticeAtom, () => notice);

    return { entries };
  } catch {
    // メッセージはモデルも読むので、ホームのユーザー名が入る絶対パスは出さない。
    const error = `bash-review: ログを読めませんでした (${LOG_SHOWN})`;
    if (!isStale()) await update($, errorAtom, () => error);

    return { error };
  }
}

function stopPolling(): void {
  poll?.cancel();
  poll = undefined;
}

// ペインを開いている間だけログの更新を拾う。開く・開き直すたびに作り直し、重ねない。
function startPolling($: EngineInterface): void {
  stopPolling();
  poll = $.clock.every(POLL_MS, () => void refreshIfChanged($));
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
    // リロード (session.start が再発火) 時に、ペインが開いたままなら更新を拾い直す。
    if (isOpen) startPolling($);

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
    isOpen = opened.isPlaced;
    if (opened.isPlaced) {
      startPolling($);

      return { text: heading };
    }

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
    stopPolling();

    return next(e);
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e);
    const entries = await read($, entriesAtom);
    const view = await read($, viewAtom);
    const error = await read($, errorAtom);
    const notice = await read($, noticeAtom);
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
            onPress={() => {
              // 自分が閉じるときは ui.close のフックを通らないことがあるので、ここでも止める。
              isOpen = false;
              stopPolling();
              void $.ui.close({ id: PANE });
            }}
          />
        </Box>
        <Text dimColor>
          {describeView(view)} — 該当 {matched.length} 件中 {shown.length}{" "}
          件を表示
        </Text>
        {error !== null && <Text color="red">{error}</Text>}
        {notice !== null && <Text dimColor>{notice}</Text>}
        {error === null && shown.length === 0 && (
          <Text dimColor>該当する判定はありません</Text>
        )}
        {shown.map((entry, index) => (
          <Box
            key={`entry-${index}-${entry.at}`}
            flexDirection="column"
            marginTop={1}
          >
            <Text wrap="truncate-end">
              <Text dimColor>{entry.at.slice(5)} </Text>
              <Text bold color={DECISION_COLOR.get(entry.decision) ?? "white"}>
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
