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
    extraListed?: Listed[];
  } = {},
) {
  mock.env(on, { HOME });
  mock.clock(on);
  const details = options.details ?? DETAILS;
  const reads: string[] = [];
  const opened: string[] = [];

  on("fs.read", ($, e) => {
    reads.push(e.path);
    if (e.path === LOG_PATH) {
      return options.log === undefined
        ? { deny: "ENOENT" }
        : { value: options.log };
    }
    const text = details[e.path.replace(`${DETAIL_DIR}/`, "")];

    return text === undefined ? { deny: "ENOENT" } : { value: text };
  });
  on("fs.list", () => ({
    value: [
      ...[...Object.keys(details), "unrelated.txt"].map((name) => ({
        name,
        kind: "file" as const,
        size: 0,
        mtimeMs: 1,
        isLink: false,
      })),
      ...(options.extraListed ?? []),
    ],
  }));
  on("fs.stat", () => ({
    value: { kind: "file", size: 0, mtimeMs: 1, isLink: false },
  }));
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

  return { reads, opened };
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
      `bash-review: ログを読めませんでした (${LOG_PATH})`,
    );
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
