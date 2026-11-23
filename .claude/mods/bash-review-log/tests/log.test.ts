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
  test("reason 末尾の detail= は detailLog に移し、reason からは外す", () => {
    const text =
      "[2026-11-21 10:00:00] ALLOW | pre      | ls | safe command detail=bash_cmd_17_42.log\n";

    expect(parseLog(text)).toEqual([
      {
        at: "2026-11-21 10:00:00",
        decision: "ALLOW",
        stage: "pre",
        command: "ls",
        reason: "safe command",
        detailLog: "bash_cmd_17_42.log",
      },
    ]);
  });

  test("reason の途中や形の違う detail= は名前として扱わない", () => {
    const text = [
      "[2026-11-21 10:00:00] ASK   | pre      | x | flag detail=bash_cmd_1_1.log in text",
      "[2026-11-21 10:00:01] ASK   | pre      | x | flag detail=../bash_cmd_1_1.log",
      "[2026-11-21 10:00:02] ASK   | pre      | x | flagdetail=bash_cmd_1_1.log",
    ].join("\n");

    const entries = parseLog(text);

    expect(entries.map((e) => e.detailLog)).toEqual([
      undefined,
      undefined,
      undefined,
    ]);
    expect(entries[0]?.reason).toBe("flag detail=bash_cmd_1_1.log in text");
  });

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

  test("改行が ⏎ で書かれた複数行コマンドは 1 行 1 エントリで、ヘッダ形の続きを別の判定と読まない", () => {
    // log_summary() はコマンドと理由の改行を ⏎ に置き換えて書く。以前は実改行の
    // まま書いていたので、2 行目がヘッダの形をしていると別の ALLOW として数えていた。
    const text =
      "[2026-11-11 10:00:01] ASK   | codex    | echo hi⏎[2026-11-11 10:00:00] ALLOW | pre      | x | gemini=ASK, codex=ASK, took=1.2s\n";

    expect(parseLog(text)).toEqual([
      {
        at: "2026-11-11 10:00:01",
        decision: "ASK",
        stage: "codex",
        command: "echo hi⏎[2026-11-11 10:00:00] ALLOW | pre      | x",
        reason: "gemini=ASK, codex=ASK, took=1.2s",
      },
    ]);
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

  // ペインの無い /bash-review-log ではこの文字列がモデルの読む本文になる。不可視の
  // 書式文字 (ゼロ幅・BOM・タグ文字) を残すと、ログ経由で見えない指示を埋め込める。
  // doc-browser の displayText (catalog.ts) と同じ文字クラスを置き換える。
  test("ゼロ幅文字・BOM・ソフトハイフン・タグ文字も置き換える (不可視の指示を残さない)", () => {
    // ZWJ (0x200d) と異体字セレクタ (0xfe0f) も置き換える: 絵文字の連結表示は崩れるが、
    // 見えない文字を残さないことを優先する (doc-browser と同じ割り切り)。
    const hidden = [
      0x200b, 0x200d, 0x2060, 0xfeff, 0x061c, 0x00ad, 0xfe0f, 0xe0041, 0xe007f,
    ];
    const input = `a${hidden.map((cp) => String.fromCodePoint(cp)).join("b")}z`;

    expect(displayText(input, 100)).toBe(
      `a${hidden.map(() => "�").join("b")}z`,
    );
  });

  test("書式文字を置き換えてもタブと改行と通常の日本語は残す", () => {
    expect(displayText("日本語\tタブ\n改行", 100)).toBe("日本語\tタブ\n改行");
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
  // サマリーの at はローカル時刻 (time.strftime) で、詳細ログの mtime は write_detail_log の
  // 直後 (数 ms 前) にある。テストの時刻もローカル時刻で組み、タイムゾーンに依らせない。
  const mtime = (clock: string) => new Date(`2026-10-04T${clock}`).getTime();
  const named = (at: string, name: string) => `${truncated(at)} detail=${name}`;

  test("detail= のある行は、その名前の詳細ログに対応づける (時刻が離れていても)", () => {
    const entries = parseLog(named("20:00:00", "bash_cmd_2_2.log"));

    const attached = attachDetails(entries, [
      {
        command: `${LONG_FULL} # close in time`,
        mtimeMs: mtime("20:00:00.3"),
        name: "bash_cmd_1_1.log",
      },
      {
        command: `${LONG_FULL} # named`,
        description: "名前で引いた",
        mtimeMs: mtime("23:00:00.0"),
        name: "bash_cmd_2_2.log",
      },
    ]);

    expect(attached[0]?.fullCommand).toBe(`${LONG_FULL} # named`);
    expect(attached[0]?.description).toBe("名前で引いた");
  });

  test("名前の詳細ログが読めなければ、時刻の合う別の詳細ログも付けない", () => {
    const entries = parseLog(named("20:00:00", "bash_cmd_9_9.log"));

    const attached = attachDetails(entries, [
      {
        command: `${LONG_FULL} # someone else`,
        mtimeMs: mtime("20:00:00.3"),
        name: "bash_cmd_1_1.log",
      },
    ]);

    expect(attached[0]?.fullCommand).toBeUndefined();
  });

  test("名前で引かれる詳細ログは、名前の無い旧い行に時刻で付けない", () => {
    const entries = parseLog(
      [truncated("20:00:00"), named("20:00:00", "bash_cmd_1_1.log")].join("\n"),
    );

    const attached = attachDetails(entries, [
      {
        command: `${LONG_FULL} # new`,
        mtimeMs: mtime("20:00:00.3"),
        name: "bash_cmd_1_1.log",
      },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([
      undefined,
      `${LONG_FULL} # new`,
    ]);
  });

  test("名前が合っても、コマンドの先頭が食い違う詳細ログは付けない", () => {
    const entries = parseLog(named("20:00:00", "bash_cmd_1_1.log"));

    const attached = attachDetails(entries, [
      {
        command: "rm -rf build",
        description: "別のコマンド",
        mtimeMs: mtime("20:00:00.3"),
        name: "bash_cmd_1_1.log",
      },
    ]);

    expect(attached[0]?.fullCommand).toBeUndefined();
    expect(attached[0]?.description).toBeUndefined();
  });

  test("80 字で切られたエントリに、詳細ログの全文と description を付ける", () => {
    const entries = parseLog(
      [
        truncated("20:00:00"),
        "[2026-10-04 20:00:01] ALLOW | pre      | ls | safe command",
      ].join("\n"),
    );

    const attached = attachDetails(entries, [
      {
        command: LONG_FULL,
        description: "履歴を見る",
        mtimeMs: mtime("20:00:00.3"),
      },
      { command: "ls", mtimeMs: mtime("20:00:01.3") },
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
      { command: "ls", description: "一覧", mtimeMs: mtime("20:00:01.3") },
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
      {
        command: exact,
        description: "ちょうど 80 字",
        mtimeMs: mtime("20:00:01.3"),
      },
      {
        command: LONG_FULL,
        description: "長い方",
        mtimeMs: mtime("20:00:01.3"),
      },
    ]);

    expect(attached[0]?.description).toBe("ちょうど 80 字");
  });

  test("同じ先頭 80 字が並ぶときは、書かれた時刻の合う詳細ログを対応づける", () => {
    // 詳細ログ (直近 1000 件) はサマリー (直近 500 行) より古くまで残る。
    const entries = parseLog(
      [truncated("20:00:00"), truncated("20:00:05")].join("\n"),
    );

    const attached = attachDetails(entries, [
      {
        command: `${LONG_FULL} # older than the summary`,
        mtimeMs: mtime("19:00:00.3"),
      },
      { command: `${LONG_FULL} # first`, mtimeMs: mtime("20:00:00.3") },
      { command: `${LONG_FULL} # second`, mtimeMs: mtime("20:00:05.3") },
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
      { command: `${LONG_FULL} # only one`, mtimeMs: mtime("20:00:05.3") },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([
      undefined,
      `${LONG_FULL} # only one`,
    ]);
  });

  test("新しい方の詳細ログが欠けても、古い方の全文を新しいエントリに付けない", () => {
    // 順序だけで新しい方から 1 対 1 に対応づけると、B の詳細ログが無いとき
    // B の行に A の全文が付いてしまう。
    const entries = parseLog(
      [truncated("20:00:00"), truncated("20:00:10")].join("\n"),
    );

    const attached = attachDetails(entries, [
      { command: `${LONG_FULL} # a`, mtimeMs: mtime("20:00:00.3") },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([
      `${LONG_FULL} # a`,
      undefined,
    ]);
  });

  test("書かれた時刻が合わない詳細ログは、キーが同じでも使わない", () => {
    const entries = parseLog(truncated("20:00:00"));

    const attached = attachDetails(entries, [
      { command: `${LONG_FULL} # an hour ago`, mtimeMs: mtime("19:00:00.3") },
    ]);

    expect(attached[0]?.fullCommand).toBeUndefined();
  });

  test("同じ秒に同じ先頭 80 字の別コマンドが重なるときは、どちらにも付けない", () => {
    // 並列に走った 2 本は、秒単位のサマリーからは順序でしか区別できない。
    const entries = parseLog(
      [truncated("20:00:00"), truncated("20:00:00")].join("\n"),
    );

    const attached = attachDetails(entries, [
      {
        command: `${LONG_FULL} # x`,
        description: "x",
        mtimeMs: mtime("20:00:00.1"),
      },
      {
        command: `${LONG_FULL} # y`,
        description: "y",
        mtimeMs: mtime("20:00:00.2"),
      },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([undefined, undefined]);
    expect(attached.map((e) => e.description)).toEqual([undefined, undefined]);
  });

  test("同じ秒に全文が同じ詳細ログだけが重なるなら、取り違えようがないので付ける", () => {
    const entries = parseLog(
      [truncated("20:00:00"), truncated("20:00:00")].join("\n"),
    );

    const attached = attachDetails(entries, [
      { command: LONG_FULL, mtimeMs: mtime("20:00:00.1") },
      { command: LONG_FULL, mtimeMs: mtime("20:00:00.2") },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([LONG_FULL, LONG_FULL]);
  });

  test("詳細ログが 1 本しかないのに同じ秒のエントリが 2 本あるときは付けない", () => {
    // もう 1 本は欠けていて、どちらの全文かは決められない。
    const entries = parseLog(
      [truncated("20:00:00"), truncated("20:00:00")].join("\n"),
    );

    const attached = attachDetails(entries, [
      { command: `${LONG_FULL} # only`, mtimeMs: mtime("20:00:00.1") },
    ]);

    expect(attached.map((e) => e.fullCommand)).toEqual([undefined, undefined]);
  });

  // サマリーは改行を ⏎ に置き換えて書かれ、詳細ログは実改行のまま残る。
  const SCRIPT = "python3 - <<'EOF'\nimport yaml\r\nEOF";
  const LONG_SCRIPT = `${"echo a\n".repeat(20)}echo end`;

  test("改行が ⏎ で書かれた複数行コマンドを、実改行の詳細ログと対応づける", () => {
    const entries = parseLog(
      `[2026-11-11 10:00:00] ASK   | highrisk | ${SCRIPT.replaceAll(/[\r\n]/g, "⏎")} | risk=stdin into python3`,
    );

    const attached = attachDetails(entries, [
      {
        command: SCRIPT,
        description: "YAML を読む",
        mtimeMs: new Date("2026-11-11T10:00:00.3").getTime(),
      },
    ]);

    expect(attached[0]?.description).toBe("YAML を読む");
    expect(attached[0]?.fullCommand).toBeUndefined();
  });

  test("切られた複数行コマンドは ⏎ 込みの先頭 80 字で対応づけ、全文は実改行のまま付ける", () => {
    const logged = Array.from(LONG_SCRIPT)
      .slice(0, 80)
      .join("")
      .replaceAll("\n", "⏎");
    const entries = parseLog(
      `[2026-11-11 10:00:00] ALLOW | gemini   | ${logged}... | approved by Gemini, took=0.8s`,
    );

    const attached = attachDetails(entries, [
      {
        command: LONG_SCRIPT,
        mtimeMs: new Date("2026-11-11T10:00:00.3").getTime(),
      },
    ]);

    expect(attached[0]?.fullCommand).toBe(LONG_SCRIPT);
  });

  test("以前の形式 (継続行から読んだ実改行入り) のエントリも対応づける", () => {
    const entries = parseLog(
      [
        "[2026-10-04 20:00:00] ASK   | highrisk | python3 - <<'EOF'",
        "import yaml\r",
        "EOF | risk=stdin into python3",
      ].join("\n"),
    );

    const attached = attachDetails(entries, [
      {
        command: SCRIPT,
        description: "YAML を読む",
        mtimeMs: mtime("20:00:00.3"),
      },
    ]);

    expect(attached[0]?.description).toBe("YAML を読む");
  });
});

describe("toOneLine", () => {
  test("改行を ⏎ に置き換えて 1 行にする", () => {
    expect(toOneLine("python3 - <<EOF\nimport yaml")).toBe(
      "python3 - <<EOF ⏎ import yaml",
    );
  });
});
