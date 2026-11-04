// doc-browser の $.state 契約。
// 一覧の項目は「どの根の下の、どの相対パスか」だけを持ち、開く直前に realPath で
// 検査し直す。描画時に取り込んだ絶対パスは信用しない。

// docs: 先頭がドットでないディレクトリ (README・docs/ など)
// config: .claude/ .codex/ .github/ など先頭がドットのディレクトリ
// reports: ~/.claude/reports/<project>/ の session-report と session-dossier
export type DocTab = "docs" | "config" | "reports";

// report: session-report (md と html を 1 行に束ねる)
// dossier: session-dossier の詳細ページ / dossier-share: その共有版
export type DocKind = "doc" | "report" | "dossier" | "dossier-share";

export type DocItem = {
  // "doc:<rel>" | "rep:<rel>"
  id: string;
  kind: DocKind;
  tab: DocTab;
  // 無害化済みの表示文字列 (detail は dim で添える)
  label: string;
  detail: string;
  // rel を解決する根。開くときに realPath がこの根の下にあることを確かめる。
  root: "repo" | "reports";
  // 根からの相対パス ("/" 区切り、".." を含まない)。reports は開くファイル。
  rel: string;
  // reports の並べ替え用 "YYYYMMDD-HHMMSS"
  stamp?: string;
};

export type DocView = {
  tab: DocTab;
  // 0 始まり
  page: number;
  // reports タブで全プロジェクトを出すか
  allProjects: boolean;
  // 大文字小文字を区別しない AND の部分一致 (パスに対して)
  filter: string[];
};

export type DocContext = {
  // git の toplevel (realPath)。リポジトリ外なら null
  repoRoot: string | null;
  // toplevel の basename、リポジトリ外なら "no-repo"
  project: string;
  // ~/.claude/reports (CLAUDE_CONFIG_DIR を尊重)
  reportsRoot: string;
  // Markdown のプレビュー HTML の置き場 (リポジトリの外)
  previewRoot: string;
  // session-report の renderer
  renderer: string;
};

export type DocStatus = { kind: "info" | "error"; text: string };

declare module "claude-code" {
  interface PluginState {
    "doc-browser": {
      items: DocItem[];
      view: DocView;
      context: DocContext | null;
      status: DocStatus | null;
      // ペインを置けない場所で本文に返した id の並び (/doc-browser open N 用)
      lastListing: string[];
    };
  }
}
