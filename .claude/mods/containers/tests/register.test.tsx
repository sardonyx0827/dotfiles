import { describe, expect, mock, test } from "claude-code/testing";
import type { Engine } from "claude-code/testing";
import type { On } from "claude-code";

const HOME = "/home/tester";
// install.sh が書く固定のラッパー。信頼できるインタプリタを -I -S で起動する 3 行の
// シェルスクリプトで、mod はこれを絶対パスで呼ぶ (3 つとも cwd は HOME)。
const CSB = `${HOME}/.local/bin/csb`;
const CSB_ARGV = [CSB, "status", "--json"];
// csb を走らせなかったときの知らせの先頭。先頭は共通で、続きは原因ごとに分かれる (ラッパーが
// 無い・通常のファイルでないとき / 下の NOTICE_HOME の HOME のとき)
const NOTICE_CSB = "csb が使えません";
// 原因が HOME (取れない・絶対パスのディレクトリでない) のときの続き。ラッパーのせいにして
// install.sh を勧めない。HOME の値は載せない
const NOTICE_HOME = "HOME が絶対パスのディレクトリでない";
const PS_ARGV = ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"];
const COMPOSE_ARGV = ["docker", "compose", "ls", "--format", "json"];
const ALLOWED = [CSB_ARGV, PS_ARGV, COMPOSE_ARGV];

const ID_A = "0123456789ab";
const ID_B = "abcdefabcdef";
const ID_BROKEN = "fedcbafedcba";
const ID_ORPHAN = "ffffffffffff";
const SHA = "a".repeat(64);
const NET_UNTIL = 1790003600;

const pad = (n: number) => String(n).padStart(2, "0");
// ローカル時刻で組み立てるので、実行するマシンのタイムゾーンに依らない。
const hhmm = (epochSeconds: number) => {
  const date = new Date(epochSeconds * 1000);

  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

type Out = {
  exitCode?: number;
  stdout?: string;
  stderr?: string;
  // reject (起動できない・時間切れ) する
  deny?: string;
  truncated?: boolean;
};

const CSB_STATUS = JSON.stringify([
  {
    id: ID_A,
    created_at: 1790000000,
    repo_root: `${HOME}/work/app`,
    image: { source: "generic", ref: "csb-generic:1", id: `sha256:${SHA}` },
    net_on_until: NET_UNTIL,
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
          advisor: "missing",
        },
      },
    ],
  },
  {
    id: ID_B,
    created_at: 1790000100,
    repo_root: "/opt/other",
    image: { source: "generic", ref: "csb-generic:1", id: `sha256:${SHA}` },
    net_on_until: null,
    container: "exited",
    check: "ok",
    patches: [],
  },
  { id: ID_BROKEN, error: "meta.json を読めない: schema が不正" },
]);

const sandboxLabels = (id: string, role: string) =>
  `dev.claude.sandbox=1,dev.claude.sandbox.id=${id},dev.claude.sandbox.role=${role},dev.claude.sandbox.profile=3`;

function ps(extra: Record<string, unknown>): string {
  return JSON.stringify({
    ID: "c0ffee000000",
    Names: "x",
    Image: "img",
    State: "running",
    Status: "Up",
    Mounts: "",
    Labels: "",
    // 見せてはいけない項目
    Command: '"run --token=s3cr3t"',
    Env: ["API_KEY=s3cr3t"],
    ...extra,
  });
}

const DOCKER_PS = [
  ps({
    ID: "aaaa00000001",
    Names: `csb-${ID_A}`,
    Image: "csb-generic:1",
    Status: "Up 3 minutes",
    Labels: sandboxLabels(ID_A, "work"),
  }),
  ps({
    ID: "aaaa00000002",
    Names: `csb-${ID_A}-verify`,
    Image: "csb-generic:1",
    State: "exited",
    Status: "Exited (0) 1 minute ago",
    Labels: sandboxLabels(ID_A, "verify"),
  }),
  ps({
    ID: "bbbb00000001",
    Names: `csb-${ID_B}`,
    Image: "csb-generic:1",
    State: "exited",
    Status: "Exited (0) 2 hours ago",
    Mounts: `${HOME}/work/other`,
    Labels: sandboxLabels(ID_B, "work"),
  }),
  ps({
    ID: "c0ffee000001",
    Names: "web",
    Image: "nginx:1.27",
    Status: "Up 2 hours",
    Mounts: `pgdata,${HOME}/site`,
    Labels: `com.docker.compose.project.working_dir=${HOME}/secret-dir`,
  }),
  ps({
    ID: "c0ffee000002",
    Names: "db",
    Image: "postgres:16",
    State: "exited",
    Status: "Exited (0) 4 weeks ago",
    Mounts: "pgdata",
  }),
  ps({
    ID: "dddd00000001",
    Names: `csb-${ID_ORPHAN}`,
    Image: "csb-generic:1",
    Status: "Up 1 hour",
    Labels: sandboxLabels(ID_ORPHAN, "work"),
  }),
].join("\n");

const COMPOSE_LS = JSON.stringify([
  {
    Name: "app",
    Status: "running(2)",
    ConfigFiles: `${HOME}/work/app/compose.yml`,
  },
  {
    Name: "data",
    Status: "exited(1)",
    ConfigFiles: `${HOME}/data/docker-compose.yml,${HOME}/data/override.yml`,
  },
]);

const SUMMARY =
  "containers: サンドボックス 3 件 · その他のコンテナ 3 件 · compose 2 件";

const PANE_PROPS = {
  title: "containers",
  isFocused: true,
  bodyColumns: 120,
  placement: "dock" as const,
  scroll: { offset: 0, bodyRows: 40 },
  view: {},
};

// テストの on は mod の下 (エンジンの位置) に座るので、mod が $ で呼ぶものに答える。
function world(
  on: On,
  options: {
    csb?: Out;
    ps?: Out;
    compose?: Out;
    // csb のラッパー (~/.local/bin/csb) が無い
    csbMissing?: boolean;
    // ラッパーの場所の種類 (既定は file)。dir は「ラッパーではない」
    csbKind?: "file" | "dir";
    // ラッパーの場所自体が symlink (kind は、辿った先の種類)。古い install が残した
    // ~/.local/bin/csb -> claude_sandbox.py のような、install.sh が書くのとは別物
    csbIsLink?: boolean;
    isPlaced?: boolean;
    // session.start のとき、ペインが開いたままになっている
    panesOpen?: boolean;
    // 環境変数 CLAUDE_CONFIG_DIR。mod は見ない (見ていないことを確かめる)
    configDir?: string;
    // 環境変数 HOME。null は未設定、既定は HOME
    home?: string | null;
    // HOME (絶対パス) の場所の種類。既定は dir。file は「ディレクトリではない」、
    // missing は stat が ENOENT で失敗する (HOME が実在しない)
    homeKind?: "dir" | "file" | "missing";
  } = {},
) {
  mock.env(on, {
    ...(options.home === null ? {} : { HOME: options.home ?? HOME }),
    ...(options.configDir === undefined
      ? {}
      : { CLAUDE_CONFIG_DIR: options.configDir }),
  });
  const clock = mock.clock(on);
  const out = {
    csb: options.csb ?? { stdout: CSB_STATUS },
    ps: options.ps ?? { stdout: DOCKER_PS },
    compose: options.compose ?? { stdout: COMPOSE_LS },
  };
  // 立てると、プロセスの答えは release() まで返らない。
  let release: () => void = () => {};
  let gate: Promise<void> | undefined;
  const hold = () => {
    gate = new Promise<void>((resolve) => {
      release = resolve;
    });
  };
  const runs: {
    argv: string[];
    timeoutMs: number | undefined;
    cwd: string | undefined;
  }[] = [];
  const opened: string[] = [];
  const closed: string[] = [];
  const registered: string[] = [];
  // mod が csb の存在を確かめる場所 (ラッパーだけ。CLAUDE_CONFIG_DIR では動かない)
  const stats: string[] = [];

  on("session.start", (_, e) => ({ cwd: e.cwd }));
  on("process.run", async ($, e) => {
    const argv = [...e.argv];
    runs.push({ argv, timeoutMs: e.init?.timeoutMs, cwd: e.init?.cwd });
    const pick =
      argv[0] === CSB
        ? out.csb
        : argv[1] === "ps"
          ? out.ps
          : argv[1] === "compose"
            ? out.compose
            : undefined;
    if (pick === undefined) return { deny: "unexpected command" };
    if (gate !== undefined) await gate;
    if (pick.deny !== undefined) return { deny: pick.deny };

    return {
      value: {
        exitCode: pick.exitCode ?? 0,
        stdout: pick.stdout ?? "",
        stderr: pick.stderr ?? "",
        isStdoutTruncated: pick.truncated ?? false,
        isStderrTruncated: false,
      },
    };
  });
  const home = options.home === undefined ? HOME : options.home;
  on("fs.stat", ($, e) => {
    stats.push(e.path);

    if (home !== null && e.path === home) {
      return options.homeKind === "missing"
        ? { deny: "ENOENT" }
        : {
            value: {
              kind: options.homeKind ?? "dir",
              size: 0,
              mtimeMs: 1,
              isLink: false,
            },
          };
    }

    return e.path === CSB && options.csbMissing !== true
      ? {
          value: {
            kind: options.csbKind ?? "file",
            size: 1,
            mtimeMs: 1,
            isLink: options.csbIsLink === true,
          },
        }
      : { deny: "ENOENT" };
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
  on("ui.close", ($, e) => {
    closed.push(e.id);

    return { value: undefined };
  });
  on("ui.panes", () => ({
    value:
      options.panesOpen === true
        ? [
            {
              id: "containers",
              title: "containers",
              isShown: true,
              isFocused: false,
              isPlaced: true,
            },
          ]
        : [],
  }));
  on("command.register", ($, e) => {
    registered.push(e.name);

    return { value: { command: e.name } };
  });

  return {
    runs,
    stats,
    opened,
    closed,
    registered,
    clock,
    out,
    hold,
    release: () => release(),
  };
}

// 人がプロンプトで /containers [args] と打ったときの入力。
function run($: Engine, args = "") {
  return $.command.run({
    command: "containers",
    args,
    origin: { kind: "composer" },
    presentation: { isFullscreen: true, columns: 120 },
  });
}

function mountPane($: Engine, surface: "terminal" | "desktop" = "terminal") {
  return $.ui.mount({
    plugin: "containers",
    surface,
    component: "Pane",
    requestId: "containers",
    props: PANE_PROPS,
  });
}

const startSession = ($: Engine) =>
  $.session.start({ cwd: "/", surface: "terminal", isInteractive: true });

async function allText(ui: Awaited<ReturnType<typeof mountPane>>) {
  return (await ui.findAll({ type: "Text" })).map((node) => node.text);
}

describe("/containers", () => {
  test("session.start で /containers を登録する", async ($, on) => {
    const { registered } = world(on);

    await startSession($);

    expect(registered).toEqual(["containers"]);
  });

  test("固定の argv 3 つを、5 秒以内の timeout で走らせ、件数を返してペインを開く", async ($, on) => {
    const { runs, opened } = world(on);

    const result = await run($);

    expect(runs.map((r) => r.argv)).toEqual(expect.arrayContaining(ALLOWED));
    expect(runs).toHaveLength(3);
    for (const r of runs) {
      expect(r.timeoutMs).toBeDefined();
      expect(r.timeoutMs).toBeLessThanOrEqual(5000);
    }
    expect(opened).toEqual(["containers"]);
    expect(result.text).toBe(SUMMARY);
  });

  test("CLAUDE_CONFIG_DIR を見ない: csb は常に HOME の .local/bin/csb を呼ぶ", async ($, on) => {
    const { runs, stats } = world(on, { configDir: "/cfg" });

    await run($);

    expect(runs.map((r) => r.argv)).toContainEqual(CSB_ARGV);
    // 確かめるのは HOME とラッパーだけ (CLAUDE_CONFIG_DIR の下は見ない)
    expect(stats).toEqual([HOME, CSB]);
    for (const r of runs) {
      expect(r.argv.join(" ")).not.toContain("/cfg");
    }
  });

  test("csb は、PATH の python3 を通さず、絶対パスのラッパーを直接呼ぶ", async ($, on) => {
    const { runs } = world(on);

    await run($);

    const csb = runs.filter((r) => r.argv.includes("status"));
    expect(csb).toHaveLength(1);
    // 起動するのはラッパー自身 (信頼できるインタプリタを -I -S で起動するのはラッパーの仕事)
    expect(csb[0]?.argv[0]).toBe(CSB);
    expect(csb[0]?.argv[0]?.startsWith("/")).toBe(true);
    // どの argv にも python3 は現れない (PATH・pyenv の shim・.python-version に左右されない)
    for (const r of runs) {
      for (const word of r.argv) {
        expect(word).not.toBe("python3");
        expect(word.endsWith("/python3")).toBe(false);
        expect(word).not.toContain("claude_sandbox.py");
      }
    }
  });

  test("HOME が絶対パスなら、3 つとも cwd を HOME にして呼ぶ (セッションの cwd のリポジトリにある .python-version・.env などに依らない)", async ($, on) => {
    const { runs } = world(on);

    await run($);

    expect(runs).toHaveLength(3);
    expect(runs.map((r) => r.argv)).toEqual(expect.arrayContaining(ALLOWED));
    for (const r of runs) expect(r.cwd).toBe(HOME);
  });

  test("引数を付けると使い方だけを返し、何も走らせずペインも開かない", async ($, on) => {
    const { runs, opened } = world(on);

    const result = await run($, "rm");

    expect(result.text).toBe("使い方: /containers");
    expect(runs).toEqual([]);
    expect(opened).toEqual([]);
  });

  test("どの呼び出しも固定の 3 つの argv のどれかで、変更系のコマンドは走らせない", async ($, on) => {
    const w = world(on);

    await run($);
    const ui = await mountPane($);
    await ui.press({ key: "refresh" });
    await w.clock.advance(30_000);
    await ui.unmount();

    expect(w.runs.length).toBeGreaterThanOrEqual(9);
    for (const r of w.runs) expect(ALLOWED).toContainEqual(r.argv);
    const words = w.runs.flatMap((r) => r.argv);
    for (const banned of [
      "rm",
      "stop",
      "kill",
      "prune",
      "net",
      "exec",
      "inspect",
      "create",
      "apply",
    ]) {
      expect(words).not.toContain(banned);
    }
  });

  test("ペインを描けない場所では、件数と各区分の先頭 20 行だけを本文で返す", async ($, on) => {
    const many = Array.from({ length: 25 }, (_, i) =>
      ps({ ID: `bulk${String(i).padStart(8, "0")}`, Names: `bulk-${i}` }),
    ).join("\n");
    world(on, { isPlaced: false, ps: { stdout: many } });

    const result = await run($);

    const lines = result.text?.split("\n") ?? [];
    expect(lines[0]).toBe(
      "containers: サンドボックス 3 件 · その他のコンテナ 25 件 · compose 2 件",
    );
    expect(lines.filter((line) => line.includes("bulk-"))).toHaveLength(20);
    expect(result.text).toContain("他 5 件");
    expect(result.text).not.toContain(HOME);
  });
});

describe("ペイン", () => {
  test("3 つの区分を、端末でもデスクトップでも描く", async ($, on) => {
    world(on);
    await run($);

    for (const surface of ["terminal", "desktop"] as const) {
      const ui = await mountPane($, surface);

      expect(
        await ui.find({ type: "Text", text: "サンドボックス (3)" }),
      ).toBeDefined();
      expect(
        await ui.find({ type: "Text", text: "その他のコンテナ (3)" }),
      ).toBeDefined();
      expect(
        await ui.find({ type: "Text", text: "Compose プロジェクト (2)" }),
      ).toBeDefined();
      await ui.unmount();
    }
  });

  test("サンドボックス: ID・状態・役割・ネットワーク・リポジトリ・パッチの verify とレビュー", async ($, on) => {
    world(on);
    await run($);
    const ui = await mountPane($);

    expect((await ui.find({ type: "Text", text: ID_A }))?.text).toBe(
      `${ID_A}  running  役割: verify, work  ネット: 接続中 (${hhmm(NET_UNTIL)} まで)`,
    );
    expect((await ui.find({ type: "Text", text: ID_B }))?.text).toBe(
      `${ID_B}  exited  役割: work  ネット: 切断  [bind mount あり]`,
    );
    expect((await ui.find({ type: "Text", text: ID_BROKEN }))?.text).toBe(
      `${ID_BROKEN}  壊れている: meta.json を読めない: schema が不正`,
    );
    expect(await ui.find({ type: "Text", text: "~/work/app" })).toBeDefined();
    expect(await ui.find({ type: "Text", text: "/opt/other" })).toBeDefined();
    expect((await ui.find({ type: "Text", text: "patch aaaaaaa" }))?.text).toBe(
      "patch aaaaaaa  verify PASS  code-reviewer=pass codex=missing advisor=missing  (3 files +10/-2)",
    );
    expect(await ui.find({ type: "Text", text: "パッチなし" })).toBeDefined();
    await ui.unmount();
  });

  test("その他のコンテナ: 名前・イメージ・状態、bind mount の印、csb のラベルだけのコンテナ。csb の状態のあるものは除く", async ($, on) => {
    world(on);
    await run($);
    const ui = await mountPane($);

    expect(
      (await ui.find({ type: "Text", text: "web  nginx:1.27" }))?.text,
    ).toBe("web  nginx:1.27  Up 2 hours  [bind mount あり]");
    expect(
      (await ui.find({ type: "Text", text: "db  postgres:16" }))?.text,
    ).toBe("db  postgres:16  Exited (0) 4 weeks ago");
    expect(
      (await ui.find({ type: "Text", text: `csb-${ID_ORPHAN}` }))?.text,
    ).toBe(`csb-${ID_ORPHAN}  csb-generic:1  Up 1 hour  [csb ラベル]`);
    // csb の状態と結びつくコンテナは、サンドボックスの行に畳んである
    expect(
      await ui.find({ type: "Text", text: `csb-${ID_A}` }),
    ).toBeUndefined();
    expect(
      await ui.find({ type: "Text", text: `csb-${ID_B}` }),
    ).toBeUndefined();
    await ui.unmount();
  });

  test("ラベルだけを似せたコンテナ (名前が csb のものでない) は、サンドボックスの行に畳まず、その他に [csb ラベル] で残す", async ($, on) => {
    world(on, {
      ps: {
        stdout: [
          DOCKER_PS,
          ps({
            ID: "eeee00000001",
            Names: "evil",
            Image: "evil:1",
            Mounts: `${HOME}/stolen`,
            // 別のラベルの値にカンマで埋め込んだ、サンドボックス ID_A のラベル
            Labels: `com.example.note=hi,${sandboxLabels(ID_A, "setup")}`,
          }),
        ].join("\n"),
      },
    });

    const result = await run($);
    const ui = await mountPane($);

    expect(result.text).toBe(
      "containers: サンドボックス 3 件 · その他のコンテナ 4 件 · compose 2 件",
    );
    expect((await ui.find({ type: "Text", text: "evil  evil:1" }))?.text).toBe(
      "evil  evil:1  Up  [bind mount あり]  [csb ラベル]",
    );
    // サンドボックスの行の役割・bind mount に、このコンテナのものは混ざらない
    expect((await ui.find({ type: "Text", text: ID_A }))?.text).toBe(
      `${ID_A}  running  役割: verify, work  ネット: 接続中 (${hhmm(NET_UNTIL)} まで)`,
    );
    await ui.unmount();
  });

  test("Compose プロジェクト: 名前・状態・設定ファイル (~ 相対、2 つ目以降は件数)", async ($, on) => {
    world(on);
    await run($);
    const ui = await mountPane($);

    expect(
      (await ui.find({ type: "Text", text: "app  running(2)" }))?.text,
    ).toBe("app  running(2)  ~/work/app/compose.yml");
    expect(
      (await ui.find({ type: "Text", text: "data  exited(1)" }))?.text,
    ).toBe("data  exited(1)  ~/data/docker-compose.yml (+1)");
    await ui.unmount();
  });

  test("一覧の行には、重ならない key を付ける", async ($, on) => {
    world(on);
    await run($);
    const ui = await mountPane($);

    for (const [prefix, count] of [
      ["sandbox-", 3],
      ["container-", 3],
      ["compose-", 2],
    ] as const) {
      const keyed = (await ui.findAll({ type: "Box" })).filter(
        (box) => box.key !== undefined && box.key.startsWith(prefix),
      );
      expect(keyed).toHaveLength(count);
      expect(new Set(keyed.map((box) => box.key)).size).toBe(count);
    }
    await ui.unmount();
  });

  test("環境変数・Command・ラベル・マウントのパスは描かず、ホームの絶対パスも出さない", async ($, on) => {
    world(on);
    await run($);
    const ui = await mountPane($);

    const shown = (await allText(ui)).join("\n");
    expect(shown).not.toContain("s3cr3t");
    expect(shown).not.toContain("secret-dir");
    expect(shown).not.toContain(`${HOME}/site`);
    expect(shown).not.toContain(HOME);
    await ui.unmount();
  });

  test("docker の出力に混ざる端末制御・表示偽装の文字は、描く前に無害化する", async ($, on) => {
    world(on, {
      ps: {
        stdout: ps({
          ID: "c0ffee000001",
          Names: "we\u001b[2Jb\u202E",
          Image: "img",
          Status: "Up",
        }),
      },
    });
    await run($);
    const ui = await mountPane($);

    const shown = (await allText(ui)).join("\n");
    expect(shown).toContain("we\uFFFD[2Jb\uFFFD");
    expect(shown).not.toContain("\u001b");
    expect(shown).not.toContain("\u202E");
    await ui.unmount();
  });

  test("更新ボタンで読み直し、変わった内容を描く", async ($, on) => {
    const w = world(on);
    await run($);
    const ui = await mountPane($);
    expect(
      await ui.find({ type: "Text", text: "web  nginx:1.27" }),
    ).toBeDefined();

    w.out.ps = {
      stdout: ps({
        ID: "c0ffee000003",
        Names: "cache",
        Image: "redis:7",
        Status: "Up 1 minute",
      }),
    };
    await ui.press({ key: "refresh" });

    expect(
      await ui.find({ type: "Text", text: "cache  redis:7" }),
    ).toBeDefined();
    expect(
      await ui.find({ type: "Text", text: "web  nginx:1.27" }),
    ).toBeUndefined();
    await ui.unmount();
  });

  test("閉じるボタンはペインを閉じる", async ($, on) => {
    const w = world(on);
    await run($);
    const ui = await mountPane($);

    await ui.press({ key: "close" });
    await w.clock.advance(30_000);

    expect(w.closed).toEqual(["containers"]);
    expect(w.runs).toHaveLength(3);
    await ui.unmount();
  });

  test("まだ一度も読んでいなければ「読み込み中」を描く (前のバージョンが残した値は捨てる)", async ($, on) => {
    on("state.get", () => ({
      value: { value: { at: "yesterday" }, version: 1 },
    }));

    const ui = await mountPane($);

    expect(await ui.find({ type: "Text", text: "読み込み中" })).toBeDefined();
    await ui.unmount();
  });
});

describe("取れなかったとき: 区分ごとに 1 行の知らせを出し、落ちない", () => {
  test("docker が無い・時間切れ: 他のコンテナと compose に知らせを出し、サンドボックスは出す", async ($, on) => {
    world(on, {
      ps: { deny: "spawn docker ENOENT" },
      compose: { deny: "timed out after 5000 ms" },
    });

    const result = await run($);
    const ui = await mountPane($);

    expect(result.text).toBe(
      [
        "containers: サンドボックス 3 件 · その他のコンテナ 取得できず · compose 取得できず",
        "- docker ps を実行できませんでした (未導入か、5 秒以内に終わりませんでした)",
        "- docker compose ls を実行できませんでした (未導入か、5 秒以内に終わりませんでした)",
      ].join("\n"),
    );
    expect(
      await ui.find({ type: "Text", text: "docker ps を実行できません" }),
    ).toBeDefined();
    expect(
      await ui.find({
        type: "Text",
        text: "docker compose ls を実行できません",
      }),
    ).toBeDefined();
    // 役割と bind mount は docker から引けていない。「役割なし・bind mount なし」に見える
    // 表示にはせず、不明と出す (行そのものは出る)
    const unknown = await ui.find({ type: "Text", text: ID_A });
    expect(unknown?.text).toBe(
      `${ID_A}  running  役割: 不明  ネット: 接続中 (${hhmm(NET_UNTIL)} まで)  [bind mount 不明]`,
    );
    // 安全の指標が取れていない行は、緑 (問題なし) にしない
    expect(unknown?.props.color).toBe("yellow");
    await ui.unmount();
  });

  test("docker ps が失敗したとき、本文 (ペインを描けない場所) の行も「不明」と出す", async ($, on) => {
    world(on, { isPlaced: false, ps: { exitCode: 1, stderr: "boom" } });

    const result = await run($);

    expect(result.text).toContain(
      `${ID_A}  running  役割: 不明  ネット: 接続中 (${hhmm(NET_UNTIL)} まで)  [bind mount 不明]`,
    );
    expect(result.text).not.toContain("役割: -");
  });

  test("docker ps の出力が途中で切れたら、サンドボックスの役割は不明、bind mount は見えたものだけ「あり」", async ($, on) => {
    const whole = ps({ ID: "c0ffee000009", Names: "cut" });
    world(on, {
      ps: {
        // ID_B の bind mount は先頭に近い行にあって見えている。ID_A の分は途中で切れている
        stdout: `${ps({
          ID: "bbbb00000001",
          Names: `csb-${ID_B}`,
          State: "exited",
          Mounts: `${HOME}/work/other`,
          Labels: sandboxLabels(ID_B, "work"),
        })}\n${whole.slice(0, whole.length - 10)}`,
        truncated: true,
      },
    });

    await run($);
    const ui = await mountPane($);

    expect((await ui.find({ type: "Text", text: ID_A }))?.text).toBe(
      `${ID_A}  running  役割: 不明  ネット: 接続中 (${hhmm(NET_UNTIL)} まで)  [bind mount 不明]`,
    );
    expect((await ui.find({ type: "Text", text: ID_B }))?.text).toBe(
      `${ID_B}  exited  役割: 不明  ネット: 切断  [bind mount あり]`,
    );
    await ui.unmount();
  });

  test("docker ps を最後まで読めて、サンドボックスのコンテナが無ければ「役割: -」(不明にはしない)", async ($, on) => {
    world(on, { ps: { stdout: ps({ ID: "c0ffee000001", Names: "web" }) } });

    await run($);
    const ui = await mountPane($);

    const row = await ui.find({ type: "Text", text: ID_A });
    expect(row?.text).toBe(
      `${ID_A}  running  役割: -  ネット: 接続中 (${hhmm(NET_UNTIL)} まで)`,
    );
    expect(row?.props.color).toBe("green");
    await ui.unmount();
  });

  test("ソケットの権限が無い: デーモンが落ちているとは別の固定の 1 行にして、stderr は出さない", async ($, on) => {
    const stderr =
      "permission denied while trying to connect to the docker API at unix:///var/run/docker.sock";
    world(on, {
      ps: { exitCode: 1, stderr },
      compose: { exitCode: 1, stderr },
    });

    const result = await run($);
    const ui = await mountPane($);

    const notice =
      "Docker のソケットを使う権限がありません (実行ユーザーの権限を確かめてください)";
    expect(await ui.findAll({ type: "Text", text: notice })).toHaveLength(2);
    expect(result.text).toContain(`- ${notice}`);
    expect(result.text).not.toContain("起動していますか");
    expect(result.text).not.toContain("docker.sock");
    expect((await allText(ui)).join("\n")).not.toContain("docker.sock");
    await ui.unmount();
  });

  test("デーモンが落ちている: stderr は出さず、固定の 1 行にする", async ($, on) => {
    const stderr =
      "Cannot connect to the Docker daemon at unix:///home/tester/.docker/run/docker.sock. Is the docker daemon running?";
    world(on, {
      ps: { exitCode: 1, stderr },
      compose: { exitCode: 1, stderr },
    });

    const result = await run($);
    const ui = await mountPane($);

    const notice =
      "Docker デーモンに接続できません (Docker が起動していますか)";
    expect(await ui.findAll({ type: "Text", text: notice })).toHaveLength(2);
    expect(result.text).not.toContain("docker.sock");
    expect((await allText(ui)).join("\n")).not.toContain("docker.sock");
    await ui.unmount();
  });

  test("csb のラッパー (~/.local/bin/csb) が無い: 場所と直し方 (install.sh) が分かる「使えません」の知らせを出し、csb は走らせない。ラベルのコンテナは csb ラベルとして残る", async ($, on) => {
    const { runs } = world(on, { csbMissing: true });

    const result = await run($);
    const ui = await mountPane($);

    expect(runs.map((r) => r.argv)).not.toContainEqual(CSB_ARGV);
    expect(runs.some((r) => r.argv[0] === CSB)).toBe(false);
    expect(result.text?.split("\n")[0]).toBe(
      "containers: サンドボックス 取得できず · その他のコンテナ 6 件 · compose 2 件",
    );
    // 場所 (~/.local/bin/csb) と、直し方 (install.sh) が分かる。HOME の絶対パスは載せない
    expect(result.text).toContain(NOTICE_CSB);
    expect(result.text).toContain("~/.local/bin/csb");
    expect(result.text).toContain("通常のファイルでない");
    expect(result.text).toContain("install.sh");
    // 原因は HOME ではないので、HOME の文面は出さない
    expect(result.text).not.toContain(NOTICE_HOME);
    expect(result.text).not.toContain(HOME);
    expect(await ui.find({ type: "Text", text: NOTICE_CSB })).toBeDefined();
    expect(
      (await ui.find({ type: "Text", text: `csb-${ID_A}  ` }))?.text,
    ).toContain("[csb ラベル]");
    await ui.unmount();
  });

  test("csb の場所がディレクトリなら、ラッパーではないので走らせず、「使えません」の知らせを出す", async ($, on) => {
    const { runs } = world(on, { csbKind: "dir" });

    const result = await run($);

    expect(runs.some((r) => r.argv[0] === CSB)).toBe(false);
    expect(result.text).toContain(NOTICE_CSB);
  });

  // $.fs.stat は symlink を辿るので kind だけでは弾けない: 古い install が残した
  // ~/.local/bin/csb -> claude_sandbox.py の symlink は kind が file になり、起動すると
  // shebang の #!/usr/bin/env python3 を通して PATH の python3 が -I -S なしで動く。
  // install.sh が書くのは通常ファイルなので、isLink が真のものは走らせない。
  test("csb の場所が symlink なら (kind が file でも)、ラッパーではないので走らせず、「使えません」の知らせを出す", async ($, on) => {
    const { runs, stats } = world(on, { csbIsLink: true });

    const result = await run($);

    expect(stats).toEqual([HOME, CSB]);
    expect(runs.some((r) => r.argv[0] === CSB)).toBe(false);
    expect(runs.some((r) => r.argv.includes("status"))).toBe(false);
    expect(result.text).toContain(NOTICE_CSB);
    expect(result.text).toContain("install.sh");
    // docker の 2 つは走る
    expect(runs.map((r) => r.argv)).toEqual(
      expect.arrayContaining([PS_ARGV, COMPOSE_ARGV]),
    );
  });

  // HOME が絶対パスでも、ディレクトリとして実在しなければ cwd にできない (spawn が失敗し、
  // 以前は動いていた docker まで取れなくなる)。stat で dir と確かめたときだけ cwd を HOME に
  // し、それ以外は cwd を指定しない。csb はラッパーが HOME の下なので、HOME が dir でなければ
  // 走らせない (csb は常に cwd=HOME で走らせる)。
  for (const homeKind of ["missing", "file"] as const) {
    test(`HOME が絶対パスでも、ディレクトリでない (${homeKind}): 3 つとも cwd を指定せず、csb は走らせず、docker は取れる`, async ($, on) => {
      const { runs, stats } = world(on, { homeKind });

      const result = await run($);

      // 存在しない HOME を cwd にすると spawn が失敗する: docker は cwd 無しで走らせる
      for (const r of runs) expect(r.cwd).toBeUndefined();
      expect(runs.map((r) => r.argv)).toEqual(
        expect.arrayContaining([PS_ARGV, COMPOSE_ARGV]),
      );
      expect(runs.some((r) => r.argv.includes("status"))).toBe(false);
      expect(runs).toHaveLength(2);
      expect(stats).toEqual([HOME]);
      // 原因は HOME: ラッパーのせいにして install.sh を勧めない。HOME の値は載せない
      expect(result.text).toContain(NOTICE_CSB);
      expect(result.text).toContain(NOTICE_HOME);
      expect(result.text).not.toContain("install.sh");
      expect(result.text).not.toContain(HOME);
      expect(result.text).toContain("その他のコンテナ 6 件 · compose 2 件");
    });
  }

  test("HOME が取れない: ラッパーの場所を決められないので、確かめも起動もせず、HOME の知らせを出す。docker は今の cwd のまま", async ($, on) => {
    const { runs, stats } = world(on, { home: null });

    const result = await run($);

    expect(stats).toEqual([]);
    expect(runs.some((r) => r.argv.includes("status"))).toBe(false);
    expect(result.text).toContain(NOTICE_CSB);
    expect(result.text).toContain(NOTICE_HOME);
    expect(result.text).not.toContain("install.sh");
    // docker の 2 つは HOME に依らず走る。HOME が無いので cwd は指定しない (セッションの cwd)
    expect(runs.map((r) => r.argv)).toEqual(
      expect.arrayContaining([PS_ARGV, COMPOSE_ARGV]),
    );
    for (const r of runs) expect(r.cwd).toBeUndefined();
  });

  // 絶対パスでない HOME は、cwd (信頼できないリポジトリのこともある) 次第で別のファイルを
  // 指す。argv[0] が絶対パスにならないので、csb は起動しない。docker は、cwd を
  // 指定しない今の挙動のまま (相対の HOME を cwd にすると、そこでもまた cwd 次第になる)。
  for (const home of ["", "relative/home", "~"]) {
    test(`HOME が絶対パスでない (${JSON.stringify(home)}): csb は起動せず HOME の知らせを出し、docker は cwd を指定せずに走らせる`, async ($, on) => {
      const { runs, stats } = world(on, { home });

      const result = await run($);

      expect(stats).toEqual([]);
      expect(runs.some((r) => r.argv.includes("status"))).toBe(false);
      expect(result.text).toContain(NOTICE_CSB);
      expect(result.text).toContain(NOTICE_HOME);
      expect(result.text).not.toContain("install.sh");
      expect(runs.map((r) => r.argv)).toEqual(
        expect.arrayContaining([PS_ARGV, COMPOSE_ARGV]),
      );
      for (const r of runs) expect(r.cwd).toBeUndefined();
    });
  }

  test("csb が失敗した・出力を読めない", async ($, on) => {
    const w = world(on, { csb: { exitCode: 2, stderr: "boom" } });

    const failed = await run($);
    expect(failed.text).toContain("- csb status が失敗しました (exit 2)");
    expect(failed.text).not.toContain("boom");

    w.out.csb = { stdout: "not json" };
    const garbled = await run($);
    expect(garbled.text).toContain("- csb status の出力を読めませんでした");
  });

  test("docker の出力が上限で切れたら、途中の行は捨て、切れたことを知らせる", async ($, on) => {
    const whole = ps({ ID: "c0ffee000009", Names: "cut" });
    world(on, {
      ps: {
        stdout: `${ps({ ID: "c0ffee000001", Names: "web", Image: "nginx" })}\n${whole.slice(0, whole.length - 10)}`,
        truncated: true,
      },
    });

    const result = await run($);
    const ui = await mountPane($);

    expect(result.text).toContain("その他のコンテナ 1 件");
    expect(
      await ui.find({ type: "Text", text: "一覧が長すぎるため途中までです" }),
    ).toBeDefined();
    expect(await ui.find({ type: "Text", text: "cut" })).toBeUndefined();
    await ui.unmount();
  });

  test("全部が失敗しても、コマンドは例外にせず本文を返す", async ($, on) => {
    world(on, {
      csb: { deny: "x" },
      ps: { deny: "x" },
      compose: { deny: "x" },
    });

    const result = await run($);

    expect(result.text?.split("\n")[0]).toBe(
      "containers: サンドボックス 取得できず · その他のコンテナ 取得できず · compose 取得できず",
    );
  });

  test("何も無いときは「なし」と出す", async ($, on) => {
    world(on, {
      csb: { stdout: "[]" },
      ps: { stdout: "" },
      compose: { stdout: "[]" },
    });

    const result = await run($);
    const ui = await mountPane($);

    expect(result.text).toBe(
      "containers: サンドボックス 0 件 · その他のコンテナ 0 件 · compose 0 件",
    );
    expect(await ui.findAll({ type: "Text", text: "なし" })).toHaveLength(3);
    await ui.unmount();
  });
});

describe("更新のタイマー", () => {
  test("ペインを開いている間は 10 秒ごとに読み直す", async ($, on) => {
    const w = world(on);

    await run($);
    expect(w.runs).toHaveLength(3);
    await w.clock.advance(10_000);
    expect(w.runs).toHaveLength(6);
    await w.clock.advance(10_000);
    expect(w.runs).toHaveLength(9);
  });

  test("ペインが閉じたらタイマーを止め、以後プロセスを起動しない", async ($, on) => {
    const w = world(on);
    await run($);
    const ui = await mountPane($);
    await w.clock.advance(10_000);
    expect(w.runs).toHaveLength(6);

    // 閉じるボタンが、読み直しを止めて $.ui.close を呼ぶ
    await ui.press({ key: "close" });
    await w.clock.advance(60_000);

    expect(w.closed).toEqual(["containers"]);
    expect(w.runs).toHaveLength(6);
    await ui.unmount();
  });

  // 閉じる経路のテストでできること・できないこと:
  // - 閉じるボタンは、読み直しを止めてから $.ui.close を呼ぶ (エンジンに届くのは
  //   w.closed で見える)。上と下のテストが見ているのは、このボタンの経路。
  // - mod の ui.close フック本体は、このテストキットでは起動できない:
  //   · mod 自身の $.ui.close は、mod 自身の ui.close フックを通らない (ボタンの自前の
  //     停止を外すと、w.closed には届くのにタイマーが止まらないことを確かめた。
  //     コードのコメントの「自分が閉じるときはフックを通らないことがある」のとおり)。
  //   · 利用者の操作 (Esc・ペインの閉じる印) やホストが起こす ui.close は起こせない:
  //     テストの Engine の $ に $.ui.close は無く、テスト側のフックから呼んでも
  //     ホストが拒む (その hooks module が呼ぶと宣言していないため)。mock にもペインの口が無い。
  //   そのため、利用者が閉じたときに ui.close のフックが読み直しを止めるかどうかは、
  //   ここでは確かめられない (実機で確かめる)。
  // - 次のテストは、確かめられるうちの境界: 読み込みの最中に閉じても、終わったあとに
  //   タイマーを動かし直さない。
  test("読み込みの最中に閉じても、終わったあとに読み直しを再開しない", async ($, on) => {
    const w = world(on);
    await run($);
    const ui = await mountPane($);

    w.hold();
    await w.clock.advance(10_000);
    // 周期の読み込みが始まって、答えを待っている
    expect(w.runs).toHaveLength(6);

    await ui.press({ key: "close" });
    w.release();
    await w.clock.settle();
    await w.clock.advance(60_000);

    expect(w.closed).toEqual(["containers"]);
    expect(w.runs).toHaveLength(6);
    await ui.unmount();
  });

  test("閉じたあとに /containers でもう一度開けば、読み直しが再開する", async ($, on) => {
    const w = world(on);
    await run($);
    const ui = await mountPane($);
    await ui.press({ key: "close" });
    await w.clock.advance(30_000);
    expect(w.runs).toHaveLength(3);

    await run($);
    await w.clock.advance(10_000);

    expect(w.runs).toHaveLength(9);
    await ui.unmount();
  });

  test("/containers を重ねて実行しても、タイマーは 1 本のまま", async ($, on) => {
    const w = world(on);
    await run($);
    await run($);
    expect(w.runs).toHaveLength(6);

    await w.clock.advance(10_000);

    expect(w.runs).toHaveLength(9);
  });

  test("ペインを描けない場所では、タイマーを動かさない", async ($, on) => {
    const w = world(on, { isPlaced: false });
    await run($);

    await w.clock.advance(60_000);

    expect(w.runs).toHaveLength(3);
  });

  test("同時に 2 回呼んでも、プロセスは 3 つだけ (読み込みを共有する)", async ($, on) => {
    const w = world(on);
    w.hold();

    const first = run($);
    const second = run($);
    await w.clock.settle();
    expect(w.runs).toHaveLength(3);

    w.release();
    expect((await first).text).toBe(SUMMARY);
    expect((await second).text).toBe(SUMMARY);
    expect(w.runs).toHaveLength(3);
  });

  test("周期が来たとき、前の読み込みが終わっていなければ重ねて始めない", async ($, on) => {
    const w = world(on);
    await run($);
    expect(w.runs).toHaveLength(3);

    w.hold();
    await w.clock.advance(10_000);
    expect(w.runs).toHaveLength(6);
    await w.clock.advance(30_000);
    expect(w.runs).toHaveLength(6);

    w.release();
    await w.clock.settle();
    // 終われば、次の周期でまた読む
    await w.clock.advance(10_000);
    expect(w.runs).toHaveLength(9);
  });

  test("リロード (session.start の再発火) でペインが開いたままなら、読み直しを再開する", async ($, on) => {
    const w = world(on, { panesOpen: true });

    await startSession($);
    expect(w.runs).toHaveLength(0);
    await w.clock.advance(10_000);

    expect(w.runs).toHaveLength(3);
  });

  test("ペインが開いていなければ、session.start ではタイマーを動かさない", async ($, on) => {
    const w = world(on, { panesOpen: false });

    await startSession($);
    await w.clock.advance(60_000);

    expect(w.runs).toEqual([]);
  });
});
