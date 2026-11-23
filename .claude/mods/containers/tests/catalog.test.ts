import { describe, expect, test } from "claude-code/testing";

import {
  MAX_ROWS,
  displayText,
  formatTime,
  hasBindMount,
  otherContainers,
  parseComposeLs,
  parseCsbStatus,
  parseDockerPs,
  processNotice,
  sandboxHeader,
  sandboxLabels,
  toTilde,
  withDockerFacts,
} from "../hooks/catalog";
import type { DockerContainer, ProcessOutcome } from "../hooks/catalog";
import type { SandboxRow } from "../types";

const HOME = "/home/tester";
const ID_A = "0123456789ab";
const ID_B = "abcdefabcdef";
const SHA = "a".repeat(64);

const pad = (n: number) => String(n).padStart(2, "0");

describe("displayText", () => {
  test("端末制御・書式文字 (ESC・bidi の書き換え・ゼロ幅) を U+FFFD にする", () => {
    expect(displayText("a\u001b[31mred\u202Ebidi\u200Bz", 80)).toBe(
      "a\uFFFD[31mred\uFFFDbidi\uFFFDz",
    );
  });

  test("上限を超えたらコードポイント単位で切り、末尾に … を付ける", () => {
    expect(displayText("あいうえお", 5)).toBe("あいうえお");
    expect(displayText("あいうえおか", 5)).toBe("あいうえ…");
    expect(displayText("😀😀😀😀😀😀", 4)).toBe("😀😀😀…");
  });
});

describe("toTilde", () => {
  test("ホームの下は ~ から始める。ホーム自身は ~", () => {
    expect(toTilde(`${HOME}/work/app`, HOME)).toBe("~/work/app");
    expect(toTilde(HOME, HOME)).toBe("~");
  });

  test("ディレクトリの境界で比べる (前方一致だけでは ~ にしない)", () => {
    expect(toTilde("/home/tester2/x", HOME)).toBe("/home/tester2/x");
    expect(toTilde("/home/testerx", HOME)).toBe("/home/testerx");
    expect(toTilde("/opt/app", HOME)).toBe("/opt/app");
  });

  test("ホームの末尾のスラッシュは無視し、ホームが空やルートなら何もしない", () => {
    expect(toTilde(`${HOME}/x`, `${HOME}/`)).toBe("~/x");
    expect(toTilde("/x", "")).toBe("/x");
    expect(toTilde("/x", "/")).toBe("/x");
  });
});

describe("hasBindMount", () => {
  test("docker ps の Mounts: ボリューム名は bind ではなく、ホストのパスは bind", () => {
    expect(hasBindMount("")).toBe(false);
    expect(hasBindMount("pgdata")).toBe(false);
    expect(hasBindMount("a".repeat(64))).toBe(false);
    expect(hasBindMount("pgdata,cache")).toBe(false);
    expect(hasBindMount("/Users/me/work")).toBe(true);
    expect(hasBindMount("pgdata,/Users/me/work")).toBe(true);
    expect(hasBindMount("/host/a,/host/b")).toBe(true);
    expect(hasBindMount("C:\\Users\\me")).toBe(true);
  });
});

describe("sandboxLabels", () => {
  const labels = [
    "com.docker.compose.project=x",
    "dev.claude.sandbox=1",
    `dev.claude.sandbox.id=${ID_A}`,
    "dev.claude.sandbox.role=verify",
    "dev.claude.sandbox.profile=3",
  ].join(",");

  test("正確なキーだけを引く", () => {
    expect(sandboxLabels(labels)).toEqual({
      isSandbox: true,
      id: ID_A,
      role: "verify",
    });
  });

  test("サンドボックスのラベルが無ければ isSandbox は false", () => {
    expect(sandboxLabels("a=b,c=d")).toEqual({
      isSandbox: false,
      id: null,
      role: null,
    });
    expect(sandboxLabels("")).toEqual({
      isSandbox: false,
      id: null,
      role: null,
    });
  });

  test("= の無い断片 (compose の config_files の 2 つ目以降) は読み飛ばす", () => {
    const parsed = sandboxLabels(
      "com.docker.compose.project.config_files=/a/x.yml,/a/y.yml,dev.claude.sandbox=1",
    );
    expect(parsed.isSandbox).toBe(true);
  });

  test("キーは完全一致: 接頭辞を足した別のキーや、値に含まれる文字列では成立しない", () => {
    expect(sandboxLabels("evil.dev.claude.sandbox=1").isSandbox).toBe(false);
    expect(sandboxLabels("x=dev.claude.sandbox=1").isSandbox).toBe(false);
    expect(sandboxLabels("dev.claude.sandbox=0").isSandbox).toBe(false);
  });

  test("id と role は形式が合うときだけ採る", () => {
    const parsed = sandboxLabels(
      "dev.claude.sandbox=1,dev.claude.sandbox.id=../x,dev.claude.sandbox.role=Work Now",
    );
    expect(parsed).toEqual({ isSandbox: true, id: null, role: null });
  });
});

function psLine(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    ID: "c0ffee000001",
    Names: "web",
    Image: "nginx:1.27",
    State: "running",
    Status: "Up 2 hours",
    Mounts: "",
    Labels: "",
    Command: '"nginx -g daemon off"',
    ...extra,
  });
}

describe("parseDockerPs", () => {
  test("JSON Lines を読み、必要な項目だけを持つ", () => {
    const stdout = [
      psLine(),
      psLine({
        ID: "c0ffee000002",
        Names: `csb-${ID_A}`,
        Image: "postgres:16",
        State: "exited",
        Status: "Exited (0) 4 weeks ago",
        Mounts: "pgdata,/Users/me/data",
        Labels: `dev.claude.sandbox=1,dev.claude.sandbox.id=${ID_A},dev.claude.sandbox.role=work`,
      }),
      "",
    ].join("\n");

    expect(parseDockerPs(stdout, false)).toEqual([
      {
        id: "c0ffee000001",
        name: "web",
        image: "nginx:1.27",
        state: "running",
        status: "Up 2 hours",
        hasBind: false,
        isSandboxLabeled: false,
        sandboxId: null,
        sandboxRole: null,
      },
      {
        id: "c0ffee000002",
        name: `csb-${ID_A}`,
        image: "postgres:16",
        state: "exited",
        status: "Exited (0) 4 weeks ago",
        hasBind: true,
        isSandboxLabeled: true,
        sandboxId: ID_A,
        sandboxRole: "work",
      },
    ]);
  });

  test("Command・Labels・Env など、見せない項目は結果に残らない", () => {
    const parsed = parseDockerPs(
      psLine({
        Command: '"run --token=s3cr3t"',
        Env: ["API_KEY=s3cr3t"],
        Labels:
          "com.docker.compose.project.working_dir=/home/tester/secret-dir",
      }),
      false,
    );

    const dumped = JSON.stringify(parsed);
    expect(dumped).not.toContain("s3cr3t");
    expect(dumped).not.toContain("secret-dir");
  });

  test("空の出力は 0 件。壊れた行は読み飛ばし、1 行も読めなければ null", () => {
    expect(parseDockerPs("", false)).toEqual([]);
    expect(parseDockerPs("\n\n", false)).toEqual([]);
    expect(parseDockerPs(`not json\n${psLine()}\n`, false)).toHaveLength(1);
    expect(parseDockerPs("not json\n[1]\n", false)).toBeNull();
  });

  test("名前の無い行は読み飛ばす", () => {
    expect(parseDockerPs(psLine({ Names: "" }), false)).toEqual([]);
    expect(parseDockerPs(psLine({ Names: 5 }), false)).toEqual([]);
  });

  test("出力が上限で切れたら、最後の改行より後ろ (途中で切れた行) は捨てる", () => {
    const whole = psLine({ ID: "c0ffee000009", Names: "cut" });
    const cut = `${psLine()}\n${whole.slice(0, whole.length - 10)}`;

    const parsed = parseDockerPs(cut, true);
    expect(parsed?.map((c) => c.name)).toEqual(["web"]);
    // 切れていなければ、末尾に改行が無くても最後の行まで読む
    expect(parseDockerPs(`${psLine()}\n${whole}`, false)).toHaveLength(2);
  });

  test("表示する文字列は無害化する", () => {
    const parsed = parseDockerPs(
      psLine({ Names: "we\u001b[2Jb\u202E", Status: "Up\nUp" }),
      false,
    );
    expect(parsed?.[0]?.name).toBe("we\uFFFD[2Jb\uFFFD");
    expect(parsed?.[0]?.status).toBe("Up\uFFFDUp");
  });

  // \u30E9\u30D9\u30EB\u306F\u8AB0\u3067\u3082\u4ED8\u3051\u3089\u308C\u308B (compose \u306E\u5225\u306E\u30E9\u30D9\u30EB\u306E\u5024\u306B\u30AB\u30F3\u30DE\u3067\u57CB\u3081\u8FBC\u3080\u3053\u3068\u3082\u3067\u304D\u308B)\u3002
  // \u30B5\u30F3\u30C9\u30DC\u30C3\u30AF\u30B9\u306E\u30B3\u30F3\u30C6\u30CA\u3068\u8A8D\u3081\u308B\u306E\u306F\u3001csb \u304C\u4ED8\u3051\u308B\u540D\u524D\u306E ID \u304C\u30E9\u30D9\u30EB\u306E ID \u3068\u5408\u3046\u3068\u304D\u3060\u3051\u3002
  test("\u30B5\u30F3\u30C9\u30DC\u30C3\u30AF\u30B9\u3068\u8A8D\u3081\u308B\u306E\u306F\u3001\u30E9\u30D9\u30EB\u306E ID \u3068 csb \u306E\u547D\u540D (csb-<ID>\u30FB-setup\u30FB-verify) \u306E ID \u304C\u5408\u3046\u3068\u304D\u3060\u3051", () => {
    const labels = (id: string) =>
      `dev.claude.sandbox=1,dev.claude.sandbox.id=${id},dev.claude.sandbox.role=work`;
    const named = (names: string, id: string) =>
      psLine({ ID: names, Names: names, Labels: labels(id) });
    const stdout = [
      named(`csb-${ID_A}`, ID_A),
      named(`csb-${ID_A}-setup`, ID_A),
      named(`csb-${ID_A}-verify`, ID_A),
      // \u540D\u524D\u304C csb \u306E\u3082\u306E\u3067\u306F\u306A\u3044 (\u30E9\u30D9\u30EB\u3060\u3051\u3092\u4F3C\u305B\u305F)
      named("evil", ID_A),
      // \u540D\u524D\u306E ID \u3068\u30E9\u30D9\u30EB\u306E ID \u304C\u9055\u3046
      named(`csb-${ID_B}`, ID_A),
      // \u540D\u524D\u306E\u524D\u5F8C\u306B\u4F59\u8A08\u306A\u6587\u5B57\u30FBcsb \u304C\u4ED8\u3051\u306A\u3044\u63A5\u5C3E\u8F9E\u30FB16 \u9032\u3067\u306A\u3044 ID
      named(`xcsb-${ID_A}`, ID_A),
      named(`csb-${ID_A}-evil`, ID_A),
      named(`csb-${ID_A}-`, ID_A),
      named(`csb-${ID_A.toUpperCase()}`, ID_A),
      // \u5225\u306E\u30E9\u30D9\u30EB\u306E\u5024\u306B\u30AB\u30F3\u30DE\u3067\u57CB\u3081\u8FBC\u3093\u3060
      psLine({
        ID: "embedded",
        Names: "web",
        Labels: `com.example.note=hi,${labels(ID_A)}`,
      }),
    ].join("\n");

    const parsed = parseDockerPs(stdout, false) ?? [];

    expect(parsed.map((one) => [one.name, one.sandboxId])).toEqual([
      [`csb-${ID_A}`, ID_A],
      [`csb-${ID_A}-setup`, ID_A],
      [`csb-${ID_A}-verify`, ID_A],
      ["evil", null],
      [`csb-${ID_B}`, null],
      [`xcsb-${ID_A}`, null],
      [`csb-${ID_A}-evil`, null],
      [`csb-${ID_A}-`, null],
      [`csb-${ID_A.toUpperCase()}`, null],
      ["web", null],
    ]);
    // \u30E9\u30D9\u30EB\u306E\u5370\u306F\u3001\u30B5\u30F3\u30C9\u30DC\u30C3\u30AF\u30B9\u3068\u8A8D\u3081\u306A\u304B\u3063\u305F\u3082\u306E\u306B\u3082\u6B8B\u308B ([csb \u30E9\u30D9\u30EB])
    expect(parsed.every((one) => one.isSandboxLabeled)).toBe(true);
    // \u5F79\u5272\u3082\u3001\u30B5\u30F3\u30C9\u30DC\u30C3\u30AF\u30B9\u3068\u8A8D\u3081\u305F\u3082\u306E\u3060\u3051\u304B\u3089\u63A1\u308B
    expect(parsed.map((one) => one.sandboxRole)).toEqual([
      "work",
      "work",
      "work",
      ...Array<null>(7).fill(null),
    ]);
  });
});

const CSB = JSON.stringify([
  {
    id: ID_A,
    created_at: 1790000000,
    repo_root: `${HOME}/work/app`,
    image: { source: "generic", ref: "csb-generic:1", id: `sha256:${SHA}` },
    net_on_until: 1790003600,
    container: "running",
    check: "ok",
    patches: [
      {
        sha256: SHA,
        files: 3,
        added: 10,
        removed: 2,
        verify: "PASS",
        reviews: {
          "code-reviewer": "pass",
          codex: "missing",
          advisor: "stale",
        },
      },
      { sha256: "b".repeat(64), error: "パッチが壊れている" },
    ],
  },
  {
    id: ID_B,
    created_at: 1790000100,
    repo_root: "/opt/other",
    image: { source: "generic", ref: "csb-generic:1", id: `sha256:${SHA}` },
    net_on_until: null,
    container: "exited",
    check: `照合に失敗: ${HOME}/x が違う`,
    patches: [],
  },
  { id: "fedcbafedcba", error: "meta.json を読めない: schema が不正" },
]);

describe("parseCsbStatus", () => {
  test("csb status --json の行を表示用の行にする", () => {
    const rows = parseCsbStatus(CSB, HOME);

    expect(rows).toEqual([
      {
        id: ID_A,
        broken: null,
        roles: null,
        state: "running",
        isRunning: true,
        netUntilMs: 1790003600 * 1000,
        net: "on",
        repo: "~/work/app",
        check: null,
        hasBind: null,
        patches: [
          {
            sha: "aaaaaaa",
            error: null,
            files: 3,
            added: 10,
            removed: 2,
            verify: "PASS",
            reviews: ["code-reviewer=pass", "codex=missing", "advisor=stale"],
          },
          {
            sha: "bbbbbbb",
            error: "パッチが壊れている",
            files: 0,
            added: 0,
            removed: 0,
            verify: "?",
            reviews: [],
          },
        ],
      },
      {
        id: ID_B,
        broken: null,
        roles: null,
        state: "exited",
        isRunning: false,
        netUntilMs: null,
        net: "off",
        repo: "/opt/other",
        check: "照合に失敗: ~/x が違う",
        hasBind: null,
        patches: [],
      },
      {
        id: "fedcbafedcba",
        broken: "meta.json を読めない: schema が不正",
        roles: null,
        state: "?",
        isRunning: false,
        netUntilMs: null,
        net: "unknown",
        repo: "",
        check: null,
        hasBind: null,
        patches: [],
      },
    ]);
  });

  test("配列でない・JSON でない出力は null。空の配列は 0 件", () => {
    expect(parseCsbStatus("[]", HOME)).toEqual([]);
    expect(parseCsbStatus("", HOME)).toBeNull();
    expect(parseCsbStatus("not json", HOME)).toBeNull();
    expect(parseCsbStatus('{"id":"x"}', HOME)).toBeNull();
  });

  test("ID の形式が違う行や、オブジェクトでない要素は読み飛ばす", () => {
    const rows = parseCsbStatus(
      JSON.stringify([
        { id: "../x", container: "running" },
        42,
        null,
        { id: ID_A, error: "x" },
      ]),
      HOME,
    );
    expect(rows?.map((row) => row.id)).toEqual([ID_A]);
  });

  test("net_connected があれば実際の接続で表示する (期限切れでも繋がっていれば切断と出さない)", () => {
    const at = (extra: Record<string, unknown>) => ({
      id: ID_A,
      repo_root: "/r",
      container: "running",
      check: "ok",
      patches: [],
      ...extra,
    });
    const rows = parseCsbStatus(
      JSON.stringify([
        at({
          net_on_until: 1790003600,
          net_connected: true,
          net_expired: false,
        }),
        at({ net_on_until: null, net_connected: true, net_expired: true }),
        at({ net_on_until: null, net_connected: true, net_expired: false }),
        at({
          net_on_until: 1790003600,
          net_connected: false,
          net_expired: false,
        }),
        at({ net_on_until: null, net_connected: null, net_expired: true }),
      ]),
      HOME,
    );
    expect(rows?.map((row) => row.net)).toEqual([
      "on",
      "expired",
      "nowindow",
      "off",
      "unknown",
    ]);
    // 「ネット: …」の後ろに続く印 ([bind mount 不明] など) は含めない
    const nets = (rows ?? []).map(
      (row) => sandboxHeader(row).split("ネット: ")[1]?.split("  ")[0],
    );
    expect(nets).toEqual([
      `接続中 (${formatTime(1790003600 * 1000)} まで)`,
      "接続中 (期限切れ。次の csb の呼び出しで切断)",
      "接続中 (期限の記録なし)",
      "切断",
      "不明",
    ]);
  });

  test("net_connected が無い古い出力は net_on_until で決める", () => {
    const rows = parseCsbStatus(
      JSON.stringify([
        {
          id: ID_A,
          repo_root: "/r",
          container: "running",
          check: "ok",
          net_on_until: 1790003600,
        },
        {
          id: ID_B,
          repo_root: "/r",
          container: "running",
          check: "ok",
          net_on_until: null,
        },
      ]),
      HOME,
    );
    expect(rows?.map((row) => row.net)).toEqual(["on", "off"]);
  });

  test("net_on_until が数値でなければ切断として扱い、sha の形式が違えば ? にする", () => {
    const rows = parseCsbStatus(
      JSON.stringify([
        {
          id: ID_A,
          repo_root: "/r",
          net_on_until: "soon",
          container: "running",
          check: "ok",
          patches: [{ sha256: "zzz", verify: "PASS", reviews: {} }],
        },
      ]),
      HOME,
    );
    expect(rows?.[0]?.netUntilMs).toBeNull();
    expect(rows?.[0]?.patches[0]?.sha).toBe("?");
  });

  test("表示する文字列は無害化する", () => {
    const rows = parseCsbStatus(
      JSON.stringify([
        {
          id: ID_A,
          repo_root: "/r\u001b[2J",
          container: "run\u202Ening",
          check: "ok",
          patches: [],
        },
      ]),
      HOME,
    );
    expect(rows?.[0]?.repo).toBe("/r\uFFFD[2J");
    expect(rows?.[0]?.state).toBe("run\uFFFDning");
    expect(rows?.[0]?.isRunning).toBe(false);
  });
});

describe("parseComposeLs", () => {
  test("docker compose ls --format json を読み、設定ファイルは ~ 相対で最初の 1 つと残りの数", () => {
    const stdout = JSON.stringify([
      {
        Name: "app",
        Status: "running(2)",
        ConfigFiles: `${HOME}/work/app/compose.yml,${HOME}/work/app/override.yml`,
      },
      { Name: "data", Status: "exited(1)", ConfigFiles: "" },
    ]);

    expect(parseComposeLs(stdout, HOME)).toEqual([
      {
        name: "app",
        status: "running(2)",
        file: "~/work/app/compose.yml",
        moreFiles: 1,
      },
      { name: "data", status: "exited(1)", file: "", moreFiles: 0 },
    ]);
  });

  test("空の配列は 0 件。JSON でない・配列でない出力は null", () => {
    expect(parseComposeLs("[]", HOME)).toEqual([]);
    expect(parseComposeLs("", HOME)).toBeNull();
    expect(parseComposeLs("nope", HOME)).toBeNull();
    expect(parseComposeLs("{}", HOME)).toBeNull();
  });

  test("名前の無い要素は読み飛ばし、文字列は無害化する", () => {
    const rows = parseComposeLs(
      JSON.stringify([{ Status: "x" }, { Name: "a\u001bb", Status: "ok" }]),
      HOME,
    );
    expect(rows?.map((row) => row.name)).toEqual(["a\uFFFDb"]);
  });
});

const container = (extra: Partial<DockerContainer> = {}): DockerContainer => ({
  id: "c0ffee000001",
  name: "web",
  image: "nginx",
  state: "running",
  status: "Up",
  hasBind: false,
  isSandboxLabeled: false,
  sandboxId: null,
  sandboxRole: null,
  ...extra,
});

// csb status から作った直後の行: 役割と bind mount は docker 側の情報なので、まだ不明。
const sandboxRow = (id: string): SandboxRow => ({
  id,
  broken: null,
  roles: null,
  state: "running",
  isRunning: true,
  netUntilMs: null,
  net: "off",
  repo: "~/r",
  check: null,
  hasBind: null,
  patches: [],
});

describe("sandboxHeader", () => {
  const header = (extra: Partial<SandboxRow>) =>
    sandboxHeader({ ...sandboxRow(ID_A), ...extra });

  test("役割・bind mount: 取れていれば値 (無ければ - と印なし)、取れていなければ「不明」", () => {
    expect(header({ roles: ["verify", "work"], hasBind: false })).toBe(
      `${ID_A}  running  役割: verify, work  ネット: 切断`,
    );
    expect(header({ roles: ["work"], hasBind: true })).toBe(
      `${ID_A}  running  役割: work  ネット: 切断  [bind mount あり]`,
    );
    // docker ps を最後まで読めて、見つからなかった
    expect(header({ roles: [], hasBind: false })).toBe(
      `${ID_A}  running  役割: -  ネット: 切断`,
    );
    // 取れていない: 「無し」に見える表示にしない
    expect(header({ roles: null, hasBind: null })).toBe(
      `${ID_A}  running  役割: 不明  ネット: 切断  [bind mount 不明]`,
    );
    expect(header({ roles: ["work"], hasBind: null })).toContain(
      "[bind mount 不明]",
    );
  });
});

describe("withDockerFacts", () => {
  test("ラベルのサンドボックス ID ごとに役割 (重複なし・並べ替え済み) と bind mount の有無を足す", () => {
    const rows = withDockerFacts(
      [sandboxRow(ID_A), sandboxRow(ID_B)],
      [
        container({
          sandboxId: ID_A,
          sandboxRole: "work",
          isSandboxLabeled: true,
        }),
        container({
          sandboxId: ID_A,
          sandboxRole: "verify",
          isSandboxLabeled: true,
        }),
        container({
          sandboxId: ID_A,
          sandboxRole: "work",
          isSandboxLabeled: true,
        }),
        container({
          sandboxId: ID_B,
          sandboxRole: "work",
          isSandboxLabeled: true,
          hasBind: true,
        }),
        container({ sandboxId: null, sandboxRole: "work" }),
        container({
          sandboxId: "ffffffffffff",
          sandboxRole: "setup",
          isSandboxLabeled: true,
        }),
      ],
      true,
    );

    expect(rows[0]?.roles).toEqual(["verify", "work"]);
    expect(rows[0]?.hasBind).toBe(false);
    expect(rows[1]?.roles).toEqual(["work"]);
    expect(rows[1]?.hasBind).toBe(true);
  });

  test("docker ps を最後まで読めて、そのサンドボックスのコンテナが無ければ、役割は無し・bind mount も無し", () => {
    const [row] = withDockerFacts([sandboxRow(ID_A)], [container()], true);

    expect(row?.roles).toEqual([]);
    expect(row?.hasBind).toBe(false);
  });

  test("docker ps を読めていなければ (null)、役割も bind mount も不明のまま", () => {
    const [row] = withDockerFacts([sandboxRow(ID_A)], null, false);

    expect(row).toEqual(sandboxRow(ID_A));
    expect(row?.roles).toBeNull();
    expect(row?.hasBind).toBeNull();
  });

  test("出力が途中で切れていたら、役割は不明。bind mount は、見えたものだけ「あり」で、残りは不明", () => {
    const rows = withDockerFacts(
      [sandboxRow(ID_A), sandboxRow(ID_B), sandboxRow("ccccccccccc0")],
      [
        container({
          sandboxId: ID_A,
          sandboxRole: "work",
          isSandboxLabeled: true,
        }),
        container({
          sandboxId: ID_B,
          sandboxRole: "work",
          isSandboxLabeled: true,
          hasBind: true,
        }),
      ],
      false,
    );

    expect(rows.map((row) => [row.roles, row.hasBind])).toEqual([
      [null, null],
      [null, true],
      [null, null],
    ]);
  });
});

describe("otherContainers", () => {
  test("csb の状態と結びつくコンテナは除き、ラベルだけのコンテナは ラベル付きで残す", () => {
    const section = otherContainers(
      [
        container({ id: "1", name: "web" }),
        container({
          id: "2",
          name: "csb-a",
          sandboxId: ID_A,
          sandboxRole: "work",
          isSandboxLabeled: true,
        }),
        container({
          id: "3",
          name: "csb-orphan",
          sandboxId: "ffffffffffff",
          sandboxRole: "work",
          isSandboxLabeled: true,
        }),
        container({ id: "4", name: "db", state: "exited", hasBind: true }),
      ],
      new Set([ID_A]),
    );

    expect(section).toEqual({
      rows: [
        {
          id: "1",
          name: "web",
          image: "nginx",
          status: "Up",
          isRunning: true,
          hasBind: false,
          isSandboxLabeled: false,
        },
        {
          id: "3",
          name: "csb-orphan",
          image: "nginx",
          status: "Up",
          isRunning: true,
          hasBind: false,
          isSandboxLabeled: true,
        },
        {
          id: "4",
          name: "db",
          image: "nginx",
          status: "Up",
          isRunning: false,
          hasBind: true,
          isSandboxLabeled: false,
        },
      ],
      notice: null,
      hidden: 0,
    });
  });

  test("上限 (MAX_ROWS) を超えた分は件数だけ hidden に入れる", () => {
    const many = Array.from({ length: MAX_ROWS + 5 }, (_, i) =>
      container({ id: String(i), name: `c${i}` }),
    );

    const section = otherContainers(many, new Set());

    expect(section.rows).toHaveLength(MAX_ROWS);
    expect(section.rows[0]?.name).toBe("c0");
    expect(section.hidden).toBe(5);
  });
});

const exited = (
  exitCode: number,
  stderr = "",
  stdout = "",
): ProcessOutcome => ({
  status: "fulfilled",
  value: { exitCode, stdout, stderr, isStdoutTruncated: false },
});

describe("processNotice", () => {
  test("成功 (exit 0) なら null", () => {
    expect(processNotice("docker ps", exited(0))).toBeNull();
  });

  test("起動できない・時間切れ (reject) は、そのコマンドの 1 行の知らせ", () => {
    const outcome: ProcessOutcome = {
      status: "rejected",
      reason: new Error("spawn docker ENOENT"),
    };
    expect(processNotice("docker ps", outcome)).toBe(
      "docker ps を実行できませんでした (未導入か、5 秒以内に終わりませんでした)",
    );
  });

  test("デーモンに繋がらない失敗は、stderr を出さずに固定の 1 行にする", () => {
    const notice = processNotice(
      "docker ps",
      exited(
        1,
        "Cannot connect to the Docker daemon at unix:///home/tester/.docker/run/docker.sock. Is the docker daemon running?",
      ),
    );
    expect(notice).toBe(
      "Docker デーモンに接続できません (Docker が起動していますか)",
    );
  });

  test("ソケットの権限が無い失敗は、デーモンが落ちているとは別の 1 行にする (stderr は出さない)", () => {
    const denied = [
      // 旧来の文言 (Got … と、dial unix … の末尾)
      'Got permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock: Get "http://%2Fvar%2Frun%2Fdocker.sock/v1.24/containers/json": dial unix /var/run/docker.sock: connect: permission denied',
      // 新しい文言 (… the docker API at …)
      "permission denied while trying to connect to the docker API at unix:///home/tester/.docker/run/docker.sock",
    ];
    for (const stderr of denied) {
      const notice = processNotice("docker ps", exited(1, stderr));

      expect(notice).toBe(
        "Docker のソケットを使う権限がありません (実行ユーザーの権限を確かめてください)",
      );
      expect(notice).not.toContain("docker.sock");
    }
  });

  test("権限の失敗の判定は docker の文脈に限る (csb の権限エラーを Docker の知らせにしない)", () => {
    expect(
      processNotice(
        "csb status",
        exited(1, "PermissionError: permission denied"),
      ),
    ).toBe("csb status が失敗しました (exit 1)");
    expect(
      processNotice(
        "docker ps",
        exited(1, "open /home/tester/x: permission denied"),
      ),
    ).toBe("docker ps が失敗しました (exit 1)");
  });

  test("docker compose が使えない失敗", () => {
    expect(
      processNotice(
        "docker compose ls",
        exited(125, "docker: 'compose' is not a docker command."),
      ),
    ).toBe("docker compose が使えません (プラグインが入っていません)");
  });

  test("それ以外の失敗は終了コードだけを出す (stderr は出さない)", () => {
    const notice = processNotice(
      "csb status",
      exited(2, "boom /home/tester/secret-dir"),
    );
    expect(notice).toBe("csb status が失敗しました (exit 2)");
    expect(notice).not.toContain("secret-dir");
  });
});

describe("formatTime", () => {
  test("ローカル時刻の HH:MM (秒を付けると HH:MM:SS)", () => {
    const ms = new Date(2026, 10, 21, 7, 5, 9).getTime();

    expect(formatTime(ms)).toBe(`${pad(7)}:${pad(5)}`);
    expect(formatTime(ms, true)).toBe(`${pad(7)}:${pad(5)}:${pad(9)}`);
  });
});
