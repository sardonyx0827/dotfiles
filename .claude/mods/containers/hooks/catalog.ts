// 表示用の行の組み立て (純粋): csb と docker の出力を、無害化・~ 相対化した行にする。
// I/O (プロセスの起動・ペイン) は register.tsx が持つ。

import type {
  ComposeRow,
  ContainerRow,
  NetState,
  PatchRow,
  SandboxRow,
  Section,
  Snapshot,
} from "../types";

// 区分ごとに描く行の上限。超えた分は件数だけ hidden に残す。
export const MAX_ROWS = 40;
// ペインを置けない場所で本文 (モデルも読む) に載せる行数の上限 (区分ごと)。
export const MAX_TEXT_ROWS = 20;
export const NAME_MAX = 80;
export const TEXT_MAX = 200;
// register.tsx が各プロセスに与える timeout (秒)。知らせの文言にも使う。
export const PROCESS_TIMEOUT_S = 5;

export const TITLE_SANDBOXES = "サンドボックス";
export const TITLE_CONTAINERS = "その他のコンテナ";
export const TITLE_COMPOSE = "Compose プロジェクト";

const SANDBOX_ID = /^[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const ROLE = /^[a-z][a-z0-9-]{0,15}$/;
const KEY_SANDBOX = "dev.claude.sandbox";
const KEY_SANDBOX_ID = `${KEY_SANDBOX}.id`;
const KEY_SANDBOX_ROLE = `${KEY_SANDBOX}.role`;
// csb が付けるコンテナ名 (_sandbox_gate.container_name): csb-<ID> (work)・csb-<ID>-setup・
// csb-<ID>-verify。ラベルは誰でも付けられる (compose の別のラベルの値にカンマで埋め込む
// こともできる) ので、サンドボックスのコンテナと認めるのは、この名前の ID がラベルの ID と
// 合うときだけにする。
const SANDBOX_CONTAINER_NAME = /^csb-([0-9a-f]{12})(?:-(?:setup|verify))?$/;

// 制御文字 (Cc)・書式文字 (Cf: ゼロ幅、BOM、bidi の書き換え、タグ文字)・既定で無視される
// 文字と行区切り。docker のコンテナ名・状態や csb の理由に混ぜると、表示を偽ったり、
// モデルが読む本文に不可視の文を埋めたりできる。
const UNSAFE_CHARS =
  /[\p{Cc}\p{Cf}\p{Default_Ignorable_Code_Point}\u2028\u2029]/gu;

// doc-browser の catalog.ts の displayText と UNSAFE_CHARS は、これと同じものの写し
// (bash-review-log の log.ts のものは、制御文字を記号にして残り字数を出す別の仕様)。
// 共有できないので、意図して写している: CLI は mod の外への import を拒み
// (`cannot import "..": it is outside the plugin's folder`)、`claude plugin validate` も
// 通らない。文字クラスを変えるときは、doc-browser のものと log.ts の UNSAFE も揃えること。
export function displayText(text: string, max: number): string {
  const clean = text.replace(UNSAFE_CHARS, "\uFFFD");
  const chars = Array.from(clean);

  return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : clean;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ホームのパスを ~ に置き換える。前後のパス要素の境界を見て、/home/tester2 や
// /other/home/tester は触らない。パス 1 本でも、パスを含む文でも使える。
export function toTilde(path: string, home: string): string {
  const base = home.replace(/\/+$/, "");
  if (base === "") return path;

  return path.replace(
    new RegExp(`(^|[^\\w./-])${escapeRegExp(base)}(?=/|$|[^\\w.-])`, "g"),
    "$1~",
  );
}

// docker ps の Mounts は、ボリュームなら名前、bind mount ならホストのパスを並べる。
// ホストのパス (絶対パス) がひとつでもあれば bind mount がある。
export function hasBindMount(mounts: string): boolean {
  return mounts.split(",").some((part) => {
    const text = part.trim();

    return text.startsWith("/") || /^[A-Za-z]:[\\/]/.test(text);
  });
}

// docker ps の Labels は "k=v,k=v" を 1 本につないだ文字列で、値自体にカンマを含む
// もの (compose の config_files) がある。= の無い断片は読み飛ばし、必要な 3 つの
// キーだけを完全一致で引く。ラベルは誰でも付けられるので、表示以外には使わない。
export function sandboxLabels(labels: string): {
  isSandbox: boolean;
  id: string | null;
  role: string | null;
} {
  let isSandbox = false;
  let id: string | null = null;
  let role: string | null = null;
  for (const part of labels.split(",")) {
    const at = part.indexOf("=");
    if (at <= 0) continue;
    const key = part.slice(0, at);
    const value = part.slice(at + 1);
    if (key === KEY_SANDBOX) {
      if (value === "1") isSandbox = true;
    } else if (key === KEY_SANDBOX_ID) {
      if (SANDBOX_ID.test(value)) id = value;
    } else if (key === KEY_SANDBOX_ROLE) {
      if (ROLE.test(value)) role = value;
    }
  }

  return { isSandbox, id, role };
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : 0;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export type DockerContainer = {
  id: string;
  name: string;
  image: string;
  state: string;
  status: string;
  hasBind: boolean;
  // csb のラベルを持つ (名前は問わない。「その他」に [csb ラベル] を付ける印)
  isSandboxLabeled: boolean;
  // サンドボックスのコンテナと認めたときだけ: ラベルの ID と csb の命名の ID が合う。
  // サンドボックスの行に畳む・役割と bind mount を足す、のはこれがあるものだけ
  sandboxId: string | null;
  sandboxRole: string | null;
};

// docker ps -a --no-trunc --format '{{json .}}' (1 行 1 コンテナの JSON)。
// 見せる項目だけを取り、Command・Labels の中身・Env などは捨てる。
// 1 行も JSON のオブジェクトとして読めなければ null。
export function parseDockerPs(
  stdout: string,
  isTruncated: boolean,
): DockerContainer[] | null {
  // 上限で切れた出力の、最後の改行より後ろは途中で切れた行なので捨てる。
  const body = isTruncated
    ? stdout.slice(0, stdout.lastIndexOf("\n") + 1)
    : stdout;
  const lines = body.split("\n").filter((line) => line.trim() !== "");
  const containers: DockerContainer[] = [];
  let objects = 0;
  for (const line of lines) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      continue;
    }
    const row = record(parsed);
    if (row === undefined) continue;
    objects += 1;
    const name = text(row.Names);
    if (name === "") continue;
    const labels = sandboxLabels(text(row.Labels));
    const isSandbox =
      labels.isSandbox &&
      labels.id !== null &&
      SANDBOX_CONTAINER_NAME.exec(name)?.[1] === labels.id;
    containers.push({
      id: displayText(text(row.ID) || name, NAME_MAX),
      name: displayText(name, NAME_MAX),
      image: displayText(text(row.Image), TEXT_MAX),
      state: displayText(text(row.State), 20),
      status: displayText(text(row.Status), NAME_MAX),
      hasBind: hasBindMount(text(row.Mounts)),
      isSandboxLabeled: labels.isSandbox,
      sandboxId: isSandbox ? labels.id : null,
      sandboxRole: isSandbox ? labels.role : null,
    });
  }

  return lines.length > 0 && objects === 0 ? null : containers;
}

function patchRow(value: unknown, home: string): PatchRow | undefined {
  const row = record(value);
  if (row === undefined) return undefined;
  const sha = SHA256.test(text(row.sha256))
    ? text(row.sha256).slice(0, 7)
    : "?";
  const error = text(row.error);
  if (error !== "") {
    return {
      sha,
      error: displayText(toTilde(error, home), TEXT_MAX),
      files: 0,
      added: 0,
      removed: 0,
      verify: "?",
      reviews: [],
    };
  }
  const reviews = Object.entries(record(row.reviews) ?? {})
    .slice(0, 8)
    .map(
      ([name, status]) =>
        `${displayText(name, 24)}=${displayText(text(status) || "?", 24)}`,
    );

  return {
    sha,
    error: null,
    files: count(row.files),
    added: count(row.added),
    removed: count(row.removed),
    verify: displayText(text(row.verify) || "?", 20),
    reviews,
  };
}

// csb status --json (ID なし): サンドボックスごとの配列。役割と bind mount の有無は
// docker 側の情報なので、ここでは不明 (null) にしておく (withDockerFacts)。
export function parseCsbStatus(
  stdout: string,
  home: string,
): SandboxRow[] | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed)) return null;
  const rows: SandboxRow[] = [];
  for (const item of parsed as unknown[]) {
    const row = record(item);
    const id = row === undefined ? "" : text(row.id);
    if (row === undefined || !SANDBOX_ID.test(id)) continue;
    const base = {
      id,
      roles: null,
      hasBind: null,
    };
    const error = text(row.error);
    if (error !== "") {
      rows.push({
        ...base,
        broken: displayText(toTilde(error, home), TEXT_MAX),
        state: "?",
        isRunning: false,
        netUntilMs: null,
        net: "unknown",
        repo: "",
        check: null,
        patches: [],
      });
      continue;
    }
    const state = displayText(text(row.container) || "?", 20);
    const until = row.net_on_until;
    const check = text(row.check);
    const netUntilMs =
      typeof until === "number" && Number.isFinite(until) && until >= 0
        ? until * 1000
        : null;
    rows.push({
      ...base,
      broken: null,
      state,
      isRunning: state === "running",
      netUntilMs,
      net: netState(row, netUntilMs),
      repo: displayText(toTilde(text(row.repo_root), home), TEXT_MAX),
      check:
        check === "ok" || check === ""
          ? null
          : displayText(toTilde(check, home), 120),
      patches: (Array.isArray(row.patches) ? (row.patches as unknown[]) : [])
        .map((patch) => patchRow(patch, home))
        .filter((patch): patch is PatchRow => patch !== undefined),
    });
  }

  return rows;
}

// docker compose ls --format json: プロジェクトごとの配列。
export function parseComposeLs(
  stdout: string,
  home: string,
): ComposeRow[] | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed)) return null;
  const rows: ComposeRow[] = [];
  for (const item of parsed as unknown[]) {
    const row = record(item);
    const name = row === undefined ? "" : text(row.Name);
    if (row === undefined || name === "") continue;
    const files = text(row.ConfigFiles)
      .split(",")
      .filter((file) => file !== "");
    rows.push({
      name: displayText(name, NAME_MAX),
      status: displayText(text(row.Status), 60),
      file:
        files[0] === undefined
          ? ""
          : displayText(toTilde(files[0], home), TEXT_MAX),
      moreFiles: Math.max(0, files.length - 1),
    });
  }

  return rows;
}

// docker のラベルから、サンドボックスの役割 (work / setup / verify) と、bind mount を
// 持つコンテナがあるか (サンドボックスにあってはならないもの) を足す。
//
// 「無い」と言えるのは、docker ps を最後まで読めたときだけ (containers が配列で、
// isComplete)。読めていなければ (containers が null)、役割も bind mount も不明 (null) の
// まま。出力が途中で切れていれば、見えなかった分に有るかもしれないので、役割は不明、
// bind mount は「見えたもの」だけ true にして、見えなかったものは不明にする。
export function withDockerFacts(
  rows: SandboxRow[],
  containers: DockerContainer[] | null,
  isComplete: boolean,
): SandboxRow[] {
  if (containers === null) return rows;
  const facts = new Map<string, { roles: Set<string>; hasBind: boolean }>();
  for (const one of containers) {
    if (!one.isSandboxLabeled || one.sandboxId === null) continue;
    const entry = facts.get(one.sandboxId) ?? {
      roles: new Set<string>(),
      hasBind: false,
    };
    if (one.sandboxRole !== null) entry.roles.add(one.sandboxRole);
    if (one.hasBind) entry.hasBind = true;
    facts.set(one.sandboxId, entry);
  }

  return rows.map((row) => {
    const entry = facts.get(row.id);
    if (!isComplete) {
      return {
        ...row,
        roles: null,
        hasBind: entry?.hasBind === true ? true : null,
      };
    }

    return {
      ...row,
      roles: entry === undefined ? [] : [...entry.roles].sort(),
      hasBind: entry?.hasBind ?? false,
    };
  });
}

// サンドボックスの行に畳んだコンテナは除く。csb の状態と結びつかないラベル付きの
// コンテナ (csb が無い・状態を消した後の残り) は、ラベルの印を付けて残す。
export function otherContainers(
  containers: DockerContainer[],
  sandboxIds: ReadonlySet<string>,
): Section<ContainerRow> {
  const rows = containers
    .filter(
      (one) =>
        !(
          one.isSandboxLabeled &&
          one.sandboxId !== null &&
          sandboxIds.has(one.sandboxId)
        ),
    )
    .map(
      (one): ContainerRow => ({
        id: one.id,
        name: one.name,
        image: one.image,
        status: one.status,
        isRunning: one.state === "running",
        hasBind: one.hasBind,
        isSandboxLabeled: one.isSandboxLabeled,
      }),
    );

  return {
    rows: rows.slice(0, MAX_ROWS),
    notice: null,
    hidden: Math.max(0, rows.length - MAX_ROWS),
  };
}

export type ProcessOutcome = PromiseSettledResult<{
  exitCode: number;
  stdout: string;
  stderr: string;
  isStdoutTruncated: boolean;
}>;

const DAEMON_DOWN =
  /cannot connect to the docker daemon|is the docker daemon running|error during connect|docker\.sock|connection refused/i;
// ソケットはあるが、権限が無い (実行ユーザーが docker を使えない)。DAEMON_DOWN の
// docker\.sock にも当たるので、先に調べる。processNotice は csb にも使うので、docker の
// 文脈 (ソケットへの接続の失敗) に限り、ファイルの権限エラー一般は拾わない。
const SOCKET_DENIED =
  /permission denied while trying to connect to the docker|docker\.sock[^\n]*connect: permission denied/i;
const COMPOSE_MISSING =
  /is not a docker command|unknown docker command|unknown shorthand flag/i;

// プロセスが成功していれば null、そうでなければ 1 行の知らせ。stderr の中身は
// (ホームのパスなどを含みうるので) 出さず、失敗の種類だけを固定の文にする。
export function processNotice(
  label: string,
  outcome: ProcessOutcome,
): string | null {
  if (outcome.status === "rejected") {
    return `${label} を実行できませんでした (未導入か、${PROCESS_TIMEOUT_S} 秒以内に終わりませんでした)`;
  }
  const { exitCode, stderr } = outcome.value;
  if (exitCode === 0) return null;
  if (SOCKET_DENIED.test(stderr)) {
    return "Docker のソケットを使う権限がありません (実行ユーザーの権限を確かめてください)";
  }
  if (DAEMON_DOWN.test(stderr)) {
    return "Docker デーモンに接続できません (Docker が起動していますか)";
  }
  if (COMPOSE_MISSING.test(stderr)) {
    return "docker compose が使えません (プラグインが入っていません)";
  }

  return `${label} が失敗しました (exit ${exitCode})`;
}

// ~/.local/bin/csb は install.sh が書くラッパー (通常のファイル)。走らせなかった理由は 2 つ:
// - ラッパーが無い・symlink やディレクトリで通常のファイルでない (install.sh で直る)
// - HOME が取れない・絶対パスのディレクトリでない (ラッパーの場所を決められない。
//   install.sh では直らないので、ラッパーのせいにしない)
// どちらも HOME の値は載せない (この文はモデルも読む)。
const NOTICE_CSB_MISSING =
  "csb が使えません (~/.local/bin/csb が無いか、通常のファイルでない。install.sh を実行)";
const NOTICE_CSB_NO_HOME =
  "csb が使えません (HOME が絶対パスのディレクトリでない)";
const NOTICE_TRUNCATED = "一覧が長すぎるため途中までです";

function failed<T>(notice: string): Section<T> {
  return { rows: [], notice, hidden: 0 };
}

function capped<T>(rows: T[]): Section<T> {
  return {
    rows: rows.slice(0, MAX_ROWS),
    notice: null,
    hidden: Math.max(0, rows.length - MAX_ROWS),
  };
}

export type Outcomes = {
  // "missing": csb のラッパー (~/.local/bin/csb) が無い・通常のファイルでなく、走らせなかった。
  // "no-home": HOME が取れない・絶対パスのディレクトリでなく、ラッパーの場所を決められなかった
  csb: ProcessOutcome | "missing" | "no-home";
  ps: ProcessOutcome;
  compose: ProcessOutcome;
};

// 3 つのプロセスの結果から、区分ごとの表示を作る。ある区分が取れなくても、他の区分は
// 出す (取れなかった区分は 1 行の知らせだけ)。
export function buildSnapshot(
  now: number,
  home: string,
  outcomes: Outcomes,
): Snapshot {
  const psNotice = processNotice("docker ps", outcomes.ps);
  // 出力が上限で切れた: 読めた分 (途中の行は捨てた) は、全件ではない
  const isPsTruncated =
    outcomes.ps.status === "fulfilled" && outcomes.ps.value.isStdoutTruncated;
  let containers: DockerContainer[] | null = null;
  let containersNotice: string | null = psNotice;
  if (outcomes.ps.status === "fulfilled" && psNotice === null) {
    containers = parseDockerPs(
      outcomes.ps.value.stdout,
      outcomes.ps.value.isStdoutTruncated,
    );
    if (containers === null) {
      containersNotice = "docker ps の出力を読めませんでした";
    }
  }

  let csbRows: SandboxRow[] | null = null;
  let csbNotice: string | null = null;
  if (outcomes.csb === "missing" || outcomes.csb === "no-home") {
    csbNotice =
      outcomes.csb === "no-home" ? NOTICE_CSB_NO_HOME : NOTICE_CSB_MISSING;
  } else {
    csbNotice = processNotice("csb status", outcomes.csb);
    if (outcomes.csb.status === "fulfilled" && csbNotice === null) {
      csbRows = parseCsbStatus(outcomes.csb.value.stdout, home);
      if (csbRows === null) csbNotice = "csb status の出力を読めませんでした";
    }
  }

  let composeRows: ComposeRow[] | null = null;
  let composeNotice = processNotice("docker compose ls", outcomes.compose);
  if (outcomes.compose.status === "fulfilled" && composeNotice === null) {
    composeRows = parseComposeLs(outcomes.compose.value.stdout, home);
    if (composeRows === null) {
      composeNotice = "docker compose ls の出力を読めませんでした";
    }
  }

  const sandboxes =
    csbRows === null
      ? failed<SandboxRow>(csbNotice ?? NOTICE_CSB_MISSING)
      : capped(withDockerFacts(csbRows, containers, !isPsTruncated));
  const others =
    containers === null
      ? failed<ContainerRow>(containersNotice ?? "docker ps を読めませんでした")
      : otherContainers(containers, new Set((csbRows ?? []).map((r) => r.id)));
  if (containers !== null && isPsTruncated) {
    others.notice = NOTICE_TRUNCATED;
  }
  const compose =
    composeRows === null
      ? failed<ComposeRow>(
          composeNotice ?? "docker compose ls を読めませんでした",
        )
      : capped(composeRows);

  return { at: now, sandboxes, containers: others, compose };
}

const pad = (n: number) => String(n).padStart(2, "0");

// ローカル時刻の HH:MM (withSeconds なら HH:MM:SS)。
export function formatTime(ms: number, withSeconds = false): string {
  const date = new Date(ms);
  const base = `${pad(date.getHours())}:${pad(date.getMinutes())}`;

  return withSeconds ? `${base}:${pad(date.getSeconds())}` : base;
}

const SEP = "  ";
const MARK_BIND = "[bind mount あり]";
const MARK_BIND_UNKNOWN = "[bind mount 不明]";
const MARK_LABEL = "[csb ラベル]";

// csb の net_connected (実際の接続) と net_expired から。期限切れでもまだ繋がって
// いるコンテナを「切断」と出さない。net_connected の無い古い csb の出力は、
// net_on_until (期限内なら数値) で on / off を決める。
function netState(
  row: Record<string, unknown>,
  netUntilMs: number | null,
): NetState {
  if (!("net_connected" in row)) return netUntilMs === null ? "off" : "on";
  const connected = row.net_connected;
  if (connected === false) return "off";
  if (connected !== true) return "unknown";
  if (netUntilMs !== null) return "on";
  return row.net_expired === true ? "expired" : "nowindow";
}

function netText(row: SandboxRow): string {
  switch (row.net) {
    case "on":
      return row.netUntilMs === null
        ? "接続中"
        : `接続中 (${formatTime(row.netUntilMs)} まで)`;
    case "expired":
      return "接続中 (期限切れ。次の csb の呼び出しで切断)";
    case "nowindow":
      return "接続中 (期限の記録なし)";
    case "off":
      return "切断";
    default:
      return "不明";
  }
}

export function sandboxHeader(row: SandboxRow): string {
  if (row.broken !== null) return `${row.id}${SEP}壊れている: ${row.broken}`;
  const net = netText(row);

  return [
    row.id,
    row.state,
    `役割: ${roleText(row.roles)}`,
    `ネット: ${net}`,
    ...(row.hasBind === true ? [MARK_BIND] : []),
    // 取れていないときに、印が無い (= bind mount は無い) ように見せない
    ...(row.hasBind === null ? [MARK_BIND_UNKNOWN] : []),
  ].join(SEP);
}

// null は取れていない (docker ps を読めない・途中で切れた)。空は「読めて、無かった」。
function roleText(roles: string[] | null): string {
  if (roles === null) return "不明";

  return roles.length === 0 ? "-" : roles.join(", ");
}

export function patchLine(patch: PatchRow): string {
  if (patch.error !== null) return `patch ${patch.sha}${SEP}${patch.error}`;

  return [
    `patch ${patch.sha}`,
    `verify ${patch.verify}`,
    ...(patch.reviews.length === 0 ? [] : [patch.reviews.join(" ")]),
    `(${patch.files} files +${patch.added}/-${patch.removed})`,
  ].join(SEP);
}

export function containerLine(row: ContainerRow): string {
  return [
    row.name,
    row.image,
    row.status,
    ...(row.hasBind ? [MARK_BIND] : []),
    ...(row.isSandboxLabeled ? [MARK_LABEL] : []),
  ].join(SEP);
}

export function composeLine(row: ComposeRow): string {
  const line = [row.name, row.status, ...(row.file === "" ? [] : [row.file])];

  return line.join(SEP) + (row.moreFiles > 0 ? ` (+${row.moreFiles})` : "");
}

export function sectionTitle(name: string, section: Section<unknown>): string {
  return section.rows.length === 0 && section.notice !== null
    ? `${name} (取得できず)`
    : `${name} (${section.rows.length + section.hidden})`;
}

function countText(name: string, section: Section<unknown>): string {
  return section.rows.length === 0 && section.notice !== null
    ? `${name} 取得できず`
    : `${name} ${section.rows.length + section.hidden} 件`;
}

// コマンドの本文の先頭: 区分ごとの件数と、取れなかった区分の知らせ。
export function summarize(snapshot: Snapshot): string[] {
  const { sandboxes, containers, compose } = snapshot;

  return [
    `containers: ${countText(TITLE_SANDBOXES, sandboxes)} · ${countText(TITLE_CONTAINERS, containers)} · ${countText("compose", compose)}`,
    ...[sandboxes.notice, containers.notice, compose.notice]
      .filter((notice): notice is string => notice !== null)
      .map((notice) => `- ${notice}`),
  ];
}

function listed(title: string, lines: string[], hidden: number): string[] {
  if (lines.length === 0) return [];
  const shown = lines.slice(0, MAX_TEXT_ROWS);
  const more = lines.length - shown.length + hidden;

  return [
    `${title}:`,
    ...shown.map((line) => `  ${line}`),
    ...(more > 0 ? [`  他 ${more} 件`] : []),
  ];
}

// ペインを置けない場所で、本文 (モデルも読む) に添える行。区分ごとに先頭だけ。
export function listRows(snapshot: Snapshot): string[] {
  return [
    ...listed(
      TITLE_SANDBOXES,
      snapshot.sandboxes.rows.map(sandboxHeader),
      snapshot.sandboxes.hidden,
    ),
    ...listed(
      TITLE_CONTAINERS,
      snapshot.containers.rows.map(containerLine),
      snapshot.containers.hidden,
    ),
    ...listed(
      TITLE_COMPOSE,
      snapshot.compose.rows.map(composeLine),
      snapshot.compose.hidden,
    ),
  ];
}
