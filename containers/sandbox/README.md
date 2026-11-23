# containers/sandbox

`csb` (`.claude/hooks/claude_sandbox.py`) の汎用イメージ。`csb image build --generic` が
この Dockerfile を `claude-sandbox-generic:1` (`_sandbox_gate.GENERIC_IMAGE_REF`) として
ビルドする。`csb create --image generic` と、devcontainer 定義が無い・使えないときの
`csb create` がこのイメージを使う。ただし、devcontainer 定義の Dockerfile が build
context の外にあるときは、このイメージに切り替えずに create を拒否する (理由は create の
要約に出る)。

この Dockerfile には `# syntax=` の行を置かない。取ってくるイメージはすべて digest で
固定する方針なので、固定していない frontend のイメージは取らず、ビルドする docker に
組み込みの frontend を使う。

## 中身

- debian:trixie-slim、git、python3 (pip / venv / dev、`python` は python3)、build-essential、procps
- Node.js 24 LTS (npm / npx / corepack)、uv。いずれも公式イメージから `COPY --from` で入れる
- yarn / pnpm は corepack の shim。プロジェクトが `packageManager` で指定した版を setup
  (ネットワーク接続中) に取得する。go / cargo は入れない (それらのプロジェクトは
  devcontainer 定義か `--image` を使う)
- uid / gid 1000 のユーザー `sandbox` (HOME は `/home/sandbox`)、その所有の `/work`
- `VOLUME` / `ENTRYPOINT` / `HEALTHCHECK` / `dev.claude.sandbox*` ラベルは置かない
  (理由は Dockerfile 冒頭のコメント)

## digest の更新手順

1. 各ベースの multi-arch index の digest を調べる (アーキテクチャ別の manifest の digest
   ではない)。

   ```sh
   docker buildx imagetools inspect debian:trixie-slim | sed -n 3p
   docker buildx imagetools inspect node:<版>-trixie-slim | sed -n 3p
   docker buildx imagetools inspect ghcr.io/astral-sh/uv:<版> | sed -n 3p
   ```

2. Dockerfile の `FROM` を `タグ@sha256:…` の形で書き換える。node と uv はタグにも版を
   書く (`node:24.x.y-trixie-slim`、`uv:0.x.y`)。
3. `csb image build --generic` でビルドし、`docker run --rm --network none
claude-sandbox-generic:1 sh -c 'node -v; uv --version; python3 --version; id'` で確かめる。
4. `GENERIC_IMAGE_REF` のタグは上げなくてよい。既存のサンドボックスは setup イメージを ID
   で持つので、汎用イメージを作り直しても影響を受けない。
