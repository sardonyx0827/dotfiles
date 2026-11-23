import { describe, expect, mock, test } from "claude-code/testing";
import type { Engine } from "claude-code/testing";
import type { On } from "claude-code";

const HOME = "/home/tester";
const LOG_PATH = `${HOME}/.claude/logs/bash-review.log`;
const DETAIL_DIR = "/tmp/claude_hooks/logs/PreToolUse/Bash/bash-review";
const USAGE =
  "使い方: /bash-review-log [all|allow|ask|deny|error|flagged ...] [件数 1-500] [full|short]";

const LONG_FULL =
  "git log --oneline --graph --decorate --all --date=short --pretty=format:'%h %ad' | head -50";
const LONG_KEY = Array.from(LONG_FULL).slice(0, 80).join("");

const LOG = [
  "[2026-10-04 20:00:00] ALLOW | pre      | git status | safe command",
  `[2026-10-04 20:00:01] ALLOW | gemini   | ${LONG_KEY}... | approved by Gemini, took=0.8s`,
  "[2026-10-04 20:00:02] ASK   | fallback | npm publish | gemini=ASK, codex=ERROR, took=4.2s",
  "[2026-10-04 20:00:03] DENY  | pre      | rm -rf / | Blocked dangerous command: 'rm'",
  "",
].join("\n");

function detail(command: string, description?: string): string {
  const input =
    description === undefined ? { command } : { command, description };

  return `Tool Name: Bash\nTool Input: ${JSON.stringify(input)}\nResult: ALLOW\n`;
}

const DETAILS: Record<string, string> = {
  "bash_cmd_1000_1.log": detail("git status"),
  "bash_cmd_2000_2.log": detail(LONG_FULL, "履歴をグラフで見る"),
  "bash_cmd_3000_3.log": detail("npm publish"),
  "bash_cmd_4000_4.log": detail("rm -rf /"),
};

// サマリーの at はローカル時刻。詳細ログは write_detail_log の直後 (数 ms 前) に書かれる。
function localMs(clock: string): number {
  return new Date(`2026-10-04T${clock}`).getTime();
}

const DETAIL_MTIMES: Record<string, number> = {
  "bash_cmd_1000_1.log": localMs("20:00:00.3"),
  "bash_cmd_2000_2.log": localMs("20:00:01.3"),
  "bash_cmd_3000_3.log": localMs("20:00:02.3"),
  "bash_cmd_4000_4.log": localMs("20:00:03.3"),
};
const UID = "501";
const DETAIL_LEVELS = [
  "/tmp/claude_hooks",
  "/tmp/claude_hooks/logs",
  "/tmp/claude_hooks/logs/PreToolUse",
  "/tmp/claude_hooks/logs/PreToolUse/Bash",
  DETAIL_DIR,
];
const DETAIL_MAX_BYTES = 256 * 1024;

const SUMMARY = "bash-review: 直近 4 件 — ALLOW 2 · ASK 1 · DENY 1 · ERROR 1";

const PANE_PROPS = {
  title: "bash-review log",
  isFocused: true,
  bodyColumns: 120,
  placement: "dock" as const,
  scroll: { offset: 0, bodyRows: 40 },
  view: {},
};

// テストの on は mod の下 (エンジンの位置) に座るので、mod が $ で呼ぶものに答える。
type Listed = {
  name: string;
  kind: "file" | "dir" | "other";
  size: number;
  mtimeMs: number;
  isLink: boolean;
};

function world(
  on: On,
  options: {
    log?: string;
    isPlaced?: boolean;
    details?: Record<string, string>;
    mtimes?: Record<string, number>;
    extraListed?: Listed[];
    // 一覧には載っているが、読もうとすると決して終わらない詳細ログ
    hung?: string[];
    // 一覧のあとで FIFO に差し替えられた詳細ログ: 範囲なしの read は塞がり、範囲付きは拒まれる
    fifo?: string[];
    // サマリーログの read が終わらない
    logHung?: boolean;
    // サマリーログの 1 回目の read だけ、この Promise が決まるまで終わらない (2 回目以降は log)
    firstLogRead?: Promise<{ value: string } | { deny: string }>;
    // 自分の所有でないディレクトリの階層 / ファイル
    foreignDirs?: string[];
    foreignFiles?: string[];
    // process.run が使えない
    processDenied?: boolean;
    // true なら mock.clock を使わない (clock のフックをテストが自前で置く)
    ownClock?: boolean;
  } = {},
) {
  mock.env(on, { HOME });
  const clock = options.ownClock === true ? undefined : mock.clock(on);
  const details = options.details ?? DETAILS;
  const reads: string[] = [];
  const readCalls: { path: string; offset?: number; length?: number }[] = [];
  const runs: string[][] = [];
  const opened: string[] = [];
  let logReads = 0;

  on("fs.read", ($, e) => {
    reads.push(e.path);
    readCalls.push({ path: e.path, offset: e.offset, length: e.length });
    if (e.path === LOG_PATH) {
      logReads += 1;
      if (options.firstLogRead !== undefined && logReads === 1)
        return options.firstLogRead;
      if (options.logHung === true) return new Promise<never>(() => {});
      return options.log === undefined
        ? { deny: "ENOENT" }
        : { value: options.log };
    }
    const name = e.path.replace(`${DETAIL_DIR}/`, "");
    if (options.hung?.includes(name) === true)
      return new Promise<never>(() => {});
    if (options.fifo?.includes(name) === true) {
      return e.length === undefined
        ? new Promise<never>(() => {})
        : { deny: "ENXIO" };
    }
    const text = details[name];

    return text === undefined ? { deny: "ENOENT" } : { value: text };
  });
  on("fs.list", () => ({
    value: [
      ...[...Object.keys(details), "unrelated.txt"].map((name) => ({
        name,
        kind: "file" as const,
        size: new TextEncoder().encode(details[name] ?? "").length,
        mtimeMs: options.mtimes?.[name] ?? DETAIL_MTIMES[name] ?? 1,
        isLink: false,
      })),
      ...(options.extraListed ?? []),
    ],
  }));
  on("fs.stat", () => ({
    value: { kind: "file", size: 0, mtimeMs: 1, isLink: false },
  }));
  on("process.run", ($, e) => {
    const argv = [...e.argv];
    runs.push(argv);
    if (options.processDenied === true) return { deny: "no process" };
    const ok = (stdout: string) => ({
      value: {
        exitCode: 0,
        stdout,
        stderr: "",
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    });
    if (argv[0] === "id") return ok(`${UID}\n`);
    if (argv[0] === "find") {
      const depth = argv.indexOf("-maxdepth");
      // find <階層...> -maxdepth 0 -type d -user <uid>: 自分の所有する実ディレクトリだけが出る
      if (argv[depth + 1] === "0") {
        return ok(
          argv
            .slice(1, depth)
            .filter((path) => options.foreignDirs?.includes(path) !== true)
            .map((path) => `${path}\n`)
            .join(""),
        );
      }
      // find <dir> -mindepth 1 -maxdepth 1 -type f -user <uid> -name ...: 自分の所有する通常ファイルだけ
      return ok(
        Object.keys(details)
          .filter((name) => options.foreignFiles?.includes(name) !== true)
          .map((name) => `${DETAIL_DIR}/${name}\n`)
          .join(""),
      );
    }

    return { deny: "unexpected command" };
  });
  on("ui.open", ($, e) => {
    opened.push(e.id);

    return {
      value:
        options.isPlaced === false
          ? { isPlaced: false, reason: "no surface places panes" }
          : { isPlaced: true },
    };
  });
  on("ui.close", () => ({ value: undefined }));
  on("ui.panes", () => ({ value: [] }));
  on("command.register", ($, e) => ({ value: { command: e.name } }));

  return { reads, readCalls, runs, opened, clock };
}

// 人がプロンプトで /bash-review-log [args] と打ったときの入力。
function run($: Engine, args = "") {
  return $.command.run({
    command: "bash-review-log",
    args,
    origin: { kind: "composer" },
    presentation: { isFullscreen: true, columns: 120 },
  });
}

function mountPane($: Engine, surface: "terminal" | "desktop" = "terminal") {
  return $.ui.mount({
    plugin: "bash-review-log",
    surface,
    component: "Pane",
    requestId: "bash-review-log",
    props: PANE_PROPS,
  });
}

describe("/bash-review-log", () => {
  test("ログと詳細ログを読み、ペインを開いて件数と表示条件を返す", async ($, on) => {
    const { reads, opened } = world(on, { log: LOG });

    const result = await run($);

    expect(reads).toContain(LOG_PATH);
    expect(reads).toContain(`${DETAIL_DIR}/bash_cmd_2000_2.log`);
    expect(opened).toEqual(["bash-review-log"]);
    expect(result.text).toBe(`${SUMMARY}（表示: 全種別 / 最大 50 件 / 1 行）`);
  });

  test("未知の引数には使い方を返し、ペインは開かない", async ($, on) => {
    const { opened } = world(on, { log: LOG });

    const result = await run($, "deny-only");

    expect(result.text).toBe(USAGE);
    expect(opened).toEqual([]);
  });

  test("ログが読めなければ、そう返してペインは開かない", async ($, on) => {
    const { opened } = world(on);

    const result = await run($);

    expect(result.text).toBe(
      "bash-review: ログを読めませんでした (~/.claude/logs/bash-review.log)",
    );
    expect(result.text).not.toContain(HOME);
    expect(opened).toEqual([]);
  });

  test("ペインを描けない場所では、指定した種別の直近分を本文で返す", async ($, on) => {
    world(on, { log: LOG, isPlaced: false });

    const result = await run($, "flagged");

    expect(result.text).toBe(
      [
        `${SUMMARY}（表示: ASK・DENY・ERROR / 最大 50 件 / 1 行）`,
        "10-04 20:00:03 DENY  pre      rm -rf / — Blocked dangerous command: 'rm'",
        "10-04 20:00:02 ASK   fallback npm publish — gemini=ASK, codex=ERROR, took=4.2s",
      ].join("\n"),
    );
  });

  test("本文 (モデルも読む) には全文を載せず、最大 20 件にとどめる", async ($, on) => {
    const many = Array.from(
      { length: 25 },
      (_, i) =>
        `[2026-10-04 21:00:${String(i).padStart(2, "0")}] ALLOW | pre      | echo ${i} | safe command`,
    );
    world(on, { log: [LOG, ...many].join("\n"), isPlaced: false });

    const lines = (await run($, "allow 500")).text?.split("\n") ?? [];

    expect(lines).toHaveLength(1 + 20);
    expect(lines.join("\n")).not.toContain(LONG_FULL);
  });

  test("詳細ログは通常ファイルだけを読む (symlink・FIFO・巨大ファイルは読まない)", async ($, on) => {
    const { reads } = world(on, {
      log: LOG,
      extraListed: [
        {
          name: "bash_cmd_5000_5.log",
          kind: "file",
          size: 10,
          mtimeMs: 1,
          isLink: true,
        },
        {
          name: "bash_cmd_6000_6.log",
          kind: "other",
          size: 0,
          mtimeMs: 1,
          isLink: false,
        },
        {
          name: "bash_cmd_7000_7.log",
          kind: "file",
          size: 10 * 1024 * 1024,
          mtimeMs: 1,
          isLink: false,
        },
      ],
    });

    await run($);

    expect(reads).toContain(`${DETAIL_DIR}/bash_cmd_2000_2.log`);
    expect(reads.filter((path) => /bash_cmd_[567]000/.test(path))).toEqual([]);
  });
});

describe("ペイン", () => {
  test("種別ボタンで絞り込み、すべてで戻す", async ($, on) => {
    world(on, { log: LOG });
    await run($);

    for (const surface of ["terminal", "desktop"] as const) {
      const ui = await mountPane($, surface);

      expect(await ui.find({ text: /git status/ })).toBeDefined();
      expect(await ui.find({ text: /rm -rf \// })).toBeDefined();

      await ui.press({ key: "type-DENY" });
      expect(await ui.find({ text: /git status/ })).toBeUndefined();
      expect(await ui.find({ text: /npm publish/ })).toBeUndefined();
      expect(await ui.find({ text: /rm -rf \// })).toBeDefined();

      await ui.press({ key: "type-ERROR" });
      expect(await ui.find({ text: /npm publish/ })).toBeDefined();

      await ui.press({ key: "type-all" });
      expect(await ui.find({ text: /git status/ })).toBeDefined();
      await ui.unmount();
    }
  });

  test("引数 deny で開くと最初から DENY だけを出す", async ($, on) => {
    world(on, { log: LOG });
    await run($, "deny");

    const ui = await mountPane($);

    expect(await ui.find({ text: /git status/ })).toBeUndefined();
    expect(await ui.find({ text: /rm -rf \// })).toBeDefined();
    await ui.unmount();
  });

  test("description を見出し行に出し、全文ボタンでコマンドを折り返す", async ($, on) => {
    world(on, { log: LOG });
    await run($);

    const ui = await mountPane($);

    expect(await ui.find({ text: /履歴をグラフで見る/ })).toBeDefined();
    const before = await ui.find({ type: "Text", text: /head -50/ });
    expect(before?.props.wrap).toBe("truncate-end");

    await ui.press({ key: "full" });
    const after = await ui.find({ type: "Text", text: /head -50/ });
    expect(after?.props.wrap).toBe("wrap");
    await ui.unmount();
  });

  test("制御文字や 1 万字を超えるコマンドがあってもペインを描ける", async ($, on) => {
    // Text は 10000 字まで・制御文字はタブと改行だけ。破ればペイン全体が描けない。
    const huge = `cat <<'EOF' > big.txt\n${"x".repeat(12000)}\nEOF`;
    const hugeKey = Array.from(huge).slice(0, 80).join("");
    world(on, {
      log: [
        "[2026-10-04 20:00:00] ALLOW | pre      | printf '\u001b[31mred' | safe command",
        `[2026-10-04 20:00:01] ALLOW | gemini   | ${hugeKey}... | approved by Gemini, took=0.8s`,
      ].join("\n"),
      details: { "bash_cmd_1000_1.log": detail(huge, "大きなファイルを書く") },
    });
    await run($, "full");

    for (const surface of ["terminal", "desktop"] as const) {
      const ui = await mountPane($, surface);

      expect(await ui.find({ text: /printf '␛\[31mred'/ })).toBeDefined();
      expect(await ui.find({ text: /残り \d+ 字/ })).toBeDefined();
      await ui.unmount();
    }
  });
});

// 同じ先頭 80 字で、末尾だけが違う長いコマンド 2 本 (A が 20:00:00、B が 20:00:10)。
const LOG_AB = [
  `[2026-10-04 20:00:00] ALLOW | gemini   | ${LONG_KEY}... | approved by Gemini, took=0.8s`,
  `[2026-10-04 20:00:10] ALLOW | gemini   | ${LONG_KEY}... | approved by Gemini, took=0.9s`,
  "",
].join("\n");
const AB_MTIMES = {
  "bash_cmd_1000_1.log": localMs("20:00:00.3"),
  "bash_cmd_2000_2.log": localMs("20:00:10.3"),
};

describe("詳細ログとの対応づけ", () => {
  test("サマリーの detail= が指す詳細ログから全文を引く (時刻が合わなくても)", async ($, on) => {
    world(on, {
      log: [
        `[2026-10-04 20:00:00] ALLOW | gemini   | ${LONG_KEY}... | approved by Gemini, took=0.8s detail=bash_cmd_2000_2.log`,
        "",
      ].join("\n"),
      details: {
        "bash_cmd_1000_1.log": detail(`${LONG_FULL} # close in time`, "近い方"),
        "bash_cmd_2000_2.log": detail(`${LONG_FULL} # named`, "名前の方"),
      },
      mtimes: {
        "bash_cmd_1000_1.log": localMs("20:00:00.3"),
        "bash_cmd_2000_2.log": localMs("23:00:00.0"),
      },
    });
    await run($, "full");

    const ui = await mountPane($);

    expect(await ui.find({ text: /名前の方/ })).toBeDefined();
    expect(await ui.find({ text: /近い方/ })).toBeUndefined();
    expect(await ui.find({ text: /detail=bash_cmd/ })).toBeUndefined();
    await ui.unmount();
  });

  test("B の詳細ログが読めなくても、B の行に A の全文や description を付けない", async ($, on) => {
    // 順序だけで新しい方から 1 対 1 に対応づけると、B の行に A の内容が付く。
    world(on, {
      log: LOG_AB,
      mtimes: AB_MTIMES,
      details: {
        "bash_cmd_1000_1.log": detail(`${LONG_FULL} # a`, "説明A"),
      },
      // B の詳細ログは一覧にあるが、読めない
      extraListed: [
        {
          name: "bash_cmd_2000_2.log",
          kind: "file",
          size: 10,
          mtimeMs: AB_MTIMES["bash_cmd_2000_2.log"],
          isLink: false,
        },
      ],
    });
    await run($, "full");

    const ui = await mountPane($);

    const rowA = await ui.find({ type: "Text", text: /^10-04 20:00:00 ALLOW/ });
    const rowB = await ui.find({ type: "Text", text: /^10-04 20:00:10 ALLOW/ });
    expect(rowA?.text).toContain("説明A");
    expect(rowB?.text).not.toContain("説明A");
    expect(await ui.findAll({ type: "Text", text: /# a/ })).toHaveLength(1);
    await ui.unmount();
  });

  test("書きかけ (空) の詳細ログは覚え込まず、書き終わったら次の読み込みで使う", async ($, on) => {
    const details = { ...DETAILS, "bash_cmd_2000_2.log": "" };
    world(on, { log: LOG, details });
    await run($);
    let ui = await mountPane($);
    expect(await ui.find({ text: /履歴をグラフで見る/ })).toBeUndefined();
    await ui.unmount();

    details["bash_cmd_2000_2.log"] = detail(LONG_FULL, "履歴をグラフで見る");
    await run($);

    ui = await mountPane($);
    expect(await ui.find({ text: /履歴をグラフで見る/ })).toBeDefined();
    await ui.unmount();
  });

  test("詳細ログは先頭から上限までの範囲で読む (FIFO に差し替えられても塞がらない)", async ($, on) => {
    const { readCalls } = world(on, {
      log: LOG,
      fifo: ["bash_cmd_3000_3.log"],
    });

    const result = await run($);

    expect(result.text).toBe(`${SUMMARY}（表示: 全種別 / 最大 50 件 / 1 行）`);
    const detailReads = readCalls.filter((call) =>
      call.path.startsWith(`${DETAIL_DIR}/`),
    );
    expect(detailReads.length).toBeGreaterThan(0);
    for (const call of detailReads) {
      expect(call.offset).toBe(0);
      expect(call.length).toBe(DETAIL_MAX_BYTES);
    }
  });

  test("詳細ログの読み込みが終わらなくても、時間で切り上げて全文なしで表示し、更新は止まらない", async ($, on) => {
    const w = world(on, { log: LOG, hung: ["bash_cmd_2000_2.log"] });

    const pending = run($);
    await w.clock?.advance(10_000);
    const result = await pending;

    expect(result.text).toBe(`${SUMMARY}（表示: 全種別 / 最大 50 件 / 1 行）`);
    const ui = await mountPane($);
    expect(await ui.find({ text: /履歴をグラフで見る/ })).toBeUndefined();
    expect(
      await ui.find({ text: /詳細ログの読み込みが時間内に終わりませんでした/ }),
    ).toBeDefined();
    await ui.unmount();

    // 終わらなかった読み込みが inFlight を塞いでいない: 次の読み込みも返る。
    const again = await run($);
    expect(again.text).toBe(`${SUMMARY}（表示: 全種別 / 最大 50 件 / 1 行）`);
  });
});

describe("ログの読み込みが終わらないとき", () => {
  test("サマリーログの読み込みが終わらなくても、時間で切り上げてエラーを返し、次の読み込みを塞がない", async ($, on) => {
    const w = world(on, { logHung: true, log: LOG });
    const pending = run($);
    await w.clock?.advance(10_000);

    const result = await pending;

    expect(result.text).toBe(
      "bash-review: ログの読み込みが時間内に終わりませんでした (~/.claude/logs/bash-review.log)",
    );
    // inFlight が解かれている: 2 回目も (また時間切れまで待って) 返ってくる。
    const again = run($);
    await w.clock?.advance(10_000);
    expect((await again).text).toBe(result.text);
  });

  // 時間切れで見限った読み込みは止められない。あとから終わって、その間に済んだ新しい
  // 読み込みの結果 (内容・エラー) を、古い内容で上書きしてはならない。
  describe("時間切れで見限った古い読み込みが、あとから終わったとき", () => {
    const STALE = `[2026-10-04 19:00:00] ALLOW | pre      | echo stale | safe command\n`;
    const FRESH = `[2026-10-04 20:00:00] ALLOW | pre      | echo fresh | safe command\n`;
    const TIMED_OUT =
      "bash-review: ログの読み込みが時間内に終わりませんでした (~/.claude/logs/bash-review.log)";

    // 1 回目の読み込みは時間切れにし、2 回目で新しい内容を読む。1 回目の read はまだ終わっていない。
    async function staleThenFresh(
      $: Engine,
      on: On,
    ): Promise<{
      settle: (outcome: { value: string } | { deny: string }) => Promise<void>;
    }> {
      let finish: (
        outcome: { value: string } | { deny: string },
      ) => void = () => {};
      const first = new Promise<{ value: string } | { deny: string }>(
        (resolve) => {
          finish = resolve;
        },
      );
      const w = world(on, { log: FRESH, details: {}, firstLogRead: first });

      const timedOut = run($);
      await w.clock?.advance(10_000);
      expect((await timedOut).text).toBe(TIMED_OUT);
      expect((await run($)).text).toContain("直近 1 件");

      return {
        settle: async (outcome) => {
          finish(outcome);
          await w.clock?.settle();
        },
      };
    }

    test("古い内容で、新しい内容を上書きしない", async ($, on) => {
      const { settle } = await staleThenFresh($, on);
      const ui = await mountPane($);
      expect(await ui.find({ text: /echo fresh/ })).toBeDefined();

      await settle({ value: STALE });

      expect(await ui.find({ text: /echo fresh/ })).toBeDefined();
      expect(await ui.find({ text: /echo stale/ })).toBeUndefined();
      await ui.unmount();
    });

    test("古い読み込みの失敗で、新しい内容の表示にエラーを出さない", async ($, on) => {
      const { settle } = await staleThenFresh($, on);
      const ui = await mountPane($);

      await settle({ deny: "EIO" });

      expect(await ui.find({ text: /echo fresh/ })).toBeDefined();
      expect(await ui.find({ text: /ログを読めませんでした/ })).toBeUndefined();
      expect(
        await ui.find({ text: /ログの読み込みが時間内に終わりませんでした/ }),
      ).toBeUndefined();
      await ui.unmount();
    });
  });
});

describe("詳細ログの所有者の検査", () => {
  test("置き場のどの階層かが自分の所有する実ディレクトリでなければ、詳細ログを 1 本も読まない", async ($, on) => {
    const { reads } = world(on, {
      log: LOG,
      foreignDirs: ["/tmp/claude_hooks"],
    });

    await run($);

    expect(reads.filter((path) => path.startsWith(DETAIL_DIR))).toEqual([]);
    const ui = await mountPane($);
    expect(await ui.find({ text: /全文を引けません/ })).toBeDefined();
    expect(await ui.find({ text: /履歴をグラフで見る/ })).toBeUndefined();
    await ui.unmount();
  });

  test("自分の所有でない詳細ログは読まず、ほかは読む", async ($, on) => {
    const { reads } = world(on, {
      log: LOG,
      foreignFiles: ["bash_cmd_2000_2.log"],
    });

    await run($);

    expect(reads).not.toContain(`${DETAIL_DIR}/bash_cmd_2000_2.log`);
    expect(reads).toContain(`${DETAIL_DIR}/bash_cmd_3000_3.log`);
  });

  test("所有者を確かめられない (process が使えない) ときは詳細ログを読まない", async ($, on) => {
    const { reads } = world(on, { log: LOG, processDenied: true });

    const result = await run($);

    expect(result.text).toBe(`${SUMMARY}（表示: 全種別 / 最大 50 件 / 1 行）`);
    expect(reads.filter((path) => path.startsWith(DETAIL_DIR))).toEqual([]);
    const ui = await mountPane($);
    expect(await ui.find({ text: /全文を引けません/ })).toBeDefined();
    await ui.unmount();
  });

  test("find には階層の検査と、ファイルの検査 (所有者・通常ファイル) を渡し、シェルは通さない", async ($, on) => {
    const { runs } = world(on, { log: LOG });

    await run($);

    expect(runs).toContainEqual([
      "find",
      ...DETAIL_LEVELS,
      "-maxdepth",
      "0",
      "-type",
      "d",
      "-user",
      UID,
    ]);
    expect(runs).toContainEqual([
      "find",
      DETAIL_DIR,
      "-mindepth",
      "1",
      "-maxdepth",
      "1",
      "-type",
      "f",
      "-user",
      UID,
      "-name",
      "bash_cmd_*.log",
    ]);
    expect(
      runs.filter((argv) => argv[0] === "sh" || argv[0] === "bash"),
    ).toEqual([]);
  });
});

describe("ペインの描画", () => {
  test("decision が Object.prototype のキー (constructor) でも描ける", async ($, on) => {
    world(on, {
      log: "[2026-10-04 20:00:00] constructor | pre      | git status | x\n",
    });
    await run($);

    const ui = await mountPane($);

    const decision = await ui.find({ type: "Text", text: /^constructor$/ });
    expect(decision).toBeDefined();
    expect(decision?.props.color).toBe("white");
    await ui.unmount();
  });

  test("エントリの Box には key を付ける", async ($, on) => {
    world(on, { log: LOG });
    await run($);

    const ui = await mountPane($);

    const keyed = (await ui.findAll({ type: "Box" })).filter(
      (box) => box.key !== undefined && box.key.startsWith("entry-"),
    );
    expect(keyed).toHaveLength(4);
    expect(new Set(keyed.map((box) => box.key)).size).toBe(4);
    await ui.unmount();
  });
});

// テストの子プロセスには setTimeout があるが、型の lib (es2023) には無い。
function sleep(ms: number): Promise<void> {
  const host = globalThis as unknown as {
    setTimeout: (fn: () => void, ms: number) => unknown;
  };

  return new Promise((resolve) => {
    host.setTimeout(resolve, ms);
  });
}

describe("更新のタイマー", () => {
  test("ペインを開いている間だけ更新を待ち、閉じたら止める", async ($, on) => {
    const waiters: (() => void)[] = [];
    let requested = 0;
    on("clock.every", () => {
      requested += 1;

      return new Promise<{ value: undefined }>((resolve) => {
        waiters.push(() => resolve({ value: undefined }));
      });
    });
    on("clock.after", () => new Promise<{ value: undefined }>(() => {}));
    world(on, { log: LOG, ownClock: true });
    const tick = async () => {
      for (const wake of waiters.splice(0)) wake();
      await sleep(5);
    };

    expect(requested).toBe(0);
    await run($);
    await tick();
    await tick();
    expect(requested).toBeGreaterThanOrEqual(2);

    // 「閉じる」ボタン ($.ui.close) でペインを閉じる。
    const ui = await mountPane($);
    await ui.press({ key: "close" });
    const before = requested;
    await tick();
    await tick();
    expect(requested).toBe(before);
    await ui.unmount();
  });
});
