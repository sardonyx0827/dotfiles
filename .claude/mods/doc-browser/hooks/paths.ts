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
// blockRemote は、信頼できない内容から書かれた文書 (codebase-survey の調査) を
// 描くとき: 手元のファイル以外からの読み込みを CSP で止めさせる。
export function renderArgv(
  renderer: string,
  outDir: string,
  markdown: string,
  options: { blockRemote?: boolean } = {},
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
    ...(options.blockRemote === true ? ["--block-remote"] : []),
    "--",
    markdown,
  ];
}

// render_report.py --block-remote が書くページの先頭: <meta charset> の直後に CSP
// を置く (meta の CSP はそれより後に読み込まれるものにしか効かない)。緩い CSP、
// コメントの中の meta、後ろに置かれた meta を通さないよう、renderer の出力と
// 1 文字違わず一致するかで判定する。この 2 つの値は tests/test_render_report.py が
// render_report.py の出力と突き合わせて固定している。
// reports の HTML は renderer しか書かない前提なので、これはフラグの付け忘れや
// 旧版の HTML を見つける検査であり、手書きの HTML の偽造は防がない。
export const RENDERED_HEAD =
  '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n';
export const BLOCK_REMOTE_CSP_META = `<meta http-equiv="Content-Security-Policy" content="img-src file: data:; font-src file: data:; style-src 'unsafe-inline' file:; media-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'none'; script-src-attr 'none'">`;

export function hasBlockRemoteCsp(html: string): boolean {
  return html.startsWith(`${RENDERED_HEAD}${BLOCK_REMOTE_CSP_META}\n`);
}

export function openArgv(opener: string, absolute: string): string[] {
  if (!absolute.startsWith("/"))
    throw new Error(`not an absolute path: ${absolute}`);

  return [opener, absolute];
}

export function openerFor(uname: string): "open" | "xdg-open" {
  return uname.trim() === "Darwin" ? "open" : "xdg-open";
}

// systemd のユーザーユニットに引き継ぐ変数。xdg-open とブラウザは、どの画面に出すか
// (DISPLAY / WAYLAND_DISPLAY / XAUTHORITY / XDG_SESSION_TYPE) と既定のブラウザ
// (BROWSER / XDG_CURRENT_DESKTOP) をここから決めるが、ユーザーマネージャーの
// 環境には入っていないことが多い。
export const DETACHED_ENV = [
  "DISPLAY",
  "WAYLAND_DISPLAY",
  "XAUTHORITY",
  "XDG_SESSION_TYPE",
  "XDG_CURRENT_DESKTOP",
  "BROWSER",
] as const;

export type DetachedEnv = Readonly<Partial<Record<string, string | undefined>>>;

// 開くときに試す argv を、試す順に返す。xdg-open は既定のブラウザが終わるまで
// 戻らないことがあり (起動していない Firefox など)、$.process.run は打ち切りの
// ときにブラウザごと止めてしまう。そこで systemd のユーザーユニットに起動させて
// 待たない形を先に試し、systemd-run が無い・使えない環境では xdg-open を直接呼ぶ。
// --wait / --pipe / --scope は付けない (どれも終わるまで待つ形に戻る)。
// 変数は値まで明示して渡す: -E NAME だけだと、未設定の変数が空の値で設定される。
// --expand-environment=no: systemd は既定でコマンドの引数の ${VAR} と $$ を展開し、
// "$" を含むファイル名だと検証したのと別のパスが渡る。これを知らない古い systemd
// (254 より前) では systemd-run が失敗し、xdg-open を直接呼ぶ形に落ちる。
// 画面の変数がどちらも無い (headless や SSH) ときは切り離さない: ユニットの起動だけで
// 成功と見なすと、中の xdg-open が失敗しても「開きました」になってしまう。
export function openCandidates(
  opener: "open" | "xdg-open",
  absolute: string,
  env: DetachedEnv,
): string[][] {
  const direct = openArgv(opener, absolute);
  if (opener !== "xdg-open") return [direct];

  const isSet = (value: string | undefined): value is string =>
    value !== undefined && value !== "";
  if (!isSet(env.DISPLAY) && !isSet(env.WAYLAND_DISPLAY)) return [direct];

  const setenv = DETACHED_ENV.flatMap((name) => {
    const value = env[name];

    return isSet(value) ? ["-E", `${name}=${value}`] : [];
  });

  return [
    [
      "systemd-run",
      "--user",
      "--collect",
      "--quiet",
      "--expand-environment=no",
      ...setenv,
      ...direct,
    ],
    direct,
  ];
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
