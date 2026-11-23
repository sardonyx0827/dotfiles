#!/usr/bin/env python3
"""csb: コンテナサンドボックスの CLI (`~/.local/bin/csb` → このファイル)。

リポジトリのスナップショットを使い捨てのコンテナへ写し、そこで作業・検証して、成果を
git パッチとしてホストへ戻す。コンテナはホストのファイルに一切触れない (マウント無し)。

    csb create [--image auto|generic|REF] [--setup CMD]
    csb exec ID [-w ABS] [-e NAME=VAL]… -- ARGV…
    csb net on ID [--minutes N] / csb net off ID
    csb export ID
    csb verify ID --patch SHA -- ARGV…
    csb attest ID --patch SHA --review NAME=pass|fail [--ref PATH] [--unresolved N]
    csb preflight ID --patch SHA
    csb apply ID --patch SHA
    csb ls [--json] / csb status [ID] [--json]
    csb rm ID / csb gc [--older-than Nd] / csb image build --generic

契約 (プロファイル・照合・argv の文法・パッチの解析・apply の条件) は全て
_sandbox_gate にある。このファイルはそれを実行に移すだけで、判定を独自に足さない。

- docker の呼び出しは全て _docker() を通る。docker 本体の絶対パスと endpoint
  (unix ソケット) は create の時に meta に固定し、以後は `-H <endpoint>` と、
  _host_env (allowlist と root 所有のディレクトリだけの固定の PATH。docker 本体の
  ディレクトリを後ろに足す) から DOCKER_HOST / DOCKER_CONTEXT / DOCKER_CONFIG /
  DOCKER_TLS* / DOCKER_CERT_PATH を除いた環境で呼ぶ。
- ホストの git は root 所有の /usr/bin/git を _host_env の環境で呼ぶ (_git)。
- exec は ロック → meta → 完全 ID での inspect → verify_work_container → 完全 ID への
  `docker exec -u <uid:gid>` の順に進み、照合に落ちたら実行しない (exit 2)。
- 終了コード: 0 成功、1 実行時のエラー、2 拒否 (引数・契約・照合の不一致)。exec と
  verify はコンテナ内のコマンドの終了コードをそのまま返す。
"""

from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import io
import json
import os
import posixpath
import re
import secrets
import shutil
import stat

# docker と git は引数リストで呼ぶ (shell は使わない)。
import subprocess  # nosec B404
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from typing import IO, Any, Callable, Iterable, Mapping, Sequence, Union

_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _sandbox_gate as gate  # noqa: E402

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
DOCKER_TIMEOUT = 60.0
GIT_TIMEOUT = 300.0
# 秘密スキャンを掛けるブロブの上限。超えたものは件数だけ注意に出す。
SCAN_MAX_BLOB = 2 * 1024 * 1024
# create_plan が中身を読むファイルの上限 (lockfile などは有無しか見ない)。
PLAN_READ_MAX = 4 * 1024 * 1024
PATCH_MAX_BYTES = 64 * 1024 * 1024
REF_MAX_BYTES = 16 * 1024 * 1024
GC_DEFAULT_DAYS = 7
DEFAULT_UID = 1000
DEFAULT_GID = 1000

WORKDIR = gate.CONTAINER_WORKDIR
# setup / verify コンテナへ起動前に投入する置き場。/tmp は起動時に tmpfs が被さって
# 中身が隠れるので使わない。所有者はコンテナの uid。
STAGE = "/csb"
BUNDLE_IN_CONTAINER = STAGE + "/snap.bundle"
PATCH_IN_CONTAINER = STAGE + "/patch.diff"
EXPORT_INDEX = WORKDIR + "/.git/csb-export.index"
SNAP_BRANCH = "snap"

_LFS_POINTER = b"version https://git-lfs.github.com/spec/"
_SNAP_IDENT = {
    "GIT_AUTHOR_NAME": "csb",
    "GIT_AUTHOR_EMAIL": "csb@sandbox.invalid",
    "GIT_AUTHOR_DATE": "@1 +0000",
    "GIT_COMMITTER_NAME": "csb",
    "GIT_COMMITTER_EMAIL": "csb@sandbox.invalid",
    "GIT_COMMITTER_DATE": "@1 +0000",
}
# ホストで起動する子プロセス (git・docker) の環境。os.environ から引き継ぐのは下の
# 名前と LC_* だけ (allowlist)。PATH は root 所有のディレクトリだけの固定の値にする。
# 落とすものの例: GIT_EXEC_PATH / GIT_SSH* / GIT_CONFIG_COUNT (別の補助コマンドや設定を
# 差し込める)、DYLD_* / LD_*、xcrun の shim (/usr/bin/git) が従う DEVELOPER_DIR /
# TOOLCHAINS / SDKROOT、GIT_DIR などのリポジトリの場所。HOME と XDG_CONFIG_HOME は残す
# (残さないと core.attributesFile の既定 ($XDG_CONFIG_HOME/git/attributes) を
# check-attr が読まず、filter の検査が global の出所を見落とす)。GIT_CONFIG_GLOBAL /
# GIT_CONFIG_SYSTEM / GIT_CONFIG_NOSYSTEM は設定ファイルの場所で、~/.gitconfig と同じく
# ユーザーが書ける出所なので残す (設定は _HOST_GIT_CONFIG の -c が上書きする)。
HOST_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_HOST_ENV_KEEP = frozenset(
    {
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
    }
)


def _host_env(path: str = HOST_PATH) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k in _HOST_ENV_KEEP or k.startswith("LC_")
    }
    env["PATH"] = path
    return env


# コンテナ内の git の設定のうち、出力の形を変えるものを打ち消す。コンテナの中の
# .git/config はサンドボックスのコードが書き換えられる。
_CONTAINER_GIT = (
    "git",
    "-c",
    "core.quotePath=true",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "diff.noprefix=false",
    "-c",
    "diff.mnemonicPrefix=false",
    "-c",
    "diff.relative=false",
    "-c",
    "diff.ignoreSubmodules=none",
    "-C",
    WORKDIR,
)
_EXPORT_DIFF = (
    "diff",
    "--cached",
    "--no-renames",
    "--binary",
    "--full-index",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)

USAGE = """usage: csb <subcommand> …
  create [--image auto|generic|REF] [--setup CMD]   スナップショットからサンドボックスを作る
  exec ID [-w ABS] [-e NAME=VAL]… -- ARGV…          work コンテナで実行する (照合付き)
  net on ID [--minutes N] | net off ID              work コンテナのネットワーク接続
  export ID                                         変更をパッチにする (sha を出力)
  verify ID --patch SHA -- ARGV…                    使い捨てコンテナでパッチを検証する
  attest ID --patch SHA --review NAME=pass|fail [--ref PATH] [--unresolved N]
  preflight ID --patch SHA                          apply の要約と拒否理由を表示する
  apply ID --patch SHA                              パッチをホストのリポジトリへ反映する
  ls [--json] | status [ID] [--json]
  rm ID | gc [--older-than Nd] | image build --generic"""


class SandboxRefused(Exception):
    """契約・照合・前提の不一致。exit 2。"""


class SandboxError(Exception):
    """docker / git / ファイルの実行時エラー。exit 1。"""


# ---------------------------------------------------------------------------
# テストで差し替える境界
# ---------------------------------------------------------------------------
def _state_root() -> str:
    return gate.state_dir()


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return secrets.token_hex(6)


def _isatty(fd: int) -> bool:
    return os.isatty(fd)


def _generic_context() -> str:
    """汎用イメージの build context (リポジトリの containers/sandbox)。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(_HERE)), "containers", "sandbox"
    )


def _out(text: str = "") -> None:
    print(text, flush=True)


def _err(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _shown(data: bytes, limit: int = 300) -> str:
    return gate.display_text(data.decode("utf-8", "replace").strip(), limit)


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Endpoint:
    """固定した docker 本体と endpoint。host が "" のときは -H を付けない (context の調査用)。"""

    bin: str
    host: str


@dataclass(frozen=True)
class DockerResult:
    returncode: int
    stdout: bytes
    stderr: bytes


_DOCKER_ENV_DROP = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
)


def _docker_env(binary: str) -> dict[str, str]:
    """docker の環境。_host_env (allowlist と固定の PATH) に、固定した docker 本体の
    ディレクトリを PATH の後ろに足す (Docker.app の credential helper はそこにある)。
    docker 本体と同じ信頼なので、穴は広がらない (README「残る穴」)。"""
    env = _host_env(f"{HOST_PATH}:{posixpath.dirname(binary)}")
    return {
        k: v
        for k, v in env.items()
        if k not in _DOCKER_ENV_DROP and not k.startswith("DOCKER_TLS")
    }


def _endpoint(target: Endpoint | Mapping[str, Any]) -> Endpoint:
    """meta の docker と endpoint。meta から作るたびに、固定した docker を確かめ直す。"""
    if isinstance(target, Endpoint):
        return target
    binary = str(target["docker_bin"])
    _check_pinned_docker(binary)
    return Endpoint(binary, str(target["docker_host"]))


def _docker(
    target: Endpoint | Mapping[str, Any],
    args: Sequence[str],
    *,
    input: bytes | None = None,  # noqa: A002 - subprocess と同じ名前
    input_path: str | None = None,
    capture: bool = True,
    timeout: float | None = DOCKER_TIMEOUT,
    on_spawn: Callable[[], None] | None = None,
    output_to_stderr: bool = False,
    user_config: bool = False,
) -> DockerResult:
    """docker を 1 回呼ぶ唯一の関数。

    capture が偽なら stdout / stderr を引き継ぐ (output_to_stderr なら stdout も
    stderr へ流す)。on_spawn はプロセスの起動直後に呼ぶ (exec がロックを外すため)。
    user_config が偽なら (既定) `--config <新しい空のディレクトリ>` を付け、~/.docker
    (config.json・cli-plugins。Bash から確認なしで書ける) を読ませない。ディレクトリは
    呼び出しごとに状態ディレクトリの下に作り、終わったら消す (_fresh_docker_config)。
    ユーザーの設定を使うのは、人が承認した create の endpoint の固定・pull・build だけ
    (credential helper のため)。
    """
    ep = _endpoint(target)
    stdin: Union[int, IO[bytes], None]
    handle: IO[bytes] | None = None
    config_dir: str | None = None
    stdout: Union[int, None] = (
        subprocess.PIPE if capture else (2 if output_to_stderr else None)
    )
    stderr: Union[int, None] = subprocess.PIPE if capture else None
    try:
        if not user_config:
            config_dir = _fresh_docker_config()
        config = [] if config_dir is None else ["--config", config_dir]
        argv = [ep.bin, *config] + (["-H", ep.host] if ep.host else []) + list(args)
        if input_path is not None:
            handle = open(input_path, "rb")  # noqa: SIM115 - finally で閉じる
            stdin = handle
        elif input is not None:
            stdin = subprocess.PIPE
        else:
            stdin = subprocess.DEVNULL if capture else None
        try:
            proc = subprocess.Popen(  # nosec B603
                argv,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=_docker_env(ep.bin),
            )
        except OSError as exc:
            return DockerResult(127, b"", str(exc).encode())
        if on_spawn is not None:
            on_spawn()
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return DockerResult(124, out or b"", (err or b"") + b"\ncsb: timeout")
        return DockerResult(proc.returncode, out or b"", err or b"")
    finally:
        if handle is not None:
            handle.close()
        if config_dir is not None:
            _remove_docker_config(config_dir)


def _docker_ok(
    target: Endpoint | Mapping[str, Any], args: Sequence[str], **kw: Any
) -> bytes:
    res = _docker(target, args, **kw)
    if res.returncode != 0:
        what = " ".join(args[:2])
        raise SandboxError(f"docker {what} が失敗した: {_shown(res.stderr)}")
    return res.stdout


# docker の実体。引き継いだ PATH (~/.cargo/bin などが前にあることがある) は
# 使わず、固定の場所の一覧から、最初に在るものを選ぶ (在るのに検査に落ちたら次へは
# 進まず拒否する)。realpath は既知の root の下で、名前は docker。辿る途中のディレクトリ・
# symlink・行き着いた先は trusted_path を満たす (owners は root と自分: Docker.app と
# Homebrew の prefix は自分の所有。macOS では admin の group が書けるものも通す)。
# 自分の所有の Docker.app や Homebrew の docker は、同じ uid なら差し替えられる
# (README「残る穴」)。
_DOCKER_TRUST_TOP = "/"


def _docker_candidates() -> tuple[str, ...]:
    """docker を探す固定のディレクトリ (この順)。HOST_PATH の後に既知の場所。"""
    host = tuple(HOST_PATH.split(":"))
    if sys.platform == "darwin":
        return host + (
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/Applications/Docker.app/Contents/Resources/bin",
        )
    return host + ("/usr/local/bin",)


def _docker_roots() -> tuple[str, ...]:
    """docker の realpath が入っていてよい場所 (末尾は /)。"""
    if sys.platform == "darwin":
        return (
            "/usr/",
            "/bin/",
            "/sbin/",
            "/opt/homebrew/",
            "/Applications/Docker.app/",
        )
    return ("/usr/", "/bin/", "/sbin/")


def _docker_write_groups() -> tuple[int, ...]:
    """group が書けても構わない gid。macOS の admin (/Applications と Homebrew)。"""
    if sys.platform != "darwin":
        return ()
    try:
        import grp

        return (grp.getgrnam("admin").gr_gid,)
    except (ImportError, KeyError):
        return ()


# Docker.app の中の docker は、同じ uid なら中身を差し替えられる。そこで
# Docker Inc の署名 (Apple が発行した Developer ID、Team ID 9BNSXJN65R) を codesign で
# 確かめる。create の時にも、固定した docker を使うたびにも確かめる。Homebrew の docker
# (Docker 社の署名ではない。prefix への書き込みは確認になる) と Linux の候補 (root の
# 下) は対象外。結果は同じプロセスの中だけで、ファイルの状態ごとに覚える。
CODESIGN = "/usr/bin/codesign"
DOCKER_TEAM_ID = "9BNSXJN65R"
DOCKER_SIGNATURE = f"Docker Inc ({DOCKER_TEAM_ID}) 確認済み"
# identifier も固定する: Docker.app の bin には、同じ Docker Inc の署名の別の
# バイナリ (kubectl・docker-credential-desktop など) があり、それを docker という名前に
# した写しも Team ID だけなら通る。本物の docker の Identifier は "docker"。
_DOCKER_TEAM_REQUIREMENT = (
    f'anchor apple generic and certificate leaf[subject.OU] = "{DOCKER_TEAM_ID}"'
)
_DOCKER_REQUIREMENT = _DOCKER_TEAM_REQUIREMENT + ' and identifier "docker"'
# identifier だけが合わないときの文面 (Docker Desktop の更新で identifier が変わったか、
# Docker の署名の別のバイナリの写し)。人が `codesign -dv` で確かめ、正しければ
# _DOCKER_REQUIREMENT を直す (README)。
DOCKER_IDENTIFIER_MISMATCH = (
    "Docker の署名だが identifier が docker でない "
    "(Docker Desktop の更新で変わった可能性。README を参照)"
)
CODESIGN_TIMEOUT = 10.0
_SIGNED: set[tuple[Any, ...]] = set()


def _docker_signed_roots() -> tuple[str, ...]:
    """署名を確かめる docker の場所 (末尾は /)。"""
    return ("/Applications/Docker.app/",) if sys.platform == "darwin" else ()


def _run_codesign(path: str) -> str:
    """codesign で Docker Inc の署名 (identifier "docker") を確かめる。満たせば ""、
    満たさなければ理由。

    落ちたときに Team ID だけの要件で確かめ直して文面を分ける (identifier だけが違うのか、
    Docker の署名ですらないのか) のは _codesign_verify。どちらでも拒否は変わらない。
    """
    result = _codesign_verify(path, _DOCKER_REQUIREMENT)
    return "" if result is True else str(result)


def _codesign_verify(path: str, requirement: str, *, retry: bool = True) -> Any:
    """要件を満たせば True、満たさなければ理由の文字列。"""
    try:
        res = subprocess.run(  # nosec B603 - 信頼を確かめた固定のパス
            [CODESIGN, "--verify", "--strict", f"-R={requirement}", path],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env={"PATH": HOST_PATH},
            timeout=CODESIGN_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "署名の確認が時間切れ"
    except OSError as exc:
        return f"codesign を起動できない ({exc.strerror})"
    if res.returncode == 0:
        return True
    if retry and requirement == _DOCKER_REQUIREMENT:
        if _codesign_verify(path, _DOCKER_TEAM_REQUIREMENT, retry=False) is True:
            return DOCKER_IDENTIFIER_MISMATCH
    return f"Docker Inc ({DOCKER_TEAM_ID}) の署名でない"


def _codesign_problem(path: str) -> str:
    """path の Docker Inc の署名。codesign 自体も root の所有を確かめる。"""
    reason = gate.trusted_path(CODESIGN)
    if reason:
        return f"codesign を信頼できない ({reason})"
    try:
        st = os.stat(path)
    except OSError as exc:
        return f"調べられない ({exc.strerror})"
    key = (path, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if key in _SIGNED:
        return ""
    problem = _run_codesign(path)
    if not problem:
        _SIGNED.add(key)
    return problem


def _docker_signature_text(path: str) -> str:
    """要約の署名の欄 (path は検査を通った realpath)。"""
    if any(path.startswith(root) for root in _docker_signed_roots()):
        return DOCKER_SIGNATURE
    return "確かめない (Docker.app の外)"


def _docker_problem(path: str) -> str:
    """path (realpath) が docker として信頼できるか。できれば ""。"""
    if posixpath.basename(path) != "docker":
        return "名前が docker でない"
    if not any(path.startswith(root) for root in _docker_roots()):
        return "既知の場所の外"
    problem = gate.trusted_path(
        path,
        owners=(0, os.geteuid()),
        top=_DOCKER_TRUST_TOP,
        write_groups=_docker_write_groups(),
    )
    if problem or not any(path.startswith(r) for r in _docker_signed_roots()):
        return problem
    return _codesign_problem(path)


def _find_docker_binary() -> str:
    """固定の場所の最初の docker を realpath で固定する (理由は上のコメント)。"""
    places = _docker_candidates()
    for directory in places:
        found = posixpath.join(directory, "docker")
        if not os.path.lexists(found):
            continue
        real = os.path.realpath(found)
        problem = gate.trusted_path(
            found,
            owners=(0, os.geteuid()),
            top=_DOCKER_TRUST_TOP,
            write_groups=_docker_write_groups(),
        ) or _docker_problem(real)
        if problem:
            raise SandboxRefused(
                f"docker の実体が不正: {gate.display_text(found)} -> "
                f"{gate.display_text(real)} ({problem})"
            )
        return real
    raise SandboxRefused(f"docker が見つからない (固定の場所: {', '.join(places)})")


def _check_pinned_docker(path: str) -> None:
    """meta に固定した docker が、まだ同じ実体 (realpath) で検査を満たすか。"""
    problem = (
        "realpath が変わった"
        if not path.startswith("/") or os.path.realpath(path) != path
        else _docker_problem(path)
    )
    if problem:
        raise SandboxRefused(
            f"固定した docker を使えない: {gate.display_text(path)} ({problem})"
        )


def _owner_name(path: str) -> str:
    """path の所有者の名前 (引けなければ uid、調べられなければ "不明")。"""
    try:
        uid = os.lstat(path).st_uid
    except OSError:
        return "不明"
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def _fresh_docker_config() -> str:
    """docker の --config に渡す、新しい空の 0700 のディレクトリ (呼び出しごとに作る)。

    親は状態ディレクトリの下の docker-config.d で、今と同じく本物のディレクトリ・自分の
    所有・0700 を確かめる (状態ディレクトリは Edit が deny、Bash の書き込みはハーネスの
    パスとして確認になる)。外から置かれたものは読まず、docker が書いたもの (buildx の
    状態など) も次の呼び出しに残らない。
    """
    root = _state_root()
    _ensure_private_dir(root)
    parent = os.path.join(root, "docker-config.d")
    _ensure_private_dir(parent)
    return tempfile.mkdtemp(prefix="cfg-", dir=parent)


def _remove_docker_config(path: str) -> None:
    """_fresh_docker_config のディレクトリを消す。symlink は辿らない。"""
    try:
        if os.path.islink(path):
            os.unlink(path)
        else:
            shutil.rmtree(path)
    except OSError:
        pass  # 消せなくても判定は変えない (次の呼び出しは別のディレクトリを使う)


def _pin_endpoint(*, user_config: bool = False) -> Endpoint:
    """docker 本体と現在の context の endpoint を固定する (unix ソケットのみ)。

    user_config は create だけが真にする (ユーザーの docker context を使う)。ほかは空の
    設定なので既定の context になる。
    """
    binary = _find_docker_binary()
    res = _docker(
        Endpoint(binary, ""),
        ["context", "inspect", "--format", "{{json .Endpoints.docker}}"],
        user_config=user_config,
    )
    if res.returncode != 0:
        raise SandboxRefused(f"docker context を調べられない: {_shown(res.stderr)}")
    try:
        host = json.loads(res.stdout.decode("utf-8"))["Host"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise SandboxRefused("docker context の endpoint を読めない") from None
    probe = {"docker_bin": binary, "docker_host": host}
    if not (
        isinstance(host, str)
        and host.startswith("unix://")
        and _meta_field_ok(probe, "docker_host")
    ):
        raise SandboxRefused(
            "docker の endpoint が unix ソケットではない: "
            + gate.display_text(str(host), 120)
        )
    return Endpoint(binary, host)


def _meta_field_ok(fields: Mapping[str, Any], key: str) -> bool:
    """meta の 1 フィールドを validate_meta と同じ規則で検査する。"""
    sample = {
        "schema": gate.META_SCHEMA,
        "id": "0" * 12,
        "repo_root": "/r",
        "base_commit": "0" * 40,
        "base_tree": "0" * 40,
        "snap_commit": "0" * 40,
        "image": {"source": "generic", "ref": "x", "id": "sha256:" + "0" * 64},
        "setup_image_id": "sha256:" + "0" * 64,
        "work_container_id": "0" * 64,
        "uid": 1,
        "gid": 1,
        "docker_bin": "/usr/bin/docker",
        "docker_host": "unix:///var/run/docker.sock",
        "created_at": 0,
    }
    sample[key] = fields[key]
    try:
        gate.validate_meta(sample)
    except gate.MetaError:
        return False
    return True


def _json_list(data: bytes) -> list[Any]:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SandboxError("docker の出力を JSON として読めない") from None
    if not isinstance(obj, list):
        raise SandboxError("docker の出力が配列ではない")
    return obj


def _inspect_container(ep: Endpoint, cid: str) -> dict:
    res = _docker(ep, ["inspect", "--type", "container", cid])
    if res.returncode != 0:
        raise SandboxError(f"コンテナを inspect できない: {_shown(res.stderr)}")
    items = _json_list(res.stdout)
    if len(items) != 1 or not isinstance(items[0], dict):
        raise SandboxError("inspect の結果が 1 件ではない")
    return items[0]


def _image_info(ep: Endpoint, ref: str) -> dict | None:
    res = _docker(ep, ["image", "inspect", ref])
    if res.returncode != 0:
        return None
    items = _json_list(res.stdout)
    return items[0] if len(items) == 1 and isinstance(items[0], dict) else None


def _image_config(info: Mapping[str, Any]) -> Mapping[str, Any]:
    config = info.get("Config")
    return config if isinstance(config, dict) else {}


def _image_user(info: Mapping[str, Any]) -> tuple[int, int]:
    """イメージの User が数値の非 root ならそれを使い、それ以外は 1000:1000。"""
    user = _image_config(info).get("User")
    m = re.fullmatch(r"([0-9]{1,10})(?::([0-9]{1,10}))?", user or "")
    if m:
        uid = int(m.group(1))
        gid = int(m.group(2)) if m.group(2) else uid
        if 0 < uid <= 2**31 - 1 and 0 < gid <= 2**31 - 1:
            return uid, gid
    return DEFAULT_UID, DEFAULT_GID


def _cexec(
    ep: Endpoint,
    cid: str,
    user: str,
    argv: Sequence[str],
    *,
    env: Iterable[tuple[str, str]] = (),
    capture: bool = True,
    timeout: float | None = GIT_TIMEOUT,
) -> DockerResult:
    args = ["exec", "-u", user, "-w", WORKDIR]
    for name, value in env:
        args += ["-e", f"{name}={value}"]
    return _docker(
        ep,
        args + [cid, *argv],
        capture=capture,
        timeout=timeout,
        output_to_stderr=not capture,
    )


def _cexec_ok(
    ep: Endpoint, cid: str, user: str, argv: Sequence[str], **kw: Any
) -> bytes:
    res = _cexec(ep, cid, user, argv, **kw)
    if res.returncode != 0:
        name = " ".join(
            list(argv[:1]) + [a for a in argv[1:] if not a.startswith("-")][:1]
        )
        raise SandboxError(
            f"コンテナ内の {gate.display_text(name, 60)} が失敗した "
            f"(exit {res.returncode}): {_shown(res.stderr)}"
        )
    return res.stdout


def _create_container(ep: Endpoint, args: Sequence[str]) -> str:
    cid = _docker_ok(ep, args).decode("utf-8", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", cid):
        raise SandboxError("docker create がコンテナ ID を返さない")
    return cid


# ---------------------------------------------------------------------------
# 状態ディレクトリ
# ---------------------------------------------------------------------------
def _ensure_private_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise SandboxRefused(f"状態ディレクトリが不正: {gate.display_text(path)}")
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)


def _write_private(path: str, data: bytes) -> None:
    """0600 で一時ファイルに書いてから置き換える (symlink は辿らない)。"""
    directory = os.path.dirname(path)
    _ensure_private_dir(directory)
    tmp = os.path.join(
        directory, f".{os.path.basename(path)}.{os.getpid()}.{secrets.token_hex(4)}"
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def _write_json(path: str, obj: Any) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_private(path, text.encode("utf-8"))


def _sandbox_ids(root: str) -> list[str]:
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for name in sorted(names):
        if not gate.is_sandbox_id(name):
            continue
        try:
            st = os.lstat(os.path.join(root, name))
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            out.append(name)
    return out


def _require_sandbox_dir(root: str, sid: str) -> str:
    path = gate.sandbox_dir(root, sid)
    try:
        st = os.lstat(path)
    except OSError:
        raise SandboxRefused(f"サンドボックス {sid} が無い") from None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise SandboxRefused(f"サンドボックス {sid} の状態ディレクトリが不正")
    return path


def _load_meta(root: str, sid: str) -> dict:
    try:
        meta = gate.load_meta(gate.meta_path(root, sid))
    except gate.MetaError as exc:
        raise SandboxRefused(f"meta.json を読めない: {exc}") from None
    if meta["id"] != sid:
        raise SandboxRefused("meta.json の id がディレクトリと一致しない")
    return meta


def _write_meta(root: str, meta: Mapping[str, Any]) -> None:
    checked = gate.validate_meta(dict(meta))
    _write_json(gate.meta_path(root, checked["id"]), checked)


class _Lock:
    """サンドボックスごとの flock。CLI どうしの操作を直列にする (コンテナは守らない)。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.fd: int | None = None

    def acquire(self, blocking: bool = True) -> bool:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise SandboxRefused(f"ロックを開けない ({exc.strerror})") from None
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            os.close(fd)
            raise SandboxRefused("ロックファイルが不正")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> _Lock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def _lock(root: str, sid: str, name: str = "lock") -> _Lock:
    return _Lock(os.path.join(_require_sandbox_dir(root, sid), name))


def _read_patch(root: str, sid: str, sha: str) -> bytes:
    try:
        data = gate.read_private_file(gate.patch_path(root, sid, sha), PATCH_MAX_BYTES)
    except gate.PrivateFileError as exc:
        raise SandboxRefused(f"パッチ {sha[:12]} を読めない: {exc}") from None
    if gate.sha256_hex(data) != sha:
        raise SandboxRefused(f"パッチ {sha[:12]} の中身が sha と一致しない")
    return data


def _first_and_more(items: Sequence[str]) -> str:
    """最初の 1 件と、残りの件数 (「X」または「X ほか N 件」)。"""
    first = gate.display_text(items[0], 120)
    return first if len(items) == 1 else f"{first} ほか {len(items) - 1} 件"


def _load_record(path: str) -> Any:
    try:
        return gate.load_private_json(path)
    except gate.PrivateFileError:
        return None


def _hash_file(path: str) -> str | None:
    # O_NONBLOCK: FIFO を開いても止まらない (開いた後に通常のファイルかを確かめる)。
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > REF_MAX_BYTES:
                return None
            return gate.sha256_hex(fh.read(REF_MAX_BYTES + 1))
    except OSError:
        return None


def _load_attests(root: str, sid: str, sha: str) -> tuple[list[Any], list[str]]:
    """attest 記録を読む。ref の中身が変わったもの・名前の食い違うものは除き、注意に出す。"""
    directory = os.path.dirname(gate.attest_record_path(root, sid, sha, "x"))
    records: list[Any] = []
    notes: list[str] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return records, notes
    for name in names:
        reviewer = name[: -len(".json")] if name.endswith(".json") else ""
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", reviewer):
            continue
        rec = _load_record(os.path.join(directory, name))
        if not gate.attest_record_valid(rec, sha) or rec["reviewer"] != reviewer:
            notes.append(f"attest {reviewer}: 記録が不正なため無視する")
            continue
        ref = rec["ref"]
        if ref is not None and _hash_file(ref["path"]) != ref["sha256"]:
            notes.append(
                f"attest {reviewer}: ref ({gate.display_text(ref['path'], 120)}) の"
                "中身が記録時と変わったため失効"
            )
            continue
        records.append(rec)
    return records, notes


# ---------------------------------------------------------------------------
# ホストの git
# ---------------------------------------------------------------------------
# ホストの git は必ずこの設定で呼ぶ。リポジトリの設定がホスト上のコマンドを動かさない
# ようにする: fsmonitor (ls-files などで起動する)、フック (post-index-change・
# reference-transaction など)、転送 (partial clone の lazy fetch を含む)。clean / smudge
# filter はここでは止まらないので、filter を通す操作 (stash create・作業ツリーとの diff・
# 属性付きの git apply) そのものを使わない (host_base_tree と cmd_apply)。
# GIT_OPTIONAL_LOCKS=0 は任意のロックを止めるだけで、git が何も書かなくなるわけではない。
#
# git 本体は root 所有の /usr/bin/git (trusted_path で確かめる)。PATH の git
# (homebrew など、ユーザーが書ける場所) は使わない。macOS の /usr/bin/git は xcrun の
# shim で、developer dir の git を実行する (DEVELOPER_DIR などは _host_env が落とす)。
HOST_GIT_BIN = "/usr/bin/git"
_HOST_GIT_CONFIG = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "protocol.allow=never",
)


def _host_git() -> list[str]:
    """git の argv の頭 (信頼できる /usr/bin/git と -c の組)。満たさなければ拒否。"""
    reason = gate.trusted_path(HOST_GIT_BIN)
    if reason:
        raise SandboxRefused(f"信頼できる git が無い: {HOST_GIT_BIN} ({reason})")
    return [HOST_GIT_BIN, *_HOST_GIT_CONFIG]


def _git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = _host_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.update(extra or {})
    return env


def _git(
    repo: str,
    args: Sequence[str],
    *,
    input: bytes | None = None,  # noqa: A002
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    # argv は _host_git() (/usr/bin/git と -c の組) で始まる。shell は使わない。
    return subprocess.run(  # nosec B603
        [*_host_git(), "-C", repo, *args],
        input=input,
        capture_output=True,
        env=_git_env(env),
        timeout=GIT_TIMEOUT,
        check=False,
    )


def _git_ok(repo: str, args: Sequence[str], **kw: Any) -> bytes:
    res = _git(repo, args, **kw)
    if res.returncode != 0:
        raise SandboxError(f"git {args[0]} が失敗した: {_shown(res.stderr)}")
    return res.stdout


def _repo_root(cwd: str) -> str:
    res = _git(cwd, ["rev-parse", "--is-bare-repository", "--show-toplevel"])
    lines = res.stdout.decode("utf-8", "surrogateescape").splitlines()
    if res.returncode != 0 or len(lines) != 2 or lines[0] != "false":
        raise SandboxRefused("git の作業ツリーの中で実行する")
    return os.path.realpath(lines[1])


@dataclass(frozen=True)
class IndexEntry:
    mode: str
    oid: str
    stage: str
    skip_worktree: bool


def _index_listing(repo: str) -> dict[str, IndexEntry]:
    """index に載っている (追跡済みの) パス。index は読むだけで、stat もしない。"""
    raw = _git_ok(repo, ["ls-files", "-s", "-t", "-z"])
    out: dict[str, IndexEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        head, _, name = record.partition(b"\t")
        tag, mode, oid, stage = head.decode("ascii").split()
        path = name.decode("utf-8", "surrogateescape")
        out[path] = IndexEntry(mode, oid, stage, tag == "S")
    return out


class _Worktree:
    """作業ツリーのパスを、途中の階層の symlink を辿らずに調べる。

    git と同じく、symlink を経由する階層の下にあるものは作業ツリーに無いとみなす
    (リポジトリの外を読まない)。
    """

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self._dirs: dict[str, bool] = {"": True}

    def _real_dir(self, rel: str) -> bool:
        if rel not in self._dirs:
            parent = posixpath.dirname(rel)
            ok = self._real_dir(parent)
            if ok:
                try:
                    ok = stat.S_ISDIR(os.lstat(os.path.join(self.repo, rel)).st_mode)
                except OSError:
                    ok = False
            self._dirs[rel] = ok
        return self._dirs[rel]

    def lstat(self, rel: str) -> os.stat_result | None:
        if not self._real_dir(posixpath.dirname(rel)):
            return None
        try:
            return os.lstat(os.path.join(self.repo, rel))
        except OSError:
            return None

    def read_regular(self, rel: str, limit: int | None = None) -> bytes | None:
        """通常ファイルの中身 (FIFO などは開かない)。limit を超えるなら b""。"""
        if self.lstat(rel) is None:
            return None
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(os.path.join(self.repo, rel), flags)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                return None
            if limit is not None and st.st_size > limit:
                return b""
            return fh.read() if limit is None else fh.read(limit + 1)


def _object_format(repo: str) -> str:
    return _git_ok(repo, ["rev-parse", "--show-object-format"]).decode().strip()


def _object_id(fmt: str, data: bytes) -> str:
    return hashlib.new(fmt or "sha1", b"blob %d\0" % len(data) + data).hexdigest()


def _file_mode_tracked(repo: str) -> bool:
    res = _git(repo, ["config", "--type=bool", "--get", "core.fileMode"])
    return res.stdout.strip() != b"false"


def host_base_tree(repo: str) -> str:
    """ホストの base tree: index に載っている (追跡済みの) ファイルの、作業ツリーの中身。

    create と apply が同じ関数で求める (apply は作成時の base_tree と一致を要求する)。
    `git stash create` は使わない: 本物の index を書き換え (index.lock と
    post-index-change フック)、作業ツリーのファイルを clean filter に通すため。ここでは
    作業ツリーを Python で直接読み、変わったものだけ `hash-object --no-filters` で
    object にし、使い捨ての index (GIT_INDEX_FILE) から tree を作る。filter も改行の
    変換も掛けない (作業ツリーのバイト列そのまま)。本物の index は読むだけ。
    object は書く (変わったファイルの blob と tree)。
    """
    entries = _index_listing(repo)
    if any(e.stage != "0" for e in entries.values()):
        raise SandboxRefused(
            "未解決のマージがある (index に stage 1-3 のエントリがある)"
        )
    # 作業ツリーを読んで hash する前に止める: filter を通さないバイト列 (git-crypt や
    # transcrypt で鍵を解いた平文) を object として書かない。
    filtered = _filter_attrs(repo, entries)
    if filtered:
        raise SandboxRefused(_filtered_text(filtered))
    file_mode = _file_mode_tracked(repo)
    fmt = _object_format(repo)
    tree = _Worktree(repo)
    records: list[bytes] = []
    for path, entry in entries.items():
        mode, oid = entry.mode, entry.oid
        if entry.skip_worktree or mode == "160000":
            records.append(f"{mode} {oid}\t".encode() + os.fsencode(path))
            continue
        st = tree.lstat(path)
        if st is None:
            continue  # 作業ツリーで消した (symlink の階層の下も同じ)
        if stat.S_ISLNK(st.st_mode):
            data = os.readlink(os.fsencode(os.path.join(repo, path)))
            new_mode = "120000"
        elif stat.S_ISREG(st.st_mode):
            content = tree.read_regular(path)
            if content is None:
                raise SandboxRefused(
                    f"追跡しているファイル {gate.display_text(path, 120)} を読めない"
                )
            data = content
            if file_mode:
                new_mode = "100755" if st.st_mode & stat.S_IXUSR else "100644"
            else:
                new_mode = mode if mode in ("100644", "100755") else "100644"
        elif stat.S_ISDIR(st.st_mode):
            continue  # ディレクトリに置き換わった: git と同じく削除とみなす
        else:
            raise SandboxRefused(
                f"追跡しているファイル {gate.display_text(path, 120)} が通常ファイルでも"
                " symlink でもない (FIFO など)"
            )
        if _object_id(fmt, data) != oid:
            oid = (
                _git_ok(
                    repo, ["hash-object", "-w", "--no-filters", "--stdin"], input=data
                )
                .decode()
                .strip()
            )
        records.append(f"{new_mode} {oid}\t".encode() + os.fsencode(path))
    tmp = tempfile.mkdtemp(prefix="csb-index-")
    try:
        index = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        _git_ok(
            repo,
            ["update-index", "-z", "--index-info"],
            input=b"".join(r + b"\0" for r in records),
            env=index,
        )
        return _git_ok(repo, ["write-tree"], env=index).decode().strip()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _filter_attrs(repo: str, paths: Iterable[str]) -> list[tuple[str, str]]:
    """paths のうち filter 属性が付いているもの (path, 値)。check-attr は filter を実行しない。

    属性はどこで付いていても数える: どの階層の .gitattributes (作業ツリーの写しも、
    未追跡のものも、index の写しも)、info/attributes、core.attributesFile (既定は
    $XDG_CONFIG_HOME/git/attributes)、マクロ。`-filter` / `!filter` は付いていない。
    """
    names = list(paths)
    if not names:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    # 既定の check-attr は作業ツリーの .gitattributes を index の写しより先に読む。
    # index にだけ filter が残っている (作業ツリーの写しを書き換えた) 場合を逃さない
    # ように、--cached (index の .gitattributes) でも読み、両方を合わせる。
    for extra in ([], ["--cached"]):
        raw = _git_ok(
            repo,
            ["check-attr", *extra, "-z", "--stdin", "filter"],
            input=b"".join(os.fsencode(p) + b"\0" for p in names),
        )
        fields = raw.split(b"\0")
        for i in range(0, len(fields) - 2, 3):
            if fields[i + 2] in (b"unspecified", b"unset"):
                continue
            path = fields[i].decode("utf-8", "surrogateescape")
            if path not in seen:
                seen.add(path)
                out.append((path, fields[i + 2].decode("utf-8", "replace")))
    return out


def _filtered_text(found: Sequence[tuple[str, str]]) -> str:
    shown = ", ".join(
        f"{gate.display_text(path, 120)} (filter={gate.display_text(value, 40)})"
        for path, value in found[:5]
    )
    more = f" ほか {len(found) - 5} 件" if len(found) > 5 else ""
    return (
        f"filter 属性のあるリポジトリは非対応 ({shown}{more})。スナップショットは filter を"
        "通さない作業ツリーのバイト列なので、git-crypt などの平文がコンテナに入る"
    )


def _head_commit(repo: str) -> str:
    """HEAD のコミット (無ければ "")。ref を読むだけ。"""
    res = _git(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"])
    return res.stdout.decode("ascii", "replace").strip() if res.returncode == 0 else ""


def _partial_clone(repo: str) -> bool:
    """partial clone (promisor)。object を読むだけでネットワークへ取りに行くので非対応。"""
    res = _git(repo, ["config", "--get", "extensions.partialClone"])
    if res.returncode == 0 and res.stdout.strip():
        return True
    res = _git(
        repo, ["config", "--type=bool", "--get-regexp", r"^remote\..*\.promisor$"]
    )
    return any(line.split()[-1:] == [b"true"] for line in res.stdout.splitlines())


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    kind: str
    oid: str
    size: int


def _tree_entries(repo: str, tree: str) -> dict[str, TreeEntry]:
    raw = _git_ok(repo, ["ls-tree", "-r", "-l", "-z", "--full-tree", tree])
    out: dict[str, TreeEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        head, _, name = record.partition(b"\t")
        mode, kind, oid, size = head.decode("ascii").split()
        path = name.decode("utf-8", "surrogateescape")
        out[path] = TreeEntry(mode, kind, oid, int(size) if size.isdigit() else 0)
    return out


class _BlobReader:
    """`git cat-file --batch` を 1 本立ててブロブを順に読む。"""

    def __init__(self, repo: str) -> None:
        self._proc = subprocess.Popen(  # nosec B603
            [*_host_git(), "-C", repo, "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_env(),
        )

    def read(self, oid: str) -> bytes:
        stdin, stdout = self._proc.stdin, self._proc.stdout
        assert stdin is not None and stdout is not None  # nosec B101
        stdin.write(oid.encode("ascii") + b"\n")
        stdin.flush()
        header = stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise SandboxError(f"ブロブ {oid[:12]} を読めない")
        size = int(header[2])
        chunks = []
        left = size
        while left:
            chunk = stdout.read(left)
            if not chunk:
                raise SandboxError(f"ブロブ {oid[:12]} が途中で切れた")
            chunks.append(chunk)
            left -= len(chunk)
        stdout.read(1)
        return b"".join(chunks)

    def close(self) -> None:
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        if self._proc.stdout is not None:
            self._proc.stdout.close()
        self._proc.wait(timeout=GIT_TIMEOUT)


@dataclass(frozen=True)
class Snapshot:
    repo_root: str
    base_commit: str
    base_tree: str
    entries: Mapping[str, TreeEntry]
    untracked: int


def take_snapshot(repo: str) -> Snapshot:
    base_commit = _head_commit(repo)
    if not base_commit:
        raise SandboxRefused("HEAD にコミットが無い (空のリポジトリは非対応)")
    if _partial_clone(repo):
        raise SandboxRefused(
            "partial clone (promisor) のリポジトリは非対応 (object を読むとネットワークへ出る)"
        )
    base_tree = host_base_tree(repo)
    untracked = _git_ok(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
    return Snapshot(
        repo_root=repo,
        base_commit=base_commit,
        base_tree=base_tree,
        entries=_tree_entries(repo, base_tree),
        untracked=len([p for p in untracked.split(b"\0") if p]),
    )


def _plan_reader(snap: Snapshot, blobs: _BlobReader) -> Callable[[str], bytes | None]:
    def read(path: str) -> bytes | None:
        entry = snap.entries.get(path)
        if entry is None or entry.mode not in ("100644", "100755"):
            return None
        return b"" if entry.size > PLAN_READ_MAX else blobs.read(entry.oid)

    return read


def _snapshot_refusals(
    snap: Snapshot, blobs: _BlobReader
) -> tuple[list[str], list[str]]:
    """submodule・LFS ポインタ・秘密の検出 (拒否) と、スキャンを省いた件数 (注意)。"""
    refusals: list[str] = []
    submodules = [p for p, e in snap.entries.items() if e.mode == "160000"]
    if submodules:
        refusals.append(f"submodule ({_first_and_more(submodules)}) は非対応")
    lfs: list[str] = []
    hits: list[str] = []
    skipped = 0
    for path, entry in sorted(snap.entries.items()):
        if entry.mode not in ("100644", "100755"):
            continue
        if entry.size > SCAN_MAX_BLOB:
            skipped += 1
            continue
        data = blobs.read(entry.oid)
        if data.startswith(_LFS_POINTER):
            lfs.append(path)
            continue
        if b"\0" in data[:8000]:
            continue
        hit, label = gate.scan_secrets(data.decode("utf-8", "replace"), None)
        if hit:
            hits.append(f"{gate.display_text(path, 120)} ({label})")
    if lfs:
        refusals.append(f"Git LFS のポインタ ({_first_and_more(lfs)}) は非対応")
    if hits:
        refusals.append("スナップショットに秘密らしき値がある: " + ", ".join(hits[:8]))
    notes = []
    if skipped:
        notes.append(
            f"{SCAN_MAX_BLOB // (1024 * 1024)} MiB を超える {skipped} ファイルは"
            "秘密スキャンを省いた"
        )
    return refusals, notes


def _snap_ref(sid: str) -> str:
    return f"refs/csb/snap-{sid}"


def make_bundle(repo: str, tree: str, sid: str, path: str) -> str:
    """tree から固定の author / date で commit を作り、一時 ref 経由で bundle にする。"""
    snap = _git_ok(
        repo,
        ["-c", "commit.gpgSign=false", "commit-tree", tree, "-m", "csb snapshot"],
        env=_SNAP_IDENT,
    )
    commit = snap.decode().strip()
    ref = _snap_ref(sid)
    _git_ok(repo, ["update-ref", ref, commit])
    try:
        _git_ok(repo, ["bundle", "create", "-q", path, ref])
    finally:
        _git(repo, ["update-ref", "-d", ref])
    os.chmod(path, 0o600)
    return commit


# ---------------------------------------------------------------------------
# tar (コンテナへの投入と build context)
# ---------------------------------------------------------------------------
def _tar_info(name: str, kind: bytes, mode: int, uid: int, gid: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.uid, info.gid = uid, gid
    info.uname = info.gname = ""
    info.mtime = int(_now())
    return info


def write_stage_tar(
    path: str,
    uid: int,
    gid: int,
    files: Sequence[tuple[str, bytes | str]],
) -> None:
    """`work/` と `csb/` を uid:gid の所有で作り、csb/ の下に files を置く tar。

    files の値は bytes (中身) か str (ホストのファイルのパス)。
    """
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(_tar_info(WORKDIR.lstrip("/"), tarfile.DIRTYPE, 0o755, uid, gid))
        tar.addfile(_tar_info(STAGE.lstrip("/"), tarfile.DIRTYPE, 0o700, uid, gid))
        for name, src in files:
            info = _tar_info(
                posixpath.join(STAGE.lstrip("/"), name),
                tarfile.REGTYPE,
                0o600,
                uid,
                gid,
            )
            if isinstance(src, bytes):
                info.size = len(src)
                tar.addfile(info, io.BytesIO(src))
            else:
                info.size = os.path.getsize(src)
                with open(src, "rb") as fh:
                    tar.addfile(info, fh)


def write_tree_tar(
    path: str, entries: Mapping[str, TreeEntry], prefix: str, blobs: _BlobReader
) -> int:
    """スナップショットの prefix 以下を build context の tar にする (ホストの dir は使わない)。"""
    count = 0
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tar:
        for name, entry in sorted(entries.items()):
            if entry.kind != "blob":
                continue
            if prefix != ".":
                if not name.startswith(prefix + "/"):
                    continue
                name = name[len(prefix) + 1 :]
            data = blobs.read(entry.oid)
            if entry.mode == "120000":
                info = _tar_info(name, tarfile.SYMTYPE, 0o777, 0, 0)
                info.linkname = data.decode("utf-8", "surrogateescape")
                tar.addfile(info)
            else:
                mode = 0o755 if entry.mode == "100755" else 0o644
                info = _tar_info(name, tarfile.REGTYPE, mode, 0, 0)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            count += 1
    return count


# ---------------------------------------------------------------------------
# コンテナの中のスナップショット
# ---------------------------------------------------------------------------
def _user(meta_or_ids: Mapping[str, Any] | tuple[int, int]) -> str:
    if isinstance(meta_or_ids, tuple):
        return f"{meta_or_ids[0]}:{meta_or_ids[1]}"
    return f"{meta_or_ids['uid']}:{meta_or_ids['gid']}"


def _clone_snapshot(ep: Endpoint, cid: str, user: str, sid: str) -> None:
    g = list(_CONTAINER_GIT)
    _cexec_ok(ep, cid, user, g + ["init", "-q"])
    _cexec_ok(
        ep,
        cid,
        user,
        g
        + [
            "fetch",
            "-q",
            "--no-tags",
            BUNDLE_IN_CONTAINER,
            f"{_snap_ref(sid)}:refs/heads/{SNAP_BRANCH}",
        ],
    )
    _cexec_ok(ep, cid, user, g + ["checkout", "-q", SNAP_BRANCH])
    # / は root の所有なので /csb 自体は消せない (uid に書き込み権限が無い)。中身だけ消す。
    _cexec_ok(ep, cid, user, ["rm", "-f", BUNDLE_IN_CONTAINER])


def _check_pristine(
    ep: Endpoint, cid: str, user: str, snap_commit: str, base_tree: str, what: str
) -> None:
    """/work の HEAD と tree が snap と一致し、git status が空であること。"""
    g = list(_CONTAINER_GIT)
    heads = _cexec_ok(ep, cid, user, g + ["rev-parse", "HEAD", "HEAD^{tree}"]).split()
    if [h.decode("ascii", "replace") for h in heads] != [snap_commit, base_tree]:
        raise SandboxError(f"{what}: /work の HEAD がスナップショットと一致しない")
    status = _cexec_ok(
        ep, cid, user, g + ["status", "--porcelain=v1", "--untracked-files=all"]
    )
    if status.strip():
        lines = status.decode("utf-8", "replace").splitlines()
        shown = "\n".join("  " + gate.display_text(ln, 160) for ln in lines[:10])
        more = f"\n  … 他 {len(lines) - 10} 件" if len(lines) > 10 else ""
        raise SandboxError(
            f"{what}: /work がスナップショットから変わった (git status が空でない)。"
            "生成物は .gitignore に入れる:\n" + shown + more
        )


def _copy_in(ep: Endpoint, cid: str, tar_path: str) -> None:
    _docker_ok(ep, ["cp", "-a", "-", f"{cid}:/"], input_path=tar_path, timeout=None)


# ---------------------------------------------------------------------------
# 照合 (exec / export / net)
# ---------------------------------------------------------------------------
def _verified_work(meta: Mapping[str, Any]) -> tuple[Endpoint, dict]:
    ep = _endpoint(meta)
    try:
        info = _inspect_container(ep, meta["work_container_id"])
    except SandboxError as exc:
        raise SandboxRefused(str(exc)) from None
    reason = gate.verify_work_container(info, meta, now=_now())
    if reason:
        raise SandboxRefused(f"work コンテナが照合に通らない: {reason}")
    return ep, info


def _networks(info: Mapping[str, Any]) -> Mapping[str, Any]:
    settings = info.get("NetworkSettings")
    nets = settings.get("Networks") if isinstance(settings, dict) else None
    return nets if isinstance(nets, dict) else {}


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------
def _resolve_image(
    ep: Endpoint,
    plan: gate.CreatePlan,
    snap: Snapshot,
    blobs: _BlobReader,
    sid: str,
    sdir: str,
) -> tuple[str, str, dict]:
    """(source, ref, image inspect) を決める。VOLUME を持つイメージは拒否する。"""
    source, ref = plan.image_source, plan.image_ref
    if source == "generic":
        info = _image_info(ep, ref)
        if info is None:
            raise SandboxRefused(
                f"汎用イメージ {ref} が無い。先に `csb image build --generic` を実行する"
            )
    elif source == "dockerfile":
        context = plan.build_context
        rel = (
            plan.dockerfile
            if context == "."
            else posixpath.relpath(plan.dockerfile, context)
        )
        # create_plan も同じ判定で拒否する (要約に出る)。ここは承認の後の計画にも効かせる。
        if not gate.dockerfile_inside_context(plan.dockerfile, context):
            raise SandboxRefused("Dockerfile が build context の外にある (非対応)")
        tar_path = os.path.join(sdir, "context.tar")
        write_tree_tar(tar_path, snap.entries, context, blobs)
        ref = f"csb-base:{sid}"
        iid_path = os.path.join(sdir, "base.iid")
        try:
            _docker_ok(
                ep,
                [
                    "build",
                    "-f",
                    rel,
                    "--iidfile",
                    iid_path,
                    "-t",
                    ref,
                    "--label",
                    f"{gate.SANDBOX_LABEL}=1",
                    "--label",
                    f"{gate.LABEL_ID}={sid}",
                    "-",
                ],
                input_path=tar_path,
                capture=False,
                output_to_stderr=True,
                timeout=None,
                user_config=True,
            )
        finally:
            os.unlink(tar_path)
        info = _image_info(ep, ref)
    else:
        info = _image_info(ep, ref)
        if info is None:
            _docker_ok(
                ep,
                ["pull", ref],
                capture=False,
                output_to_stderr=True,
                timeout=None,
                user_config=True,
            )
            info = _image_info(ep, ref)
    if info is None or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(info.get("Id"))):
        raise SandboxError(f"イメージ {gate.display_text(ref, 120)} を調べられない")
    if _image_config(info).get("Volumes"):
        raise SandboxRefused(
            "イメージが VOLUME を宣言している (docker commit に入らないため非対応)"
        )
    return source, ref, info


def _remove_resources(
    ep: Endpoint, sid: str, extra_images: Sequence[str] = ()
) -> list[str]:
    """このサンドボックスのラベルを持つコンテナ・ネットワーク・イメージを消す。失敗は返す。

    一覧 (ps / network ls / images) が失敗したときも失敗として返す: docker に届かない
    ときに「消すものが無い」と読むと、呼び出し元が状態を消し、資源だけが残る。
    """
    label = f"label={gate.LABEL_ID}={sid}"
    problems: list[str] = []

    def listed(what: str, args: list[str]) -> list[str]:
        res = _docker(ep, args)
        if res.returncode != 0:
            problems.append(f"{what}の一覧: {_shown(res.stderr)}")
            return []
        return res.stdout.decode("ascii", "replace").split()

    containers = listed("コンテナ", ["ps", "-aq", "--no-trunc", "--filter", label])
    if containers:
        rm = _docker(ep, ["rm", "-f", *containers])
        if rm.returncode != 0:
            problems.append(f"コンテナ: {_shown(rm.stderr)}")
    networks = listed("ネットワーク", ["network", "ls", "-q", "--filter", label])
    if networks:
        rm = _docker(ep, ["network", "rm", *networks])
        if rm.returncode != 0:
            problems.append(f"ネットワーク: {_shown(rm.stderr)}")
    labelled = listed("イメージ", ["images", "-q", "--no-trunc", "--filter", label])
    images = list(dict.fromkeys(list(extra_images) + labelled))
    for image in images:
        info = _image_info(ep, image)
        labels = _image_config(info).get("Labels") if info else None
        if not isinstance(labels, dict) or labels.get(gate.LABEL_ID) != sid:
            continue
        rm = _docker(ep, ["rmi", "-f", image])
        if rm.returncode != 0:
            problems.append(f"イメージ: {_shown(rm.stderr)}")
    return problems


def _recording(
    read: Callable[[str], bytes | None], seen: list[str]
) -> Callable[[str], bytes | None]:
    def wrapped(path: str) -> bytes | None:
        seen.append(path)
        return read(path)

    return wrapped


def _untracked_plan_files(
    repo: str, requested: Iterable[str], tracked: Mapping[str, Any]
) -> list[str]:
    """計画が読もうとしたが追跡されていない (未追跡・ignore) のに作業ツリーにあるファイル。

    スナップショットは追跡済みのファイルだけなので、作業ツリーで見えている設定とは
    違う計画になる。確認で見せた計画とずれるので、作成を拒否する。
    """
    tree = _Worktree(repo)
    out = []
    for path in dict.fromkeys(requested):
        parts = path.split("/")
        if path.startswith("/") or ".." in parts or path in tracked:
            continue
        if tree.lstat(path) is not None:
            out.append(
                f"計画が読む {gate.display_text(path, 120)} が追跡されていない"
                " (未追跡・ignore のファイルはスナップショットに入らない)"
            )
    return out


def _create_plan(
    repo: str, snap: Snapshot, blobs: _BlobReader, cmd: gate.CsbCommand
) -> gate.CreatePlan:
    """create の計画 (スナップショットの拒否と注意を足したもの)。読むだけで何も作らない。"""
    requested: list[str] = []
    plan = gate.create_plan(
        repo,
        _recording(_plan_reader(snap, blobs), requested),
        image=cmd.image,
        setup=cmd.setup,
        paths=list(snap.entries),
        base_commit=snap.base_commit,
        untracked=snap.untracked,
    )
    refusals, notes = _snapshot_refusals(snap, blobs)
    refusals += _untracked_plan_files(repo, requested, snap.entries)
    return dataclasses.replace(
        plan,
        notices=plan.notices + tuple(notes),
        unsupported=plan.unsupported + tuple(refusals),
    )


def _create_preview_data(cwd: str, cmd: gate.CsbCommand) -> dict:
    """`csb create` の確認の data。

    計画は cmd_create と同じ gate.create_plan で、同じ規則 (追跡済みのファイルの作業
    ツリーの中身) から立てる。ただしスナップショットは作らない: 使う git は ref と
    index の読み取りと config の読み取りだけで、作業ツリーのファイルは直接読む
    (symlink の階層は辿らない)。object・ref・index・状態ディレクトリ・コンテナは
    書かない。未コミットの変更・未追跡の件数・秘密スキャン・LFS は見ない (作成時に
    検査する)。
    """
    repo = _repo_root(cwd)
    head = _head_commit(repo)
    listing = _index_listing(repo)
    tree = _Worktree(repo)

    def read(path: str) -> bytes | None:
        entry = listing.get(path)
        if entry is None or entry.mode not in ("100644", "100755"):
            return None
        return tree.read_regular(path, PLAN_READ_MAX)

    requested: list[str] = []
    plan = gate.create_plan(
        repo,
        _recording(read, requested),
        image=cmd.image,
        setup=cmd.setup,
        paths=list(listing),
        base_commit=head,
    )
    blockers = []
    if not head:
        blockers.append("HEAD にコミットが無い (空のリポジトリは非対応)")
    submodules = [p for p, e in listing.items() if e.mode == "160000"]
    if submodules:
        blockers.append(f"submodule ({_first_and_more(submodules)}) は非対応")
    if any(e.stage != "0" for e in listing.values()):
        blockers.append("未解決のマージがある")
    if _partial_clone(repo):
        blockers.append("partial clone (promisor) のリポジトリは非対応")
    filtered = _filter_attrs(repo, listing)
    if filtered:
        blockers.append(_filtered_text(filtered))
    blockers += _untracked_plan_files(repo, requested, listing)
    # 固定する docker (create が _pin_endpoint で使うのと同じ選び方)。docker は起動しない。
    docker: dict[str, str] | None = None
    try:
        binary = _find_docker_binary()
        docker = {
            "path": binary,
            "owner": _owner_name(binary),
            "signature": _docker_signature_text(binary),
        }
    except SandboxRefused as exc:
        blockers.append(str(exc))
    return gate.create_preview_data(plan, head=head, blockers=blockers, docker=docker)


def cmd_create(cmd: gate.CsbCommand) -> int:
    repo = _repo_root(os.getcwd())
    snap = take_snapshot(repo)
    blobs = _BlobReader(repo)
    try:
        plan = _create_plan(repo, snap, blobs, cmd)
        _out(gate.create_summary_text(plan))
        if plan.refused:
            raise SandboxRefused("非対応の要素があるため作成しない")
        ep = _pin_endpoint(user_config=True)
        root = _state_root()
        _ensure_private_dir(root)
        sid = _new_id()
        sdir = gate.sandbox_dir(root, sid)
        # 資源 (ネットワーク・コンテナ・イメージ) を作る前に状態のディレクトリを作る。
        # gc の孤児の判定は、ラベルの一覧を取った後に読んだディレクトリで判断する。
        os.mkdir(sdir, 0o700)
        lock = _Lock(os.path.join(sdir, "lock"))
        lock.acquire()
        try:
            meta = _build_sandbox(ep, plan, snap, blobs, sid, sdir)
            _write_meta(root, meta)
        except BaseException:
            _err(f"csb: 作成に失敗したため {sid} の資源を片付ける")
            for problem in _remove_resources(ep, sid):
                _err(f"csb: 片付けに失敗: {problem}")
            lock.release()
            shutil.rmtree(sdir, ignore_errors=True)
            raise
        lock.release()
    finally:
        blobs.close()
    _out(f"作成した: サンドボックス {sid} (コンテナ {gate.container_name(sid)})")
    _out(f"次: csb exec {sid} -- <コマンド> / csb export {sid}")
    _out(f"sandbox: {sid}")
    return 0


def _build_sandbox(
    ep: Endpoint,
    plan: gate.CreatePlan,
    snap: Snapshot,
    blobs: _BlobReader,
    sid: str,
    sdir: str,
) -> dict:
    bundle = os.path.join(sdir, "snap.bundle")
    snap_commit = make_bundle(snap.repo_root, snap.base_tree, sid, bundle)
    source, ref, info = _resolve_image(ep, plan, snap, blobs, sid, sdir)
    uid, gid = (DEFAULT_UID, DEFAULT_GID) if source == "generic" else _image_user(info)
    user = _user((uid, gid))
    net = gate.network_name(sid)
    _docker_ok(
        ep,
        [
            "network",
            "create",
            "--driver",
            "bridge",
            "--label",
            f"{gate.SANDBOX_LABEL}=1",
            "--label",
            f"{gate.LABEL_ID}={sid}",
            net,
        ],
    )
    setup_cid = _create_container(ep, gate.setup_create_args(sid, info["Id"], uid, gid))
    stage = os.path.join(sdir, "stage.tar")
    write_stage_tar(stage, uid, gid, [("snap.bundle", bundle)])
    try:
        _copy_in(ep, setup_cid, stage)
    finally:
        os.unlink(stage)
    _docker_ok(ep, ["start", setup_cid])
    _clone_snapshot(ep, setup_cid, user, sid)
    _check_pristine(ep, setup_cid, user, snap_commit, snap.base_tree, "clone")
    for step in plan.setup:
        _err(
            f"csb: setup {gate.display_text(step.origin, 80)}: {gate.display_text(' '.join(step.argv), 200)}"
        )
        res = _cexec(ep, setup_cid, user, step.argv, capture=False, timeout=None)
        if res.returncode != 0:
            raise SandboxError(
                f"setup {gate.display_text(step.origin, 80)} が exit {res.returncode} で失敗した"
            )
    _check_pristine(ep, setup_cid, user, snap_commit, snap.base_tree, "setup")
    _docker_ok(ep, ["stop", "-t", "5", setup_cid])
    setup_image = (
        _docker_ok(ep, ["commit", setup_cid, f"csb-setup:{sid}"]).decode().strip()
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", setup_image):
        raise SandboxError("docker commit がイメージ ID を返さない")
    _docker_ok(ep, ["rm", "-f", setup_cid])
    work_cid = _create_container(ep, gate.work_create_args(sid, setup_image, uid, gid))
    _docker_ok(ep, ["network", "disconnect", net, work_cid])
    _docker_ok(ep, ["start", work_cid])
    meta = {
        "schema": gate.META_SCHEMA,
        "id": sid,
        "repo_root": snap.repo_root,
        "base_commit": snap.base_commit,
        "base_tree": snap.base_tree,
        "snap_commit": snap_commit,
        "image": {"source": source, "ref": ref, "id": info["Id"]},
        "setup_image_id": setup_image,
        "work_container_id": work_cid,
        "uid": uid,
        "gid": gid,
        "docker_bin": ep.bin,
        "docker_host": ep.host,
        "created_at": _now(),
    }
    gate.validate_meta(meta)
    reason = gate.verify_work_container(
        _inspect_container(ep, work_cid), meta, now=_now()
    )
    if reason:
        raise SandboxError(f"作成した work コンテナが照合に通らない: {reason}")
    return meta


def cmd_exec(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    lock = _lock(root, cmd.sandbox_id)
    lock.acquire()
    try:
        meta = _load_meta(root, cmd.sandbox_id)
        ep, _ = _verified_work(meta)
        args = ["exec", "-i"]
        if _isatty(0) and _isatty(1):
            args.append("-t")
        args += ["-u", _user(meta)]
        if cmd.workdir is not None:
            args += ["-w", cmd.workdir]
        for name, value in cmd.env:
            args += ["-e", f"{name}={value}"]
        args += [meta["work_container_id"], *cmd.argv]
        res = _docker(ep, args, capture=False, timeout=None, on_spawn=lock.release)
    finally:
        lock.release()
    return res.returncode


def cmd_net_on(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    minutes = cmd.minutes or gate.NET_MINUTES_DEFAULT
    with _lock(root, cmd.sandbox_id):
        meta = _load_meta(root, cmd.sandbox_id)
        ep, info = _verified_work(meta)
        net = gate.network_name(cmd.sandbox_id)
        connected = net in _networks(info)
        if not connected:
            _docker_ok(ep, ["network", "connect", net, meta["work_container_id"]])
        meta["net_on_until"] = _now() + minutes * 60
        try:
            _write_meta(root, meta)
        except BaseException:
            if not connected:
                _docker(ep, ["network", "disconnect", net, meta["work_container_id"]])
            raise
    until = time.strftime("%H:%M", time.localtime(meta["net_on_until"]))
    _out(f"net on: {cmd.sandbox_id} を {minutes} 分間 ({until} まで) {net} に接続した")
    return 0


def _disconnect(meta: dict) -> None:
    """接続されていれば切断する。切断できなければ例外 (SandboxError / SandboxRefused)。"""
    ep = _endpoint(meta)
    info = _inspect_container(ep, meta["work_container_id"])
    net = gate.network_name(meta["id"])
    if net in _networks(info):
        _docker_ok(ep, ["network", "disconnect", net, meta["work_container_id"]])


def cmd_net_off(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    with _lock(root, cmd.sandbox_id):
        meta = _load_meta(root, cmd.sandbox_id)
        _disconnect(meta)
        meta.pop("net_on_until", None)
        _write_meta(root, meta)
    _out(f"net off: {cmd.sandbox_id} をネットワークから切断した")
    return 0


def expire_windows() -> None:
    """期限の切れた net on を切断する。どのサブコマンドでも最初に呼ぶ (失敗は警告だけ)。"""
    root = _state_root()
    now = _now()
    for sid in _sandbox_ids(root):
        try:
            meta = gate.load_meta(gate.meta_path(root, sid))
        except gate.MetaError:
            continue
        if not ("net_on_until" in meta and meta["net_on_until"] <= now):
            continue
        lock = _Lock(os.path.join(gate.sandbox_dir(root, sid), "lock"))
        try:
            if not lock.acquire(blocking=False):
                continue
        except SandboxRefused:
            continue
        try:
            meta = gate.load_meta(gate.meta_path(root, sid))
            if "net_on_until" in meta and meta["net_on_until"] <= now:
                _disconnect(meta)
                meta.pop("net_on_until")
                _write_meta(root, meta)
                _err(f"csb: {sid} の net on の期限が切れたため切断した")
        except (gate.MetaError, SandboxError, SandboxRefused, OSError) as exc:
            # SandboxRefused: 固定した docker が検査に落ちた。ほかのサブコマンド
            # (ls・rm・gc) を止めないよう、警告だけにする。
            _err(
                f"csb: {sid} の期限切れの切断に失敗した: {gate.display_text(str(exc), 200)}"
            )
        finally:
            lock.release()


def cmd_export(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    sid = cmd.sandbox_id
    with _lock(root, sid):
        meta = _load_meta(root, sid)
        ep, _ = _verified_work(meta)
        cid, user = meta["work_container_id"], _user(meta)
        g = list(_CONTAINER_GIT)
        env = [("GIT_INDEX_FILE", EXPORT_INDEX)]
        _cexec_ok(ep, cid, user, g + ["read-tree", meta["snap_commit"]], env=env)
        _cexec_ok(ep, cid, user, g + ["add", "-A"], env=env)
        patch = _cexec_ok(
            ep, cid, user, g + [*_EXPORT_DIFF, meta["snap_commit"]], env=env
        )
        if not patch:
            _out("変更なし (パッチは作らない)")
            return 0
        if len(patch) > PATCH_MAX_BYTES:
            raise SandboxRefused(
                f"パッチが {PATCH_MAX_BYTES // (1024 * 1024)} MiB を超える (分割する)"
            )
        summary = gate.analyze_patch(patch)
        _write_private(gate.patch_path(root, sid, summary.sha256), patch)
    _out(summary.sha256)
    _out(gate.patch_summary_text(summary))
    _out(f"次: csb verify {sid} --patch {summary.sha256} -- <テストの argv>")
    return 0


def cmd_verify(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    sid, sha = cmd.sandbox_id, cmd.patch
    with _lock(root, sid):
        meta = _load_meta(root, sid)
        patch = _read_patch(root, sid, sha)
    ep = _endpoint(meta)
    user = _user(meta)
    serial = _lock(root, sid, "verify.lock")
    if not serial.acquire(blocking=False):
        raise SandboxRefused(f"{sid} の verify が別に実行中")
    try:
        name = gate.container_name(sid, "verify")
        stale = _docker(
            ep,
            [
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                f"name=^/{name}$",
                "--filter",
                f"label={gate.LABEL_ID}={sid}",
            ],
        )
        if stale.stdout.strip():
            _docker(ep, ["rm", "-f", *stale.stdout.decode().split()])
        cid = _create_container(
            ep,
            gate.verify_create_args(
                sid, meta["setup_image_id"], meta["uid"], meta["gid"]
            ),
        )
        try:
            stage = os.path.join(gate.sandbox_dir(root, sid), "verify-stage.tar")
            write_stage_tar(stage, meta["uid"], meta["gid"], [("patch.diff", patch)])
            try:
                _copy_in(ep, cid, stage)
            finally:
                os.unlink(stage)
            _docker_ok(ep, ["start", cid])
            _check_pristine(
                ep, cid, user, meta["snap_commit"], meta["base_tree"], "verify"
            )
            applied = _cexec(
                ep,
                cid,
                user,
                list(_CONTAINER_GIT)
                + ["apply", "--whitespace=nowarn", PATCH_IN_CONTAINER],
            )
            if applied.returncode != 0:
                raise SandboxRefused(
                    f"パッチがスナップショットに適用できない: {_shown(applied.stderr)}"
                )
            _err(
                f"csb: verify {sid} --patch {sha[:12]}: {gate.display_text(' '.join(cmd.argv), 200)}"
            )
            res = _docker(
                ep,
                ["exec", "-u", user, "-w", WORKDIR, cid, *cmd.argv],
                capture=False,
                timeout=None,
            )
        finally:
            _docker(ep, ["rm", "-f", cid])
    finally:
        serial.release()
    record = {
        "schema": gate.VERIFY_SCHEMA,
        "id": sid,
        "patch_sha256": sha,
        "base_tree": meta["base_tree"],
        "setup_image_id": meta["setup_image_id"],
        "argv": list(cmd.argv),
        "exit_code": res.returncode,
        "finished_at": _now(),
    }
    with _lock(root, sid):
        _write_json(gate.verify_record_path(root, sid, sha), record)
    status = gate.verify_status(record, sha, meta)
    _err(f"csb: verify {status} (exit {res.returncode})")
    return res.returncode


def cmd_attest(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    sid, sha = cmd.sandbox_id, cmd.patch
    ref = None
    if cmd.ref is not None:
        path = os.path.abspath(cmd.ref)
        digest = _hash_file(path)
        if digest is None:
            raise SandboxRefused(
                f"--ref のファイルを読めない: {gate.display_text(path)}"
            )
        ref = {"path": path, "sha256": digest}
    with _lock(root, sid):
        _load_meta(root, sid)
        _read_patch(root, sid, sha)
        record = {
            "schema": gate.ATTEST_SCHEMA,
            "patch_sha256": sha,
            "reviewer": cmd.review_name,
            "result": cmd.review_result,
            "unresolved": cmd.unresolved or 0,
            "ref": ref,
            "recorded_at": _now(),
        }
        if not gate.attest_record_valid(record, sha):
            raise SandboxRefused("attest の記録が不正")
        _write_json(gate.attest_record_path(root, sid, sha, cmd.review_name), record)
    _out(
        f"attest: {cmd.review_name}={cmd.review_result} (未解決 {record['unresolved']} 件)"
        f" をパッチ {sha[:12]} に記録した"
    )
    return 0


@dataclass(frozen=True)
class ApplyContext:
    meta: dict
    patch: bytes
    summary: gate.PatchSummary
    verify_record: Any
    attests: list[Any]
    notes: list[str]
    host_tree: str | None
    text: str
    blockers: list[str]
    host_checked: bool = True
    head: str = "unknown"
    manual_steps: tuple[str, ...] = ()
    repo_problem: str = ""


def _apply_context(
    root: str, sid: str, sha: str, *, host_check: bool = True
) -> ApplyContext:
    """apply の判断材料。host_check=False (preflight と確認の要約) はホストの作業ツリーを
    見ない: 使う git は ref の読み取りだけで、base tree の照合は「未確認」として残す。
    """
    meta = _load_meta(root, sid)
    patch = _read_patch(root, sid, sha)
    summary = gate.analyze_patch(patch)
    if summary.sha256 != sha:
        raise SandboxRefused("パッチの sha が --patch と一致しない")
    record = _load_record(gate.verify_record_path(root, sid, sha))
    attests, notes = _load_attests(root, sid, sha)
    repo = meta["repo_root"]
    tree: str | None = None
    head, problem = "unknown", ""
    try:
        if _repo_root(repo) != repo:
            raise SandboxRefused("repo_root が作業ツリーの最上位ではない")
        head = "same" if _head_commit(repo) == meta["base_commit"] else "moved"
        if host_check:
            tree = host_base_tree(repo)
    except (SandboxRefused, SandboxError) as exc:
        problem = f"ホストのリポジトリを確かめられない: {exc}"
        if host_check:
            notes.append(f"ホストの base tree を求められない: {exc}")
    text = gate.apply_summary_text(summary, meta, record, attests)
    blockers = gate.apply_blockers(
        summary, meta, record, attests, tree, host_checked=host_check
    )
    if problem and not host_check:
        blockers.append(problem)
    manual: tuple[str, ...] = ()
    if dict(summary.risky).get("harness"):
        manual = tuple(
            gate.harness_manual_steps(repo, gate.patch_path(root, sid, sha), sha)
        )
    return ApplyContext(
        meta=meta,
        patch=patch,
        summary=summary,
        verify_record=record,
        attests=attests,
        notes=notes,
        host_tree=tree,
        text=text,
        blockers=blockers,
        host_checked=host_check,
        head=head,
        manual_steps=manual,
        repo_problem=problem,
    )


def context_text(ctx: ApplyContext) -> str:
    """preflight / apply が表示する要約・注意・未確認の項目・拒否理由・人の手順。"""
    lines = [ctx.text]
    lines += [f"注意: {note}" for note in ctx.notes]
    if not ctx.host_checked:
        lines += [
            f"未確認 (apply の時に検査する): {item}" for item in gate.APPLY_UNCHECKED
        ]
    if ctx.blockers:
        lines.append("apply を拒否する理由:")
        lines += [f"  - {blocker}" for blocker in ctx.blockers]
    else:
        lines.append("apply を拒否する理由: なし")
    if ctx.manual_steps:
        lines.append(
            "人が自分で対象のリポジトリへ適用する手順 (エージェントには実行させない):"
        )
        lines += [f"  {step}" for step in ctx.manual_steps]
    return "\n".join(lines)


def _print_context(ctx: ApplyContext) -> None:
    _out(context_text(ctx))


def _apply_preview_data(root: str, cmd: gate.CsbCommand) -> dict:
    """`csb apply` の確認の data。preflight と同じ材料で、ロックは取らない。

    ホストの作業ツリーは見ない (stash create も index の stat もしない): base tree と
    filter 属性の照合は未確認として出し、本物の apply が検査する。
    """
    ctx = _apply_context(root, cmd.sandbox_id, cmd.patch, host_check=False)
    return gate.apply_preview_data(
        ctx.summary,
        ctx.meta,
        ctx.verify_record,
        ctx.attests,
        notes=ctx.notes,
        head=ctx.head,
        patch_file=gate.patch_path(root, cmd.sandbox_id, cmd.patch),
        extra_blockers=[ctx.repo_problem] if ctx.repo_problem else [],
    )


def preview_data(cmd: gate.CsbCommand, cwd: str, root: str) -> dict:
    """bash-review フックの確認に出す data (gate.render_preview が描く)。"""
    if cmd.sub == "create":
        return _create_preview_data(cwd, cmd)
    if cmd.sub == "apply":
        return _apply_preview_data(root, cmd)
    if cmd.sub == "image build":
        return _image_build_preview_data()
    raise ValueError(f"no preview for {cmd.sub}")


def _image_build_preview_data() -> dict:
    """`csb image build --generic` の確認の data。docker もビルドも起動しない。

    Dockerfile はリポジトリの containers/sandbox にあり、リポジトリは書き換えられる。
    そのため中身の sha256 と、取ってくるイメージ (digest で固定しているか) を見せる。
    """
    context = _generic_context()
    dockerfile = os.path.join(context, "Dockerfile")
    blockers: list[str] = []
    sha, bases = "", []
    instructions: list[str] = []
    try:
        # _Worktree.read_regular と同じく、symlink を辿らず、FIFO などで止まらない
        # (O_NONBLOCK) で開き、fstat で通常ファイルかを確かめ、大きさに上限を付ける。
        fd = os.open(
            dockerfile,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise OSError("通常ファイルでない")
            data = fh.read(PLAN_READ_MAX + 1)
        if len(data) > PLAN_READ_MAX:
            blockers.append("Dockerfile が大きすぎる")
        else:
            sha = hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8", "replace")
            bases = gate.dockerfile_bases(text)
            instructions = gate.dockerfile_instructions(text)
            blockers += gate.dockerfile_blockers(text)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        blockers.append(
            f"Dockerfile を読めない: {gate.display_text(dockerfile)} ({reason})"
        )
    docker: dict[str, str] | None = None
    try:
        binary = _find_docker_binary()
        docker = {
            "path": binary,
            "owner": _owner_name(binary),
            "signature": _docker_signature_text(binary),
        }
    except SandboxRefused as exc:
        blockers.append(str(exc))
    return gate.image_build_preview_data(
        context=context,
        dockerfile=dockerfile,
        sha256=sha,
        bases=bases,
        instructions=instructions,
        context_diff=_context_head_diff(context),
        docker=docker,
        blockers=blockers,
    )


def _context_head_diff(context: str) -> dict[str, Any]:
    """build context の中で HEAD と違うファイルの数 ({"state", "count"})。拒否はしない。

    git は _host_git の作法で、ref・index・object の読み取りと、追跡されていないファイルの
    一覧だけを使う (filter も fsmonitor も動かさない)。作業ツリーの中身は _Worktree で
    読み、blob の id を _object_id で計算して HEAD の木と比べる (変更・追加・削除・
    未追跡を数える)。リポジトリの外なら "unknown"。
    """
    unknown = {"state": "unknown", "count": 0}
    try:
        repo = _repo_root(context)
        rel = os.path.relpath(os.path.realpath(context), repo)
        if rel == ".." or rel.startswith("../"):
            return unknown
        prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
        fmt = _object_format(repo)
        head: dict[str, tuple[str, str]] = {}
        raw = _git_ok(repo, ["ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", rel])
        for record in raw.split(b"\0"):
            if not record:
                continue
            meta, _, name = record.partition(b"\t")
            mode, kind, oid = meta.decode("ascii").split()
            if kind == "blob":
                head[name.decode("utf-8", "surrogateescape")] = (mode, oid)
        others = _git_ok(
            repo, ["ls-files", "-z", "--others", "--exclude-standard", "--", rel]
        )
        present = {p for p in _index_listing(repo) if p.startswith(prefix)}
        present |= {
            p.decode("utf-8", "surrogateescape") for p in others.split(b"\0") if p
        }
    except (SandboxError, SandboxRefused, OSError, ValueError):
        return unknown
    tree = _Worktree(repo)
    changed = 0
    for path in sorted(present | set(head)):
        st = tree.lstat(path)
        current: tuple[str, str] | None = None
        if st is not None and stat.S_ISLNK(st.st_mode):
            try:
                target = os.fsencode(os.readlink(os.path.join(repo, path)))
            except OSError:
                target = None
            if target is not None:
                current = ("120000", _object_id(fmt, target))
        elif st is not None and stat.S_ISREG(st.st_mode):
            data = tree.read_regular(path, PLAN_READ_MAX)
            if data is not None:
                mode = "100755" if st.st_mode & 0o111 else "100644"
                current = (mode, _object_id(fmt, data))
        if current != head.get(path):
            changed += 1
    return {"state": "changed" if changed else "none", "count": changed}


_PREVIEW_KEYS = frozenset({"argv", "cwd", "state"})


def _preview_request(raw: str) -> tuple[gate.CsbCommand, str, str] | None:
    try:
        req = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(req, dict) or set(req) != _PREVIEW_KEYS:
        return None
    argv, cwd, root = req["argv"], req["cwd"], req["state"]
    if not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
        return None
    if not all(isinstance(p, str) and os.path.isabs(p) for p in (cwd, root)):
        return None
    cmd = gate.parse_csb_argv(argv)
    if cmd is None or cmd.sub not in ("create", "apply", "image build"):
        return None
    return cmd, cwd, root


def preview_main(stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    """bash-review フックの子プロセスの入口: create / apply の確認の data を作る。

    stdin の JSON `{"argv": [...], "cwd": 絶対パス, "state": 状態ディレクトリ}` を読み、
    preview_data の結果を JSON で stdout へ書く (文面はフックが render_preview で描く)。
    object・ref・index・状態ディレクトリ・コンテナは書かない。git はリポジトリの設定を
    固めて (_host_git) 呼び、ref・index・config を読むだけ。作業ツリーのファイルは
    直接読む。終了コード: 0 書いた、2 拒否 (SandboxRefused)、1 それ以外 (要求の不正・
    実行時のエラー)。失敗の理由はフックへ渡さない (フックは固定の語だけを足す)。
    """
    request = _preview_request((stdin or sys.stdin).read())
    if request is None:
        return 1
    cmd, cwd, root = request
    try:
        data = preview_data(cmd, cwd, root)
    except SandboxRefused:
        return 2
    except (SandboxError, OSError, subprocess.SubprocessError):
        return 1
    (stdout or sys.stdout).write(json.dumps(data, ensure_ascii=False))
    return 0


def cmd_preflight(cmd: gate.CsbCommand) -> int:
    """apply の判断材料を見せる (静的に許可される)。ホストの作業ツリーは見ない。"""
    root = _state_root()
    with _lock(root, cmd.sandbox_id):
        ctx = _apply_context(root, cmd.sandbox_id, cmd.patch, host_check=False)
    _print_context(ctx)
    return 1 if ctx.blockers else 0


def _filtered_paths(repo: str, summary: gate.PatchSummary) -> list[str]:
    """パッチが触れるパスのうち filter 属性が付いているもの (check-attr は filter を実行しない)。"""
    paths = dict.fromkeys(
        p for f in summary.files for p in (f.old_path, f.new_path) if p is not None
    )
    return [path for path, _ in _filter_attrs(repo, paths)]


def cmd_apply(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    with _lock(root, cmd.sandbox_id):
        ctx = _apply_context(root, cmd.sandbox_id, cmd.patch)
        _print_context(ctx)
        if ctx.blockers:
            raise SandboxRefused("apply の条件を満たしていない")
        if _isatty(0):
            sys.stderr.write("このパッチをホストのリポジトリへ適用しますか? [y/N]: ")
            sys.stderr.flush()
            answer = sys.stdin.readline().strip().lower()
            if answer not in ("y", "yes"):
                # 終了コードの表 (references/sandbox.md): exit 1、stderr は `csb: 中止:`
                _err("csb: 中止: 確認で断ったため適用していない")
                return 1
        repo = ctx.meta["repo_root"]
        filtered = _filtered_paths(repo, ctx.summary)
        if filtered:
            raise SandboxRefused(
                "filter 属性のあるファイルには適用しない (git apply が filter を実行するため): "
                + ", ".join(gate.display_text(p, 120) for p in filtered[:8])
            )
        git_apply = ["-c", "apply.whitespace=nowarn", "apply", "--whitespace=nowarn"]
        check = _git(repo, git_apply + ["--check", "-"], input=ctx.patch)
        if check.returncode != 0:
            raise SandboxRefused(
                f"git apply --check が失敗した: {_shown(check.stderr)}"
            )
        _git_ok(repo, git_apply + ["-"], input=ctx.patch)
    _out(
        f"適用した: {len(ctx.summary.files)} ファイル (+{ctx.summary.added} / "
        f"-{ctx.summary.removed} 行)。後片付け: csb rm {cmd.sandbox_id}"
    )
    return 0


NET_UNREACHABLE = "docker に届かない"
NET_REFUSED_DOCKER = "固定した docker が検査に落ちた"
NET_UNVERIFIED = "照合に通らない"


def _container_state(
    meta: Mapping[str, Any],
) -> tuple[str, str, bool | None, str]:
    """(State.Status, 照合の結果, サンドボックスのネットワークにつながっているか,
    つながっているかが分からない理由)。

    つながっているかは inspect の Networks で見る (記録した期限ではない)。分からない
    ときは None で、理由は次のどれか (分かるときは ""):
    - NET_REFUSED_DOCKER: 固定した docker が検査に落ちた (docker を呼ばない)
    - NET_UNREACHABLE: docker に届かない
    - NET_UNVERIFIED: 照合に落ちている (NetworkMode が host / container:X など)、または
      サンドボックスのネットワーク以外にもつながっている (「off」とは言えない)
    前の 2 つでは State.Status は "?"、照合の結果は例外の文面。
    """
    try:
        ep = _endpoint(meta)
    except SandboxRefused as exc:
        return "?", str(exc), None, NET_REFUSED_DOCKER
    try:
        info = _inspect_container(ep, meta["work_container_id"])
    except (SandboxError, SandboxRefused) as exc:
        return "?", str(exc), None, NET_UNREACHABLE
    state = info.get("State")
    status = state.get("Status") if isinstance(state, dict) else None
    reason = gate.verify_work_container(info, meta, now=_now())
    net = gate.network_name(meta["id"])
    networks = _networks(info)
    attached = net in networks
    connected: bool | None = attached
    if set(networks) - {net}:
        connected = None
    elif reason and not _only_the_window_expired(info, meta, attached):
        connected = None
    unknown = NET_UNVERIFIED if connected is None else ""
    return str(status or "?"), reason or "ok", connected, unknown


def _only_the_window_expired(
    info: Mapping[str, Any], meta: Mapping[str, Any], connected: bool
) -> bool:
    """照合に落ちた理由が「net on の期限が過ぎてもつながっている」ことだけか。

    期限の内側の時刻で照合し直して通れば、そう判断する (次の csb の呼び出しで切断する
    状態なので、表示は「on (期限切れ。…)」)。
    """
    until = meta.get("net_on_until")
    if not (connected and isinstance(until, (int, float)) and until <= _now()):
        return False
    return not gate.verify_work_container(info, meta, now=until - 1)


def _sandbox_rows(root: str, ids: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sid in ids:
        try:
            meta = _load_meta(root, sid)
        except SandboxRefused as exc:
            rows.append({"id": sid, "error": str(exc)})
            continue
        status, check, connected, unknown = _container_state(meta)
        until = meta.get("net_on_until")
        rows.append(
            {
                "id": sid,
                "created_at": meta["created_at"],
                "repo_root": meta["repo_root"],
                "image": meta["image"],
                # 記録した期限 (期限内のときだけ。意味は前と同じ)
                "net_on_until": until if until and until > _now() else None,
                # 実際の接続 (inspect の Networks)。None は分からない (理由は check)
                "net_connected": connected,
                # 期限が過ぎた記録が残っている (次の csb の呼び出しで切断する)
                "net_expired": until is not None and until <= _now(),
                "container": status,
                "check": check,
                # 表示用。`_` で始まるキーは --json に出さない (_public_rows)
                "_net_unknown": unknown,
            }
        )
    return rows


def _public_rows(rows: Any) -> Any:
    """--json に出す形 (`_` で始まる表示用のキーを除く。形は前と同じ)。"""
    if isinstance(rows, list):
        return [_public_rows(row) for row in rows]
    return {k: v for k, v in rows.items() if not k.startswith("_")}


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _net_text(row: Mapping[str, Any]) -> str:
    """ネットワークの状態 (実際の接続にもとづく)。期限は次の csb の呼び出しで処理する。"""
    connected = row["net_connected"]
    if connected is None:
        fallback = NET_UNREACHABLE if row.get("container") == "?" else NET_UNVERIFIED
        return f"不明 ({row.get('_net_unknown') or fallback})"
    if not connected:
        return "off"
    if row["net_on_until"]:
        return f"on ({_when(row['net_on_until'])} まで)"
    if row["net_expired"]:
        return "on (期限切れ。次の csb の呼び出しで切断)"
    return "on (期限の記録なし)"


def cmd_ls(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    rows = _sandbox_rows(root, _sandbox_ids(root))
    if cmd.as_json:
        _out(json.dumps(_public_rows(rows), ensure_ascii=False, indent=2))
        return 0
    if not rows:
        _out("サンドボックスは無い")
    for row in rows:
        if "error" in row:
            _out(f"{row['id']}  (壊れている: {row['error']})")
            continue
        net = f"net {_net_text(row)}"
        image = row["image"]
        _out(
            f"{row['id']}  {_when(row['created_at'])}  {row['container']}  {net}  "
            f"{image['source']}:{gate.display_text(image['ref'], 60)}  "
            f"{gate.display_text(row['repo_root'], 80)}"
            + (
                ""
                if row["check"] == "ok"
                else f"  [照合: {gate.display_text(row['check'], 120)}]"
            )
        )
    return 0


def _patch_rows(root: str, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    sid = meta["id"]
    directory = os.path.dirname(gate.patch_path(root, sid, "0" * 64))
    rows: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for name in names:
        sha = name[: -len(".patch")]
        if not (name.endswith(".patch") and re.fullmatch(r"[0-9a-f]{64}", sha)):
            continue
        try:
            summary = gate.analyze_patch(_read_patch(root, sid, sha))
        except SandboxRefused as exc:
            rows.append({"sha256": sha, "error": str(exc)})
            continue
        record = _load_record(gate.verify_record_path(root, sid, sha))
        attests, _notes = _load_attests(root, sid, sha)
        rows.append(
            {
                "sha256": sha,
                "files": len(summary.files),
                "added": summary.added,
                "removed": summary.removed,
                "verify": gate.verify_status(record, sha, meta),
                "reviews": {
                    r: gate.review_status(attests, sha, r)
                    for r in gate.required_reviews(summary)
                },
            }
        )
    return rows


def cmd_status(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    ids = [cmd.sandbox_id] if cmd.sandbox_id else _sandbox_ids(root)
    if cmd.sandbox_id:
        _require_sandbox_dir(root, cmd.sandbox_id)
    rows = _sandbox_rows(root, ids)
    for row in rows:
        if "error" not in row:
            row["patches"] = _patch_rows(root, _load_meta(root, row["id"]))
    if cmd.as_json:
        _out(
            json.dumps(
                _public_rows(rows if not cmd.sandbox_id else rows[0]),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not rows:
        _out("サンドボックスは無い")
    for row in rows:
        _out(f"サンドボックス {row['id']}")
        if "error" in row:
            _out(f"  壊れている: {row['error']}")
            continue
        image = row["image"]
        _out(f"  リポジトリ: {gate.display_text(row['repo_root'])}")
        _out(f"  作成: {_when(row['created_at'])}")
        _out(f"  イメージ: {image['source']} {gate.display_text(image['ref'], 120)}")
        _out(
            f"  コンテナ: {row['container']} (照合: {gate.display_text(row['check'], 200)})"
        )
        _out(f"  ネットワーク: {_net_text(row)}")
        if not row["patches"]:
            _out("  パッチ: なし")
        for patch in row["patches"]:
            if "error" in patch:
                _out(f"  パッチ {patch['sha256']}: {patch['error']}")
                continue
            reviews = ", ".join(f"{k}={v}" for k, v in patch["reviews"].items())
            _out(
                f"  パッチ {patch['sha256']}: {patch['files']} ファイル "
                f"+{patch['added']}/-{patch['removed']}  verify {patch['verify']}  {reviews}"
            )
    return 0


def _cleanup_endpoint(meta: Mapping[str, Any] | None, sid: str) -> Endpoint:
    """rm / gc が資源を消すときの docker と endpoint。

    meta があれば、作成したときに固定した docker と endpoint を使う。その docker が
    検査に落ちたとき (更新で置き換わった・署名が通らない) は、docker の実体だけを固定の
    場所から選び直し (_find_docker_binary: trusted_path と署名を確かめ直す)、endpoint は
    記録した値 (validate_meta を通った unix ソケット) のままにする。資源は作成した
    endpoint にあるので、ここで context を読み直すと別の daemon を見てしまう (空の設定の
    既定の context は、Docker Desktop では作成時の context と違うことが多い)。
    meta が無いときだけ、いま固定できる docker と endpoint (_pin_endpoint) を使う。
    どちらでも、消す対象はラベルのサンドボックス ID で絞る (_remove_resources)。
    """
    if not meta:
        return _pin_endpoint()
    try:
        return _endpoint(meta)
    except SandboxRefused as exc:
        _err(
            f"csb: {sid} の記録した docker が使えないため、固定の場所の docker に"
            f"切り替える (endpoint は記録したもの): {gate.display_text(str(exc), 200)}"
        )
    return Endpoint(_find_docker_binary(), str(meta["docker_host"]))


def cmd_rm(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    sid = cmd.sandbox_id
    sdir = _require_sandbox_dir(root, sid)
    lock = _lock(root, sid)
    if not lock.acquire(blocking=False):
        raise SandboxRefused(f"{sid} は使用中")
    try:
        try:
            meta: dict | None = _load_meta(root, sid)
        except SandboxRefused:
            meta = None
        ep = _cleanup_endpoint(meta, sid)
        extra = [meta["setup_image_id"]] if meta else []
        problems = _remove_resources(ep, sid, extra)
        if problems:
            raise SandboxError("docker の資源を消せない: " + " / ".join(problems))
        shutil.rmtree(sdir)
    finally:
        lock.release()
    _out(f"削除した: {sid}")
    return 0


def _labelled_ids(ep: Endpoint) -> set[str]:
    """dev.claude.sandbox=1 を持つコンテナ・ネットワーク・イメージのサンドボックス ID。"""
    label = f"label={gate.SANDBOX_LABEL}=1"
    found: set[str] = set()
    for listing, inspect in (
        (
            ["ps", "-aq", "--no-trunc", "--filter", label],
            ["inspect", "--type", "container"],
        ),
        (["network", "ls", "-q", "--filter", label], ["network", "inspect"]),
        (["images", "-q", "--no-trunc", "--filter", label], ["image", "inspect"]),
    ):
        ids = _docker_ok(ep, listing).decode("ascii", "replace").split()
        if not ids:
            continue
        for item in _json_list(_docker_ok(ep, inspect + list(dict.fromkeys(ids)))):
            if not isinstance(item, dict):
                continue
            labels = item.get("Labels")
            if labels is None:
                labels = _image_config(item).get("Labels")
            sid = labels.get(gate.LABEL_ID) if isinstance(labels, dict) else None
            if gate.is_sandbox_id(sid):
                found.add(str(sid))
    return found


def cmd_gc(cmd: gate.CsbCommand) -> int:
    root = _state_root()
    days = cmd.older_than_days or GC_DEFAULT_DAYS
    cutoff = _now() - days * 86400
    removed: list[str] = []
    for sid in _sandbox_ids(root):
        sdir = gate.sandbox_dir(root, sid)
        # meta の無いディレクトリの年齢は mtime で見る。lock を開く (O_CREAT) と
        # ディレクトリの mtime が今になるので、その前に読む。
        try:
            mtime = os.lstat(sdir).st_mtime
        except OSError:
            continue
        lock = _Lock(os.path.join(sdir, "lock"))
        try:
            if not lock.acquire(blocking=False):
                continue
        except SandboxRefused:
            continue
        try:
            try:
                meta: dict | None = _load_meta(root, sid)
            except SandboxRefused:
                meta = None
            created = meta["created_at"] if meta else mtime
            if created > cutoff:
                continue
            ep = _cleanup_endpoint(meta, sid)
            problems = _remove_resources(
                ep, sid, [meta["setup_image_id"]] if meta else []
            )
            if problems:
                _err(f"csb: {sid} を消せない: {' / '.join(problems)}")
                continue
            shutil.rmtree(sdir)
            removed.append(sid)
        except (SandboxRefused, SandboxError, OSError) as exc:
            # 1 件の失敗で残りの gc を止めない。
            _err(f"csb: {sid} を消せない: {gate.display_text(str(exc), 200)}")
        finally:
            lock.release()
    try:
        ep = _pin_endpoint()
        labelled = _labelled_ids(ep)
        # 一覧を取った後で状態のディレクトリを読み直す: gc の途中で始まった create は、
        # 資源を作る前に状態のディレクトリを作る (cmd_create) ので、ここで見える。
        orphans = sorted(labelled - set(_sandbox_ids(root)))
    except (SandboxRefused, SandboxError) as exc:
        _err(f"csb: 状態の無い docker の資源を調べられない: {exc}")
        orphans = []
    for sid in orphans:
        problems = _remove_resources(ep, sid)
        if problems:
            _err(f"csb: {sid} の資源を消せない: {' / '.join(problems)}")
        else:
            removed.append(sid)
    _out(
        f"gc: {len(removed)} 件を削除した"
        + (f" ({', '.join(removed)})" if removed else "")
    )
    return 0


def cmd_image_build(cmd: gate.CsbCommand) -> int:
    context = _generic_context()
    if not os.path.isfile(os.path.join(context, "Dockerfile")):
        raise SandboxError(
            f"汎用イメージの Dockerfile が無い: {gate.display_text(context)}"
        )
    # 人が承認した境界 (image build) なので、create と同じくユーザーの docker 設定を
    # 使う (context・buildx などの cli-plugins・credential helper)。
    ep = _pin_endpoint(user_config=True)
    _docker_ok(
        ep,
        ["build", "--pull", "-t", gate.GENERIC_IMAGE_REF, context],
        capture=False,
        output_to_stderr=True,
        timeout=None,
        user_config=True,
    )
    info = _image_info(ep, gate.GENERIC_IMAGE_REF)
    _out(f"built: {gate.GENERIC_IMAGE_REF} ({info.get('Id') if info else '?'})")
    return 0


_HANDLERS: dict[str, Callable[[gate.CsbCommand], int]] = {
    "create": cmd_create,
    "exec": cmd_exec,
    "net on": cmd_net_on,
    "net off": cmd_net_off,
    "export": cmd_export,
    "verify": cmd_verify,
    "attest": cmd_attest,
    "preflight": cmd_preflight,
    "apply": cmd_apply,
    "ls": cmd_ls,
    "status": cmd_status,
    "rm": cmd_rm,
    "gc": cmd_gc,
    "image build": cmd_image_build,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"], ["help"]):
        _out(USAGE)
        return 0
    cmd = gate.parse_csb_argv(args)
    if cmd is None:
        _err("csb: 引数が不正\n" + USAGE)
        return 2
    try:
        expire_windows()
        return _HANDLERS[cmd.sub](cmd)
    except SandboxRefused as exc:
        _err(f"csb: 拒否: {exc}")
        return 2
    except SandboxError as exc:
        _err(f"csb: エラー: {exc}")
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        # ファイルの読み書き・子プロセスの起動や時間切れ。トレースバックにせず、
        # 終了コードの表 (references/sandbox.md) どおり exit 1 の `csb: エラー:` にする。
        _err(f"csb: エラー: {type(exc).__name__}: {gate.display_text(str(exc), 300)}")
        return 1
    except KeyboardInterrupt:
        _err("csb: 中断した")
        return 130


if __name__ == "__main__":
    sys.exit(main())
