// containers の $.state 契約。
// 行は表示用に整えた (無害化・~ 相対化済みの) 文字列だけを持つ。docker / csb の出力の
// 全体 (Env・Labels・Command・マウントのパスなど) は保持しない。
export type PatchRow = {
  // 先頭 7 桁 (csb が返す 64 桁の sha256 が形式どおりでなければ "?")
  sha: string;
  // パッチを解析できなかったときの理由。通常は null
  error: string | null;
  files: number;
  added: number;
  removed: number;
  // csb の verify_status: PASS / FAIL / MISSING / STALE (未知の値はそのまま)
  verify: string;
  // "<reviewer>=<status>" の並び (status: pass / fail / unresolved / stale / missing)
  reviews: string[];
};

export type NetState = "on" | "expired" | "nowindow" | "off" | "unknown";

export type SandboxRow = {
  // 12 桁の 16 進 ID
  id: string;
  // meta を読めなかったときの理由。通常は null
  broken: string | null;
  // docker のラベル dev.claude.sandbox.role から (work / setup / verify)。
  // null: 不明 (docker ps を読めない・出力が途中で切れて見えなかった分があるかもしれない)。
  // 空の配列: docker ps を最後まで読めて、このサンドボックスのコンテナが無かった
  roles: string[] | null;
  // csb の container (docker の State.Status、引けなければ "?")
  state: string;
  isRunning: boolean;
  // ネットワークを繋いでいる期限 (epoch ミリ秒)。期限内でなければ null
  netUntilMs: number | null;
  // 実際の接続 (csb の net_connected / net_expired から)。on: 期限内で接続中、
  // expired: 期限を過ぎても接続中 (次の csb の呼び出しで切断)、nowindow: 接続中で
  // 期限の記録なし、off: 切断、unknown: 確かめられない (docker に届かない・照合に
  // 通らない)。net_connected の無い古い csb では net_on_until から on / off を決める
  net: NetState;
  // ~ 相対
  repo: string;
  // 照合が ok 以外のときの理由。ok なら null
  check: string | null;
  // このサンドボックスのコンテナに bind mount がある (サンドボックスではあってはならない)。
  // true: 見えた。false: docker ps を最後まで読めて、無かった。
  // null: 不明 (docker ps を読めない・出力が途中で切れて、見えた分には無かった)。
  // 「無い」に見える安全の指標にしないため、取れなかったときを false にしない
  hasBind: boolean | null;
  // csb の並び (sha 順)。最新かどうかを判る材料が csb の出力に無いので全件を持つ
  patches: PatchRow[];
};

export type ContainerRow = {
  // 一覧の key 用 (docker の ID)
  id: string;
  name: string;
  image: string;
  status: string;
  isRunning: boolean;
  // Mounts に bind mount (ホストのパス) がある
  hasBind: boolean;
  // csb のラベルを持つが、csb の状態と結びつかないコンテナ
  isSandboxLabeled: boolean;
};

export type ComposeRow = {
  name: string;
  status: string;
  // 最初の設定ファイル (~ 相対)
  file: string;
  // 2 つ目以降の設定ファイルの数
  moreFiles: number;
};

// rows が空で notice があれば「取れなかった」、rows も notice も空なら「無い」。
// hidden は上限で出さなかった件数。
export type Section<T> = {
  rows: T[];
  notice: string | null;
  hidden: number;
};

export type Snapshot = {
  // 取得した時刻 (epoch ミリ秒)
  at: number;
  sandboxes: Section<SandboxRow>;
  containers: Section<ContainerRow>;
  compose: Section<ComposeRow>;
};

declare module "claude-code" {
  interface PluginState {
    containers: {
      // null: まだ一度も読んでいない
      snapshot: Shaped<Snapshot | null>;
    };
  }
}
