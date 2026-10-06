import type {
  BashReviewLogEntry,
  BashReviewLogType,
  BashReviewLogView,
} from "../types";

export type DecisionCounts = {
  allow: number;
  ask: number;
  deny: number;
  // レビュアー (Gemini / Codex) のエラー。判定の数え方とは重なる
  error: number;
  other: number;
};

// 詳細ログ (コマンドごと 1 ファイル) の Tool Input から引いたもの。
export type Detail = { command: string; description?: string };

export const TYPES: readonly BashReviewLogType[] = [
  "ALLOW",
  "ASK",
  "DENY",
  "ERROR",
];
// 要確認 (人の目が要る) もの: flagged が指す
const FLAGGED: readonly BashReviewLogType[] = ["ASK", "DENY", "ERROR"];
export const DEFAULT_LIMIT = 50;
export const MAX_LIMIT = 500;
export const DEFAULT_VIEW: BashReviewLogView = {
  types: [],
  limit: DEFAULT_LIMIT,
  isFull: false,
};

// _bash_review_common.py の log_summary() が書く 1 行:
//   [YYYY-MM-DD HH:MM:SS] DECISION | stage    | command | reason
// decision / stage は空白詰めの固定幅。command は 80 文字で切られ、改行を含めば
// 次の行以降へ続く (継続行)。reason に " | " は現れないので、エントリ末尾の
// 最後の " | " が command と reason の境目になる。
// s フラグ: コマンドに U+2028 / U+2029 / \r が混じっても . が止まらないように。
const HEADER =
  /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (\S+)\s*\| (\S+)\s*\| (.*)$/s;

// ui.render の Text は 10000 字まで、制御文字はタブと改行だけ。どちらかを破ると
// ペイン全体が描けなくなる (エンジンが自前の表示に差し替える) ので、ログ由来の
// 文字列は displayText() を通してから描く。bidi 制御と行区切りは表示の偽装に
// 使えるので一緒に置き換える。
const UNSAFE =
  /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069\u2028\u2029]/g;
export const COMPACT_MAX = 500;
export const FULL_MAX = 9000;
const LABEL_MAX = 200;

const SEPARATOR = " | ";

// log_summary() の command[:80] + "..."。Python の添字はコードポイント単位。
const TRUNCATE_AT = 80;
const ELLIPSIS = "...";

const REVIEWER_ERROR = /\b(?:gemini|codex)=ERROR\b/;

const TOOL_INPUT = "Tool Input: ";

export function parseLog(text: string): BashReviewLogEntry[] {
  const entries: BashReviewLogEntry[] = [];
  let head: { at: string; decision: string; stage: string } | undefined;
  let body = "";

  const flush = () => {
    if (head === undefined) return;
    const cut = body.lastIndexOf(SEPARATOR);
    entries.push({
      ...head,
      command: cut < 0 ? body : body.slice(0, cut),
      reason: cut < 0 ? "" : body.slice(cut + SEPARATOR.length),
    });
  };

  for (const line of text.replace(/\n+$/, "").split("\n")) {
    const match = HEADER.exec(line);
    if (match) {
      flush();
      const [, at = "", decision = "", stage = "", rest = ""] = match;
      head = { at, decision, stage };
      body = rest;
    } else if (head !== undefined) {
      body += `\n${line}`;
    }
    // 最初のヘッダより前の行は、ローテーション (末尾 500 行の保持) で頭を
    // 失ったエントリの残りなので捨てる。
  }
  flush();

  return entries;
}

export function isReviewerError(entry: BashReviewLogEntry): boolean {
  return REVIEWER_ERROR.test(entry.reason);
}

export function countDecisions(
  entries: readonly BashReviewLogEntry[],
): DecisionCounts {
  const counts: DecisionCounts = {
    allow: 0,
    ask: 0,
    deny: 0,
    error: 0,
    other: 0,
  };
  for (const entry of entries) {
    if (entry.decision === "ALLOW") counts.allow += 1;
    else if (entry.decision === "ASK") counts.ask += 1;
    else if (entry.decision === "DENY") counts.deny += 1;
    else counts.other += 1;
    if (isReviewerError(entry)) counts.error += 1;
  }

  return counts;
}

function isType(value: string): value is BashReviewLogType {
  return (TYPES as readonly string[]).includes(value);
}

// 引数: 種別 (allow / ask / deny / error / flagged / all)、件数、full / short を
// 空白区切りで順不同に。知らない語があれば undefined。
export function parseArgs(args: string): BashReviewLogView | undefined {
  const types = new Set<BashReviewLogType>();
  let isAll = false;
  let limit = DEFAULT_LIMIT;
  let isFull = false;

  for (const token of args.trim().split(/\s+/).filter(Boolean)) {
    const word = token.toUpperCase();
    if (isType(word)) types.add(word);
    else if (word === "FLAGGED") FLAGGED.forEach((type) => types.add(type));
    else if (word === "ALL") isAll = true;
    else if (word === "FULL") isFull = true;
    else if (word === "SHORT") isFull = false;
    else if (/^\d+$/.test(word) && Number(word) > 0)
      limit = Math.min(Number(word), MAX_LIMIT);
    else return undefined;
  }

  return {
    types: isAll ? [] : TYPES.filter((type) => types.has(type)),
    limit,
    isFull,
  };
}

export function toggleType(
  view: BashReviewLogView,
  type: BashReviewLogType,
): BashReviewLogView {
  const types = view.types.includes(type)
    ? view.types.filter((t) => t !== type)
    : TYPES.filter((t) => t === type || view.types.includes(t));

  return { ...view, types };
}

export function describeView(view: BashReviewLogView): string {
  const types = view.types.length === 0 ? "全種別" : view.types.join("・");

  return `${types} / 最大 ${view.limit} 件 / ${view.isFull ? "全文" : "1 行"}`;
}

function matchesTypes(
  entry: BashReviewLogEntry,
  types: readonly BashReviewLogType[],
): boolean {
  return (
    types.length === 0 ||
    types.some((type) =>
      type === "ERROR" ? isReviewerError(entry) : entry.decision === type,
    )
  );
}

// ログは古い順に並ぶので、新しい順に反転してから絞り込む。
export function selectEntries(
  entries: readonly BashReviewLogEntry[],
  types: readonly BashReviewLogType[],
  limit: number,
): BashReviewLogEntry[] {
  return [...entries]
    .reverse()
    .filter((entry) => matchesTypes(entry, types))
    .slice(0, limit);
}

export function toOneLine(command: string): string {
  return command.replaceAll("\n", " ⏎ ");
}

// C0 は制御記号 (U+2400〜, ESC なら ␛)、DEL は ␡、それ以外は U+FFFD にし、
// max 字 (コードポイント) を超えたら切って残りの字数を添える。
export function displayText(text: string, max: number): string {
  const safe = text.replace(UNSAFE, (char) => {
    const code = char.charCodeAt(0);
    if (code < 0x20) return String.fromCharCode(0x2400 + code);

    return code === 0x7f ? "␡" : "�";
  });
  const chars = Array.from(safe);

  return chars.length <= max
    ? safe
    : `${chars.slice(0, max).join("")}…（残り ${chars.length - max} 字）`;
}

// 1 行表示用: 改行を ⏎ にしてから無害化する。
export function oneLineText(text: string): string {
  return displayText(toOneLine(text), COMPACT_MAX);
}

export function labelText(text: string): string {
  return displayText(text, LABEL_MAX);
}

export function commandOf(entry: BashReviewLogEntry): string {
  return entry.fullCommand ?? entry.command;
}

export function summarize(entries: readonly BashReviewLogEntry[]): string {
  const { allow, ask, deny, error, other } = countDecisions(entries);
  const tail = other > 0 ? ` · その他 ${other}` : "";

  return `bash-review: 直近 ${entries.length} 件 — ALLOW ${allow} · ASK ${ask} · DENY ${deny} · ERROR ${error}${tail}`;
}

// 本文 (コマンドの text。モデルも読み、トランスクリプトに残る) 用の 1 行。年を
// 落とした日時と固定幅の decision / stage を並べる。他プロジェクトのコマンドも
// 混じるログなので、詳細ログの全文は載せずサマリーの 80 字までにとどめる。
export function formatEntry(entry: BashReviewLogEntry): string {
  const decision = labelText(entry.decision).padEnd(5);
  const stage = labelText(entry.stage).padEnd(8);

  return `${entry.at.slice(5)} ${decision} ${stage} ${oneLineText(entry.command)} — ${oneLineText(entry.reason)}`;
}

// write_detail_log() が書く詳細ログから Tool Input を取り出す。秘密検出で
// "[REDACTED - credential detected]" に伏せられたものは JSON ではないので捨てる。
export function parseDetail(text: string): Detail | undefined {
  const line = text.split("\n").find((l) => l.startsWith(TOOL_INPUT));
  if (line === undefined) return undefined;
  try {
    const input: unknown = JSON.parse(line.slice(TOOL_INPUT.length));
    if (typeof input !== "object" || input === null) return undefined;
    const { command, description } = input as Record<string, unknown>;
    if (typeof command !== "string") return undefined;

    return typeof description === "string" && description !== ""
      ? { command, description }
      : { command };
  } catch {
    return undefined;
  }
}

// サマリーで切られたコマンドか (先頭 80 字 + "..." の形)。
function isTruncated(entry: BashReviewLogEntry): boolean {
  return (
    entry.command.endsWith(ELLIPSIS) &&
    Array.from(entry.command).length === TRUNCATE_AT + ELLIPSIS.length
  );
}

// 詳細ログとの照合キー。切られたものは「先頭 80 字」、切られていないものは
// 「全文」で引く。ちょうど 80 字のコマンドが、それで始まる長いコマンドの
// キーと混ざらないよう、二つの名前空間に分ける。
function entryKey(entry: BashReviewLogEntry): string {
  return isTruncated(entry)
    ? `cut:${Array.from(entry.command).slice(0, TRUNCATE_AT).join("")}`
    : `all:${entry.command}`;
}

function detailKey(detail: Detail): string {
  const chars = Array.from(detail.command);

  return chars.length > TRUNCATE_AT
    ? `cut:${chars.slice(0, TRUNCATE_AT).join("")}`
    : `all:${detail.command}`;
}

// details は古い順。サマリー (末尾 500 行) と詳細ログ (末尾 1000 件、/tmp なので
// 再起動で消える) は保持範囲が違っても、どちらも「今」で終わる。そこで同じキーの
// 中で新しい方から 1 対 1 に対応づけ、詳細ログが足りない古いエントリはそのままにする。
// 全文は切られたエントリにだけ、description は対応がついたすべてに付ける。
export function attachDetails(
  entries: readonly BashReviewLogEntry[],
  details: readonly Detail[],
): BashReviewLogEntry[] {
  const byKey = new Map<string, Detail[]>();
  for (const detail of details) {
    const key = detailKey(detail);
    byKey.set(key, [...(byKey.get(key) ?? []), detail]);
  }

  const attached = [...entries];
  for (let i = attached.length - 1; i >= 0; i -= 1) {
    const entry = attached[i];
    const detail =
      entry === undefined ? undefined : byKey.get(entryKey(entry))?.pop();
    if (entry === undefined || detail === undefined) continue;
    attached[i] = {
      ...entry,
      ...(isTruncated(entry) ? { fullCommand: detail.command } : {}),
      ...(detail.description === undefined
        ? {}
        : { description: detail.description }),
    };
  }

  return attached;
}
