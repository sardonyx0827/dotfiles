// bash-review-log の $.state 契約。
// サマリーログ 1 行 (複数行コマンドは継続行込み) を 1 エントリとして持つ。
export type BashReviewLogEntry = {
  // "YYYY-MM-DD HH:MM:SS"
  at: string;
  // ALLOW / ASK / DENY (未知の値もそのまま保持する)
  decision: string;
  // pre / secret / highrisk / gemini / codex / fallback
  stage: string;
  // サマリーログのまま (80 字を超えると先頭 80 字 + "...")
  command: string;
  reason: string;
  // 詳細ログから引いた切られる前の全文と、Bash ツールの description
  fullCommand?: string;
  description?: string;
};

// ERROR は判定ではなく「Gemini か Codex が応答できなかった」(reason に *=ERROR)
export type BashReviewLogType = "ALLOW" | "ASK" | "DENY" | "ERROR";

export type BashReviewLogView = {
  // 空なら全種別
  types: BashReviewLogType[];
  // ペインに描く最大件数
  limit: number;
  // true: コマンドを折り返して全文 / false: 1 行に切り詰める
  isFull: boolean;
};

declare module "claude-code" {
  interface PluginState {
    "bash-review-log": {
      entries: BashReviewLogEntry[];
      view: BashReviewLogView;
      error: string | null;
    };
  }
}
