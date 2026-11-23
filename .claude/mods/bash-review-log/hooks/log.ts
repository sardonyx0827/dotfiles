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

// サマリーとの照合に使う、詳細ログのファイルの更新時刻 (ms) とファイル名つき。
export type TimedDetail = Detail & { mtimeMs: number; name?: string };

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
// decision / stage は空白詰めの固定幅。command は 80 文字で切られる。command と
// reason の改行 (CR / LF) は NEWLINE_MARK の 1 文字に置き換えて書かれるので、1 判定は
// 1 物理行に収まる。reason の " | " も書き手が置き換えるので、エントリ末尾の最後の
// " | " が command と reason の境目になる。
// 改行をそのまま書いていた頃のログは、コマンドが次の行以降へ続く (継続行)。
// 手元に残っている分を読めるよう、ヘッダの形をしていない行は直前のエントリへ繋ぐ。
// s フラグ: コマンドに U+2028 / U+2029 / \r が混じっても . が止まらないように。
const HEADER =
  /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (\S+)\s*\| (\S+)\s*\| (.*)$/s;

// log_summary() が改行の代わりに書く 1 文字 (_bash_review_common.py: _LOG_NEWLINE)。
// 詳細ログ側のコマンドは実改行のままなので、照合キーを作るときに同じ置換を掛ける。
// 1 文字を 1 文字に置き換えるので、「先頭 80 字」の数え方は変わらない。
export const NEWLINE_MARK = "⏎";

function markNewlines(text: string): string {
  return text.replace(/[\r\n]/g, NEWLINE_MARK);
}

// ui.render の Text は 10000 字まで、制御文字はタブと改行だけ。どちらかを破ると
// ペイン全体が描けなくなる (エンジンが自前の表示に差し替える) ので、ログ由来の
// 文字列は displayText() を通してから描く。bidi 制御と行区切りは表示の偽装に
// 使えるので一緒に置き換える。書式文字 (Cf: ゼロ幅、BOM、ソフトハイフン、タグ文字)
// と既定で無視される文字も置き換える: ペインの無い /bash-review-log ではこの文字列が
// モデルの読む本文になり、不可視の文で指示を埋め込めるため (doc-browser の
// catalog.ts の UNSAFE_CHARS と、タブと改行を残す点だけが違う文字クラス。bidi 制御は
// Cf に含まれる)。u フラグでタグ文字などの astral 文字も 1 字として置き換える。
const UNSAFE =
  /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u2028\u2029\p{Cf}\p{Default_Ignorable_Code_Point}]/gu;
export const COMPACT_MAX = 500;
export const FULL_MAX = 9000;
const LABEL_MAX = 200;

const SEPARATOR = " | ";

// log_summary() の command[:80] + "..."。Python の添字はコードポイント単位。
const TRUNCATE_AT = 80;
const ELLIPSIS = "...";

const REVIEWER_ERROR = /\b(?:gemini|codex)=ERROR\b/;

// フックが詳細ログを書けた実行で reason の末尾に添える、その詳細ログのファイル名
// (bash-review.py の detail_ref)。末尾に固定する: 理由文の途中にはコマンド由来の語が
// 入りうるので、そこにある detail= は名前に数えない。
const DETAIL_REF = / detail=(bash_cmd_\d+_\d+\.log)$/;

const TOOL_INPUT = "Tool Input: ";

export function parseLog(text: string): BashReviewLogEntry[] {
  const entries: BashReviewLogEntry[] = [];
  let head: { at: string; decision: string; stage: string } | undefined;
  let body = "";

  const flush = () => {
    if (head === undefined) return;
    const cut = body.lastIndexOf(SEPARATOR);
    const reason = cut < 0 ? "" : body.slice(cut + SEPARATOR.length);
    const ref = DETAIL_REF.exec(reason);
    entries.push({
      ...head,
      command: cut < 0 ? body : body.slice(0, cut),
      reason: ref === null ? reason : reason.slice(0, ref.index),
      ...(ref?.[1] === undefined ? {} : { detailLog: ref[1] }),
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
//
// doc-browser・containers の catalog.ts にも displayText があるが、あちらは制御文字を
// 区別せず U+FFFD にし (タブと改行も置き換える)、切ったら … だけを付ける別の仕様。
// 共有できないので、意図して別々に持っている: CLI は mod の外への import を拒み
// (`cannot import "..": it is outside the plugin's folder`)、`claude plugin validate` も
// 通らない。UNSAFE の文字クラスを変えるときは、あちらの UNSAFE_CHARS も揃えること。
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
// キーと混ざらないよう、二つの名前空間に分ける。改行はどちらの側も NEWLINE_MARK に
// 揃える: サマリーは書き手が置き換え済み (以前の形式は継続行から読んだ実改行)、
// 詳細ログは実改行のまま。
function entryKey(entry: BashReviewLogEntry): string {
  const command = markNewlines(entry.command);

  return isTruncated(entry)
    ? `cut:${Array.from(command).slice(0, TRUNCATE_AT).join("")}`
    : `all:${command}`;
}

function detailKey(detail: Detail): string {
  const chars = Array.from(markNewlines(detail.command));

  return chars.length > TRUNCATE_AT
    ? `cut:${chars.slice(0, TRUNCATE_AT).join("")}`
    : `all:${chars.join("")}`;
}

// サマリーの at (time.strftime のローカル時刻、秒の切り捨て) を epoch ms にする。
// 読めなければ NaN。
function atMs(at: string): number {
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(at);
  if (match === null) return Number.NaN;
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number);

  return new Date(
    year ?? 0,
    (month ?? 1) - 1,
    day ?? 1,
    hour,
    minute,
    second,
  ).getTime();
}

// 詳細ログの mtime が、サマリーの at から見てこの範囲にあれば同じ判定のもの。
// フックは write_detail_log の直後に log_summary を呼ぶので、mtime は書いた時刻 S の
// 数 ms 前で、at は S の秒を切り捨てたもの。つまり mtime は at の少し前から at + 1 秒の
// 手前までに入る。前側は、書き込みの競合待ちやファイルシステムの時刻の粗さの分だけ余らせる。
const MATCH_BEFORE_MS = 2000;
const MATCH_AFTER_MS = 1000;

// 全文は切られたエントリにだけ、description は対応がついたすべてに付ける。
function withDetail(
  entry: BashReviewLogEntry,
  detail: TimedDetail,
): BashReviewLogEntry {
  return {
    ...entry,
    ...(isTruncated(entry) ? { fullCommand: detail.command } : {}),
    ...(detail.description === undefined
      ? {}
      : { description: detail.description }),
  };
}

// 詳細ログ (details) をサマリーのエントリに対応づける。
//
// reason の末尾に detail=<ファイル名> があるエントリ (detailLog) は、その名前の詳細ログ
// だけに対応づける。コマンドの照合キーが食い違えば付けない。その詳細ログが読めなければ
// 付けず、時刻での推定にも回さない。名前で引かれる詳細ログは、下の推定の候補からも外す。
//
// 名前の無い旧い行は、時刻とコマンドで推定する: コマンドの照合キーが同じで、詳細ログの
// mtime がサマリーの時刻と合うもの。順序だけで対応づけると、片方のログに欠けがあるとき
// (秘密検出で伏せた分、書きかけのまま読めなかった分、止まっていた間の分) に 1 つずつ
// ずれて、別のコマンドの全文が付く。全文を取り違えて見せるのは、見せないよりも悪いので、
// 決め手が無いときは付けない:
// - 候補の全文・description が食い違うとき (並列に走った同じ先頭の別コマンド)
// - 1 本の詳細ログを取り合うエントリが、候補の本数より多いとき (どれかの詳細ログが欠けている)
export function attachDetails(
  entries: readonly BashReviewLogEntry[],
  details: readonly TimedDetail[],
): BashReviewLogEntry[] {
  const byName = new Map<string, TimedDetail>();
  for (const detail of details) {
    if (detail.name !== undefined) byName.set(detail.name, detail);
  }
  const claimed = new Set(
    entries.flatMap((entry) =>
      entry.detailLog === undefined ? [] : [entry.detailLog],
    ),
  );
  const byKey = new Map<string, TimedDetail[]>();
  for (const detail of details) {
    if (detail.name !== undefined && claimed.has(detail.name)) continue;
    const key = detailKey(detail);
    byKey.set(key, [...(byKey.get(key) ?? []), detail]);
  }

  const candidates = entries.map((entry) => {
    if (entry.detailLog !== undefined) return [];
    const at = atMs(entry.at);

    return (byKey.get(entryKey(entry)) ?? []).filter((detail) => {
      const diff = detail.mtimeMs - at;

      return diff >= -MATCH_BEFORE_MS && diff <= MATCH_AFTER_MS;
    });
  });
  const rivals = new Map<TimedDetail, number>();
  for (const list of candidates) {
    for (const detail of list)
      rivals.set(detail, (rivals.get(detail) ?? 0) + 1);
  }

  return entries.map((entry, index) => {
    if (entry.detailLog !== undefined) {
      const detail = byName.get(entry.detailLog);

      return detail !== undefined && detailKey(detail) === entryKey(entry)
        ? withDetail(entry, detail)
        : entry;
    }
    const list = candidates[index] ?? [];
    const [first] = list;
    if (first === undefined) return entry;
    const isSame = list.every(
      (detail) =>
        detail.command === first.command &&
        detail.description === first.description,
    );
    const contenders = Math.max(
      ...list.map((detail) => rivals.get(detail) ?? 1),
    );
    if (!isSame || list.length < contenders) return entry;

    return withDetail(entry, first);
  });
}
