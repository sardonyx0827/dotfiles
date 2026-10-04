import { describe, expect, test } from "claude-code/testing";

import {
  attachDetails,
  countDecisions,
  describeView,
  displayText,
  formatEntry,
  parseArgs,
  parseDetail,
  parseLog,
  selectEntries,
  toggleType,
  toOneLine,
} from "../hooks/log";

// log_summary() が 80 字を超えるコマンドに書く形 (先頭 80 字 + "...")。
const LONG =
  "git log --oneline --graph --decorate --all --date=short --pretty=format:'%h %ad'";
const LONG_FULL = `${LONG} | head -50`;
const LONG_KEY = Array.from(LONG_FULL).slice(0, 80).join("");

describe("parseLog", () => {
  test('コマンド中の " | " は残し、最後の " | " で reason を切り出す', () => {
    const text =
      "[2026-10-04 20:24:39] ALLOW | pre      | ls | grep foo | head | safe command\n";

    expect(parseLog(text)).toEqual([
      {
        at: "2026-10-04 20:24:39",
        decision: "ALLOW",
        stage: "pre",
        command: "ls | grep foo | head",
        reason: "safe command",
      },
    ]);
  });

  test("複数行コマンドの継続行は直前のエントリのコマンドへ連結する", () => {
    const text = [
      "[2026-10-04 20:00:00] ASK   | highrisk | python3 - <<EOF",
      "import yaml",
      "print(1)... | risk=stdin into python3, gemini=ALLOW, codex=ASK, took=3.1s",
      "[2026-10-04 20:00:05] DENY  | pre      | rm -rf / | Blocked dangerous command: 'rm'",
      "",
    ].join("\n");

    const entries = parseLog(text);

    expect(entries).toHaveLength(2);
    expect(entries[0]?.command).toBe(
      "python3 - <<EOF\nimport yaml\nprint(1)...",
    );
    expect(entries[0]?.reason).toBe(
      "risk=stdin into python3, gemini=ALLOW, codex=ASK, took=3.1s",
    );
    expect(entries[1]?.decision).toBe("DENY");
  });

  test("ローテーションで頭が切れた継続行 (先頭のヘッダなし行) は捨てる", () => {
    const text = [
      "a = yaml.safe_load(...) | risk=stdin into python3, took=37.2s",
      "[2026-10-04 20:00:00] ALLOW | gemini   | git status | approved by Gemini, took=0.8s",
    ].join("\n");

    const entries = parseLog(text);

    expect(entries).toHaveLength(1);
    expect(entries[0]?.command).toBe("git status");
  });

  test("空のログは空配列", () => {
    expect(parseLog("")).toEqual([]);
  });

  test("コマンドに行区切り文字 (U+2028) を含むヘッダ行も 1 エントリとして読む", () => {
    const text = [
      "[2026-10-04 20:00:00] ALLOW | pre      | ls | safe command",
      "[2026-10-04 20:00:01] DENY  | pre      | echo \u2028x | Blocked dangerous command: 'x'",
    ].join("\n");

    const entries = parseLog(text);

    expect(entries).toHaveLength(2);
    expect(entries[1]?.decision).toBe("DENY");
    expect(entries[0]?.reason).toBe("safe command");
  });
});

describe("displayText", () => {
  test("Text が受け付けない制御文字を見える記号に置き換え、改行とタブは残す", () => {
    expect(
      displayText("a\u001b[31mb\u0000c\u0007d\u007fe\u009bf\tg\nh", 100),
    ).toBe("a␛[31mb␀c␇d␡e�f\tg\nh");
  });

  test("bidi 制御文字と行区切り文字も置き換える (表示の偽装を防ぐ)", () => {
    expect(displayText("rm \u202ecod.sh\u2028x", 100)).toBe("rm �cod.sh�x");
  });

  test("max 字を超えたら切り、残りの字数を添える", () => {
    expect(displayText("あいうえおかきくけこ", 4)).toBe(
      "あいうえ…（残り 6 字）",
    );
    expect(displayText("abc", 3)).toBe("abc");
  });
});

describe("formatEntry", () => {
  test("本文用の 1 行にはサマリーのコマンド (80 字まで) を使い、詳細ログの全文は載せない", () => {
    const [entry] = parseLog(
      `[2026-10-04 20:00:01] ALLOW | gemini   | ${LONG_KEY}... | approved by Gemini, took=0.8s`,
    );
    if (entry === undefined) throw new Error("no entry");

    expect(formatEntry({ ...entry, fullCommand: LONG_FULL })).toBe(
      `10-04 20:00:01 ALLOW gemini   ${LONG_KEY}... — approved by Gemini, took=0.8s`,
    );
  });

  test("制御文字は本文でも置き換える", () => {
    const [entry] = parseLog(
      "[2026-10-04 20:00:01] ALLOW | pre      | printf '\u001b[31m' | safe command",
    );
    if (entry === undefined) throw new Error("no entry");

    expect(formatEntry(entry)).toBe(
      "10-04 20:00:01 ALLOW pre      printf '␛[31m' — safe command",
    );
  });
});

describe("countDecisions", () => {
  test("判定ごとに数え、レビュアーのエラーは error に、未知の判定は other に入れる", () => {
    const entries = parseLog(
      [
        "[2026-10-04 20:00:00] ALLOW | pre      | ls | safe command",
        "[2026-10-04 20:00:01] ALLOW | codex    | git log | gemini=ERROR, codex=ALLOW, took=2.0s",
        "[2026-10-04 20:00:02] ASK   | fallback | npm i | gemini=ASK, codex=ERROR, took=9.0s",
        "[2026-10-04 20:00:03] DENY  | codex    | x | gemini=ASK, codex=DENY, took=9.0s",
        "[2026-10-04 20:00:04] WEIRD | pre      | y | z",
      ].join("\n"),
    );

    expect(countDecisions(entries)).toEqual({
      allow: 2,
      ask: 1,
      deny: 1,
      error: 2,
      other: 1,
    });
  });
});

describe("parseArgs", () => {
  test("引数なしは全種別・50 件・1 行表示", () => {
    expect(parseArgs("")).toEqual({ types: [], limit: 50, isFull: false });
  });

  test("種別・件数・full を順不同で受け取り、種別は大文字小文字を問わない", () => {
    expect(parseArgs("full DENY 20 ask")).toEqual({
      types: ["ASK", "DENY"],
      limit: 20,
      isFull: true,
    });
  });

  test("flagged は ASK・DENY・ERROR、all は種別の絞り込みを外す", () => {
    expect(parseArgs("flagged")?.types).toEqual(["ASK", "DENY", "ERROR"]);
    expect(parseArgs("deny all")?.types).toEqual([]);
  });

  test("件数は 1 から 500 に収める", () => {
    expect(parseArgs("9999")?.limit).toBe(500);
    expect(parseArgs("0")).toBeUndefined();
  });

  test("知らない語は undefined (使い方を返させる)", () => {
    expect(parseArgs("deny-only")).toBeUndefined();
  });
});

describe("toggleType", () => {
  test("含まれていれば外し、なければ決まった順で足す", () => {
    const view = { types: ["DENY" as const], limit: 50, isFull: false };

    expect(toggleType(view, "ASK").types).toEqual(["ASK", "DENY"]);
    expect(toggleType(view, "DENY").types).toEqual([]);
  });
});

describe("describeView", () => {
  test("種別・件数・表示形式を 1 行で表す", () => {
    expect(describeView({ types: [], limit: 50, isFull: false })).toBe(
      "全種別 / 最大 50 件 / 1 行",
    );
    expect(
      describeView({ types: ["ASK", "ERROR"], limit: 10, isFull: true }),
    ).toBe("ASK・ERROR / 最大 10 件 / 全文");
  });
});

describe("selectEntries", () => {
  const entries = parseLog(
    [
      "[2026-10-04 20:00:00] ALLOW | pre      | a | safe command",
      "[2026-10-04 20:00:01] ASK   | codex    | b | gemini=ASK, codex=ASK, took=1.0s",
      "[2026-10-04 20:00:02] ALLOW | codex    | c | gemini=ERROR, codex=ALLOW, took=2.0s",
      "[2026-10-04 20:00:03] DENY  | pre      | d | Blocked dangerous command: 'dd'",
    ].join("\n"),
  );

  test("種別の指定がなければ全件を新しい順に並べる", () => {
    expect(selectEntries(entries, [], 10).map((e) => e.command)).toEqual([
      "d",
      "c",
      "b",
      "a",
    ]);
  });

  test("指定した種別のどれかに当たるものだけを残す", () => {
    expect(
      selectEntries(entries, ["ASK", "DENY"], 10).map((e) => e.command),
    ).toEqual(["d", "b"]);
  });

  test("ERROR は reason に gemini=ERROR / codex=ERROR を含むもの", () => {
    expect(selectEntries(entries, ["ERROR"], 10).map((e) => e.command)).toEqual(
      ["c"],
    );
  });

  test("limit 件で打ち切る", () => {
    expect(selectEntries(entries, [], 2).map((e) => e.command)).toEqual([
      "d",
      "c",
    ]);
  });
});

describe("parseDetail", () => {
  test("Tool Input の JSON から command と description を取り出す", () => {
    const text = [
      "Tool Name: Bash",
      `Tool Input: ${JSON.stringify({ command: "ls -la\necho ok", description: "一覧" })}`,
      "Gemini: ALLOW",
      "Result: ALLOW (gemini)",
    ].join("\n");

    expect(parseDetail(text)).toEqual({
      command: "ls -la\necho ok",
      description: "一覧",
    });
  });

  test("秘密検出で伏せられた Tool Input は使わない", () => {
    expect(
      parseDetail(
        "Tool Name: Bash\nTool Input: [REDACTED - credential detected]\n",
      ),
    ).toBeUndefined();
  });
});

describe("attachDetails", () => {
  const truncated = (at: string) =>
    `[2026-10-04 ${at}] ALLOW | gemini   | ${LONG_KEY}... | approved by Gemini, took=0.8s`;

  test("80 字で切られたエントリに、詳細ログの全文と description を付ける", () => {
    const entries = parseLog(
      [
        truncated("20:00:00"),
        "[2026-10-04 20:00:01] ALLOW | pre      | ls | safe command",
      ].join("\n"),
    );

    const attached = attachDetails(entries, [
      { command: LONG_FULL, description: "履歴を見る" },
      { command: "ls" },
    ]);

    expect(attached[0]?.fullCommand).toBe(LONG_FULL);
    expect(attached[0]?.description).toBe("履歴を見る");
    expect(attached[1]?.fullCommand).toBeUndefined();
  });

  test("切られていないエントリには description だけを付ける", () => {
    const entries = parseLog(
      "[2026-10-04 20:00:01] ALLOW | pre      | ls | safe command",
    );

    const attached = attachDetails(entries, [
      { command: "ls", description: "一覧" },
    ]);

    expect(attached[0]?.fullCommand).toBeUndefined();
    expect(attached[0]?.description).toBe("一覧");
  });

  test("ちょうど 80 字のコマンドは、それで始まる長いコマンドと取り違えない", () => {
    const exact = LONG_KEY;
    const entries = parseLog(
      `[2026-10-04 20:00:01] ALLOW | gemini   | ${exact} | approved by Gemini, took=0.8s`,
    );

    const attached = attachDetails(entries, [
      { command: exact, description: "ちょうど 80 字" },
      { command: LONG_FULL, description: "長い方" },
    ]);

    expect(attached[0]?.description).toBe("ちょうど 80 字");
  });

  test("同じ先頭 80 字が並ぶときは新しい方から 1 対 1 で対応づける", () => {
    // 詳細ログ (直近 1000 件) はサマリー (直近 500 行) より古くまで残る。
    const entries = parseLog(
      [truncated("20:00:00"), truncated("20:00:05")].join("\n"),
    );

    const attached = attachDetails(entries, [
      { command: `${LONG_FULL} # older than the summary` },
      { command: `${LONG_FULL} # first` },
      { command: `${LONG_FULL} # second` },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([
      `${LONG_FULL} # first`,
      `${LONG_FULL} # second`,
    ]);
  });

  test("詳細ログが足りない (再起動で /tmp が消えた) 古いエントリは切れたまま", () => {
    const entries = parseLog(
      [truncated("20:00:00"), truncated("20:00:05")].join("\n"),
    );

    const attached = attachDetails(entries, [
      { command: `${LONG_FULL} # only one` },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([
      undefined,
      `${LONG_FULL} # only one`,
    ]);
  });
});

describe("toOneLine", () => {
  test("改行を ⏎ に置き換えて 1 行にする", () => {
    expect(toOneLine("python3 - <<EOF\nimport yaml")).toBe(
      "python3 - <<EOF ⏎ import yaml",
    );
  });
});
