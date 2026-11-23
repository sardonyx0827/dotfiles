import { atom, read, update } from "claude-code";
import type { EngineInterface, Register } from "claude-code";

import type { Snapshot } from "../types";
import {
  PROCESS_TIMEOUT_S,
  TITLE_COMPOSE,
  TITLE_CONTAINERS,
  TITLE_SANDBOXES,
  buildSnapshot,
  composeLine,
  containerLine,
  formatTime,
  listRows,
  patchLine,
  sandboxHeader,
  sectionTitle,
  summarize,
} from "./catalog";

// csb のサンドボックスと、ローカルの Docker コンテナの状態を一覧する mod。
// 変更するコマンド (rm / stop / prune / net on など) は一切走らせない: 走らせるのは
// 下の 3 つの固定の argv だけで、シェルは通さない (csb は <HOME>/.local/bin/csb の絶対
// パスで呼ぶ。3 つとも、HOME が絶対パスのディレクトリなら cwd を HOME にする)。ただし
// 「読み取り専用」ではない:
// csb は、どのサブコマンドでも (status --json でも) 最初に、期限の切れた net on の窓を
// 切断する (expire_windows)。/containers を実行するたびと、ペインを開いている間は
// 10 秒ごとに csb status を呼ぶので、この mod が期限切れの窓を切断する主体になる
// (接続を切る向きの動きで、接続を増やすことはない)。ツールの実行可否に関わるイベント
// には一切フックしない: mod は settings hook の前後で判定を差し替えられるため、触れる
// と bash-review などを素通りさせる余地が生まれる (tests/test_config_wiring.py が
// 静的に検査している)。
//
// docker inspect は呼ばない (環境変数を引かない)。docker ps の出力からは、名前・
// イメージ・状態・Mounts・サンドボックスのラベルだけを取り、Command・Labels の中身・
// マウントのパスは捨てる。

const COMMAND = "containers";
const PANE = "containers";
const TITLE = "containers";
const USAGE = `使い方: /${COMMAND}`;
const PANE_COLUMNS = 110;
// 読み直しの周期。プロセスの timeout (5 秒) より長くして、重ならないようにする。
const POLL_MS = 10_000;
const PROCESS_TIMEOUT_MS = PROCESS_TIMEOUT_S * 1000;
const PS_ARGV = ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"];
const COMPOSE_ARGV = ["docker", "compose", "ls", "--format", "json"];
const RELOAD_FAILED = "containers: 読み込めませんでした (詳細はデバッグログ)";

// 値は表示用に整えた Snapshot だけ。形を変えたら shape の版を上げる (前の版が
// 残した値はリロード後に捨てられる)。
const snapshotAtom = atom(
  { plugin: "containers", key: "snapshot" } as const,
  null as Snapshot | null,
  { shape: "snapshot-2" },
);

// モジュール変数はホットリロードで初期化される。ペインの開閉は session.start
// (リロード時にも再発火する) で $.ui.panes() から取り直す。
let isOpen = false;
// ペインを開いている間だけ動く読み直しのタイマー。
let poll: { cancel: () => void } | undefined;
// 読み込み中の Promise。周期・ボタン・コマンドが重なっても、プロセスは 1 組だけ。
let inFlight: Promise<Snapshot> | undefined;

// csb の入口は、install.sh が書く固定のラッパー <HOME>/.local/bin/csb だけ。信頼できる
// (root 所有の) python3 を -I -S で起動する数行のシェルスクリプトなので、ここでは
// インタプリタを選ばない: PATH の python3 (pyenv の shim など) を通すと、cwd の
// .python-version (信頼できないリポジトリのこともある) で別のインタプリタに差し替えられる。
// 場所は HOME から決め、CLAUDE_CONFIG_DIR は見ない (あれは設定の置き場で、実行物の場所
// を決める値にはしない)。HOME が絶対パスでなければ、argv[0] が cwd 次第の相対パスに
// なるので、無いものとして扱う (呼び出し側の homeCwd が先に弾く)。
//
// $.fs.stat は symlink を辿り、kind は辿った先の種類になる。install.sh が書くのは通常
// ファイル (ラッパーの検証も lstat で通常ファイルを求める) なので、isLink が真のもの
// (古い install が残した ~/.local/bin/csb -> claude_sandbox.py など) は起動しない:
// 起動すると、shebang の #!/usr/bin/env python3 を通して PATH の python3 が -I -S なしで
// 動く。realPath との比較はしない (~/.local や HOME の上位が symlink でも、正しい
// ラッパーを「使えない」ことにしてしまうため)。
async function csbWrapper(
  $: EngineInterface,
  home: string,
): Promise<string | undefined> {
  const path = `${home}/.local/bin/csb`;
  try {
    const stat = await $.fs.stat(path);

    return stat.kind === "file" && !stat.isLink ? path : undefined;
  } catch {
    return undefined;
  }
}

// 3 つを走らせる cwd: HOME が、絶対パスで、ディレクトリとして実在するときだけ HOME。
// セッションの cwd は信頼できないリポジトリのことがあり、.python-version・.env などに
// 左右されないようにする。それ以外は undefined (cwd を指定しない = セッションの cwd): 絶対パス
// でない HOME を cwd にしても cwd 次第になるだけで、実在しない HOME を cwd にすると spawn
// が失敗し、以前は取れていた docker まで取れなくなる。
async function homeCwd(
  $: EngineInterface,
  home: string,
): Promise<string | undefined> {
  if (!home.startsWith("/")) return undefined;
  try {
    return (await $.fs.stat(home)).kind === "dir" ? home : undefined;
  } catch {
    return undefined;
  }
}

async function collect($: EngineInterface): Promise<Snapshot> {
  const home = (await $.env.get("HOME")) ?? "";
  const cwd = await homeCwd($, home);
  // csb のラッパーは HOME の下にあるので、HOME が cwd にできるとき (絶対パスの dir) だけ
  // 探す。csb は常に cwd=HOME で走る (セッションの cwd では走らせない)。
  const csbPath = cwd === undefined ? undefined : await csbWrapper($, cwd);
  const run = (argv: string[]) =>
    $.process.run(argv, {
      timeoutMs: PROCESS_TIMEOUT_MS,
      ...(cwd === undefined ? {} : { cwd }),
    });
  // 3 つは独立なので並べて走らせる。どれもエンジンが timeout で止めるので、
  // 読み直し 1 回は長くても 5 秒ほど。
  const [csb, ps, compose] = await Promise.allSettled([
    csbPath === undefined
      ? Promise.reject(new Error("csb is missing"))
      : run([csbPath, "status", "--json"]),
    run(PS_ARGV),
    run(COMPOSE_ARGV),
  ]);

  return buildSnapshot(await $.clock.now(), home, {
    // 知らせの原因を分ける: HOME が使えない (install.sh では直らない) と、ラッパーが使えない
    csb:
      cwd === undefined ? "no-home" : csbPath === undefined ? "missing" : csb,
    ps,
    compose,
  });
}

function load($: EngineInterface): Promise<Snapshot> {
  if (inFlight !== undefined) return inFlight;
  const work = (async () => {
    try {
      const snapshot = await collect($);
      await update($, snapshotAtom, () => snapshot);

      return snapshot;
    } finally {
      inFlight = undefined;
    }
  })();
  inFlight = work;

  return work;
}

async function loadQuietly($: EngineInterface): Promise<Snapshot | undefined> {
  try {
    return await load($);
  } catch (error) {
    $.ui.log(`containers: load failed: ${String(error)}`, { to: "debug" });

    return undefined;
  }
}

function stopPolling(): void {
  poll?.cancel();
  poll = undefined;
}

// ペインを開いている間だけ読み直す。開く・開き直すたびに作り直し、重ねない。
function startPolling($: EngineInterface): void {
  stopPolling();
  poll = $.clock.every(POLL_MS, () => {
    if (isOpen) void loadQuietly($);
  });
}

export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    // ここで投げるとセッションの開始 (next) まで止めてしまうので、握って続ける。
    try {
      await $.command.register({
        name: COMMAND,
        description:
          "csb サンドボックスとローカルの Docker コンテナの状態をペインで表示 (実行のたびと、開いている間は 10 秒ごとに csb status を呼ぶので、期限切れの net の窓は切断される)",
        immediate: true,
      });
      isOpen = (await $.ui.panes()).some((pane) => pane.id === PANE);
    } catch (error) {
      $.ui.log(`containers: session.start failed: ${String(error)}`, {
        to: "debug",
      });
    }
    // リロード (session.start が再発火) 時に、ペインが開いたままなら読み直しを再開する。
    if (isOpen) startPolling($);

    return next(e);
  });

  on("command.run", { command: COMMAND }, async ($, e) => {
    // 型の上では string だが、別プラグインが $.command.run を args なしで呼ぶと空になる。
    if ((e.args ?? "").trim() !== "") return { text: USAGE };
    const snapshot = await loadQuietly($);
    if (snapshot === undefined) return { text: RELOAD_FAILED };

    const lines = summarize(snapshot);
    const opened = await $.ui.open({
      id: PANE,
      title: TITLE,
      columns: PANE_COLUMNS,
    });
    isOpen = opened.isPlaced;
    if (opened.isPlaced) {
      startPolling($);

      return { text: lines.join("\n") };
    }

    // ペインを描けない場所では、区分ごとの先頭を本文で返す。本文はモデルも読むので
    // 行数を絞り、パッチの行 (サンドボックスの中身) は載せない。
    return { text: [...lines, ...listRows(snapshot)].join("\n") };
  });

  on("ui.close", { id: PANE }, ($, e, next) => {
    isOpen = false;
    stopPolling();

    return next(e);
  });

  on("ui.render", { component: "Pane", requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e);
    const snapshot = await read($, snapshotAtom);

    const notice = (text: string | null) =>
      text === null ? null : <Text color="yellow">{text}</Text>;

    return (
      <Box flexDirection="column">
        <Box flexDirection="row" flexWrap="wrap" columnGap={2}>
          <Text bold>containers</Text>
          <Text dimColor>
            {snapshot === null
              ? "未取得"
              : `取得 ${formatTime(snapshot.at, true)}`}
          </Text>
          <Button
            key="refresh"
            hotkey="r"
            label="更新"
            onPress={async () => {
              await loadQuietly($);
            }}
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
        {snapshot === null && <Text dimColor>読み込み中…</Text>}
        {snapshot !== null && (
          <Box flexDirection="column" rowGap={1}>
            <Box flexDirection="column">
              <Text bold>
                {sectionTitle(TITLE_SANDBOXES, snapshot.sandboxes)}
              </Text>
              {notice(snapshot.sandboxes.notice)}
              {snapshot.sandboxes.rows.length === 0 &&
                snapshot.sandboxes.notice === null && (
                  <Text dimColor>なし</Text>
                )}
              {snapshot.sandboxes.rows.map((row) => (
                <Box key={`sandbox-${row.id}`} flexDirection="column">
                  <Text
                    color={
                      row.broken !== null || row.hasBind === true
                        ? "red"
                        : // bind mount の有無を確かめられていない行は、問題なし (緑) にしない
                          row.hasBind === null
                          ? "yellow"
                          : row.isRunning
                            ? "green"
                            : undefined
                    }
                  >
                    {sandboxHeader(row)}
                  </Text>
                  {row.broken === null && (
                    <Box flexDirection="column" paddingLeft={2}>
                      <Text dimColor>{row.repo}</Text>
                      {row.check !== null && (
                        <Text color="yellow">{`照合: ${row.check}`}</Text>
                      )}
                      {row.patches.length === 0 && (
                        <Text dimColor>パッチなし</Text>
                      )}
                      {row.patches.map((patch, index) => (
                        <Box key={`patch-${row.id}-${index}`}>
                          <Text
                            color={
                              patch.error !== null || patch.verify === "FAIL"
                                ? "red"
                                : patch.verify === "PASS"
                                  ? "green"
                                  : "yellow"
                            }
                          >
                            {patchLine(patch)}
                          </Text>
                        </Box>
                      ))}
                    </Box>
                  )}
                </Box>
              ))}
              {snapshot.sandboxes.hidden > 0 && (
                <Text dimColor>{`他 ${snapshot.sandboxes.hidden} 件`}</Text>
              )}
            </Box>
            <Box flexDirection="column">
              <Text bold>
                {sectionTitle(TITLE_CONTAINERS, snapshot.containers)}
              </Text>
              {notice(snapshot.containers.notice)}
              {snapshot.containers.rows.length === 0 &&
                snapshot.containers.notice === null && (
                  <Text dimColor>なし</Text>
                )}
              {snapshot.containers.rows.map((row) => (
                <Box key={`container-${row.id}`}>
                  <Text
                    {...(row.hasBind
                      ? { color: "yellow" }
                      : row.isRunning
                        ? {}
                        : { dimColor: true })}
                  >
                    {containerLine(row)}
                  </Text>
                </Box>
              ))}
              {snapshot.containers.hidden > 0 && (
                <Text dimColor>{`他 ${snapshot.containers.hidden} 件`}</Text>
              )}
            </Box>
            <Box flexDirection="column">
              <Text bold>{sectionTitle(TITLE_COMPOSE, snapshot.compose)}</Text>
              {notice(snapshot.compose.notice)}
              {snapshot.compose.rows.length === 0 &&
                snapshot.compose.notice === null && <Text dimColor>なし</Text>}
              {snapshot.compose.rows.map((row, index) => (
                <Box key={`compose-${index}-${row.name}`}>
                  <Text>{composeLine(row)}</Text>
                </Box>
              ))}
              {snapshot.compose.hidden > 0 && (
                <Text dimColor>{`他 ${snapshot.compose.hidden} 件`}</Text>
              )}
            </Box>
          </Box>
        )}
      </Box>
    );
  });
};
