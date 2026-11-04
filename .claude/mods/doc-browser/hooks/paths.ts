// パスと argv の組み立て (純粋)。開く対象を決めるのは register.tsx だが、何を
// 安全とみなすかはここで決める: 相対パスは根の下にとどまる形だけを受け付け、
// 外部コマンドにはシェルを通さず argv で、絶対パスだけを渡す。

// 根からの相対パスとして受け付ける形。絶対パス・".."・空の区切り・NUL・
// バックスラッシュは拒否する (git ls-files の出力やファイル名は信用しない)。
export function isSafeRel(rel: string): boolean {
  if (rel === "" || rel.startsWith("/")) return false;
  // NUL・改行などの制御文字は、引数や出力の行を分断できるので受け付けない。
  if (/[\u0000-\u001f\u007f\\]/.test(rel)) return false;

  return rel
    .split("/")
    .every((seg) => seg !== "" && seg !== "." && seg !== "..");
}

// path が root の真下にあるか。"/r2" を "/r" の内側と見なす前方一致の罠を避ける。
export function isInside(path: string, root: string): boolean {
  const prefix = root.endsWith("/") ? root : `${root}/`;

  return path.startsWith(prefix) && path.length > prefix.length;
}

export function joinPath(root: string, rel: string): string {
  return `${root.replace(/\/+$/, "")}/${rel}`;
}

// プレビューの出力先。リポジトリの構造を写すので、別の場所にある同名の
// README.md どうしはぶつからない。
export function previewDir(
  previewRoot: string,
  project: string,
  rel: string,
): string {
  const slash = rel.lastIndexOf("/");
  const base = joinPath(previewRoot, project);

  return slash === -1 ? base : joinPath(base, rel.slice(0, slash));
}

// session-report の renderer で Markdown を HTML にする。ブラウザは開かせず
// (開くのはこちらで検査してから)、"--" の後に md を置いて引数と取り違えない。
export function renderArgv(
  renderer: string,
  outDir: string,
  markdown: string,
): string[] {
  const base = markdown.slice(0, Math.max(1, markdown.lastIndexOf("/")));

  return [
    "python3",
    renderer,
    "--no-open",
    "--out",
    outDir,
    // 相対パスの画像やリンク (README の assets/*.svg) を元の場所で解決させる。
    "--base-dir",
    base,
    "--",
    markdown,
  ];
}

export function openArgv(opener: string, absolute: string): string[] {
  if (!absolute.startsWith("/"))
    throw new Error(`not an absolute path: ${absolute}`);

  return [opener, absolute];
}

export function openerFor(uname: string): "open" | "xdg-open" {
  return uname.trim() === "Darwin" ? "open" : "xdg-open";
}

export function extensionOf(path: string): string {
  const base = path.slice(path.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");

  return dot <= 0 ? "" : base.slice(dot).toLowerCase();
}

// 出力の最後の空でない行 (renderer の stderr の要約に使う)。
export function lastLine(text: string): string {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");

  return lines[lines.length - 1] ?? "";
}
