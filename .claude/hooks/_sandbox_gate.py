"""コンテナサンドボックス (`csb`) の契約モジュール。

`csb` CLI (claude_sandbox.py) と bash-review フックの両方が参照する単一の真実の源。
ほとんどは純関数で、docker・ネットワーク・時計は呼ばない (時刻は引数 `now` で受け取る)。
例外は次のとおり:
- 子プロセスを起動するのは trusted_python の版の確認 (_probe_version) だけ。起動するのは
  trusted_path を満たした固定の候補の python3 で、`-I -S` と時間制限を付ける
- ファイルの中身を読むのは read_private_file (と、それを使う load_private_json /
  load_meta) だけで、1 ファイルを検査付きで読む。trusted_path などは lstat / readlink
  でファイルシステムを見る

中身は次の 9 群:

1. 名前と状態ディレクトリ (ラベル、ID、コンテナ名、ネットワーク名、state_dir、記録のパス)
2. セキュリティプロファイル (WORK_PROFILE) と `docker create` の引数
3. inspect JSON の照合 (verify_work_container)
4. meta.json の読み込みと検証
5. ホスト側コマンドの構文検査・`csb` の argv 解析・フック向けの分類
6. パッチの解析と apply の最終確認 (要約・ブロッカー・verify / attest の記録)
7. create の計画 (devcontainer / lockfile から image と setup を決める)
8. bash-review の確認に出す要約 (子プロセスが data を作り、フックが描く)
9. `~/.local/bin/csb` のラッパー (install.sh が書き、フックが照合する)
"""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import math
import os
import posixpath
import pwd
import re
import shlex
import stat
import subprocess  # nosec B404 - trusted_python の版の確認だけ
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Union

from _bash_review_common import (
    _UNRESOLVABLE_EXPANSION,
    COMPLEX_SHELL_SYNTAX,
    _has_brace_expansion,
    _has_unquoted_glob,
    _is_harness_target,
    _normalize_cmd,
    _split_top_level,
    scan_secrets,
)

# ---------------------------------------------------------------------------
# 1. 名前と状態ディレクトリ
# ---------------------------------------------------------------------------
SANDBOX_LABEL = "dev.claude.sandbox"
LABEL_ID = SANDBOX_LABEL + ".id"
LABEL_ROLE = SANDBOX_LABEL + ".role"
LABEL_PROFILE = SANDBOX_LABEL + ".profile"
ROLES = ("setup", "work", "verify")

# プロファイルを変えたら上げる。古いプロファイルで作ったコンテナは照合に落ちる。
PROFILE_VERSION = "2"
META_SCHEMA = 1
VERIFY_SCHEMA = 1
ATTEST_SCHEMA = 1

META_MAX_BYTES = 64 * 1024
PATCH_SUMMARY_LIMIT = 4 * 1024 * 1024
DEVCONTAINER_MAX_BYTES = 1024 * 1024
NET_MINUTES_DEFAULT = 15
NET_MINUTES_MAX = 120
GC_MAX_DAYS = 3650
CONTAINER_WORKDIR = "/work"
# コンテナの中の tmpfs のマウント先 (ホストの一時ディレクトリではない)。
CONTAINER_TMP = "/tmp"  # nosec B108
GENERIC_IMAGE_REF = "claude-sandbox-generic:1"
REQUIRED_REVIEWERS = ("code-reviewer", "codex", "advisor")
SECURITY_REVIEWER = "security-reviewer"
IMAGE_SOURCES = ("devcontainer", "dockerfile", "generic", "explicit")

# `csb create` は確認を出す (ユーザー決定)。create はネットワーク接続中に
# リポジトリ定義の setup (postCreateCommand / lockfile の install) を実行するため。
CREATE_REQUIRES_CONFIRMATION = True

_SANDBOX_ID = re.compile(r"[0-9a-f]{12}")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_GIT_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_REVIEWER = re.compile(r"[a-z][a-z0-9-]{0,63}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_UNIX_ID = 2**31 - 1
_STATE_PARTS = (".local", "state", "claude-sandbox")

# docker のイメージ参照 (distribution/reference の文法を絞ったもの)。先頭要素が
# ドメインと見なされるのは `.` / `:port` を含むか localhost のときだけで、それ以外の
# 要素は小文字に限る。`-` で始まる値 (docker のフラグとして読まれる) は通らない。
_REF_HOST = (
    r"(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::[0-9]{1,5})?"
    r"|localhost(?::[0-9]{1,5})?|[A-Za-z0-9-]+:[0-9]{1,5})/"
)
_REF_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_IMAGE_REF = re.compile(
    rf"(?:{_REF_HOST})?{_REF_COMPONENT}(?:/{_REF_COMPONENT})*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[0-9a-f]{64})?"
)


def is_sandbox_id(value: object) -> bool:
    """value がサンドボックス ID (小文字 16 進 12 桁) か。"""
    return isinstance(value, str) and _SANDBOX_ID.fullmatch(value) is not None


def is_image_ref(value: object) -> bool:
    """value が docker のイメージ参照 (name[:tag][@digest]) として妥当か。"""
    return (
        isinstance(value, str)
        and len(value) <= 255
        and _IMAGE_REF.fullmatch(value) is not None
    )


def _is_image(value: object) -> bool:
    return isinstance(value, str) and (
        _IMAGE_ID.fullmatch(value) is not None or is_image_ref(value)
    )


def _require_id(sandbox_id: str) -> str:
    if not is_sandbox_id(sandbox_id):
        raise ValueError(f"invalid sandbox id: {sandbox_id!r}")
    return sandbox_id


def _require_sha(sha: str) -> str:
    if not (isinstance(sha, str) and _SHA256.fullmatch(sha)):
        raise ValueError(f"invalid sha256: {sha!r}")
    return sha


def container_name(sandbox_id: str, role: str = "work") -> str:
    """役割ごとのコンテナ名 (`csb-<id>` / `csb-<id>-setup` / `csb-<id>-verify`)。"""
    _require_id(sandbox_id)
    if role == "work":
        return f"csb-{sandbox_id}"
    if role in ("setup", "verify"):
        return f"csb-{sandbox_id}-{role}"
    raise ValueError(f"unknown role: {role!r}")


def network_name(sandbox_id: str) -> str:
    """サンドボックス専用の bridge ネットワーク名 (`csb-net-<id>`)。"""
    return f"csb-net-{_require_id(sandbox_id)}"


def state_dir(home: str | None = None) -> str:
    """状態ディレクトリ `~/.local/state/claude-sandbox`。

    ホームはパスワードデータベースから引く。HOME / XDG_STATE_HOME は見ない: 環境変数で
    状態の置き場所 (meta / verify / attest の記録) を差し替えられないようにするため。
    引数 home はテスト用。
    """
    base = home if home is not None else pwd.getpwuid(os.getuid()).pw_dir
    return os.path.join(base, *_STATE_PARTS)


def sandbox_dir(state: str, sandbox_id: str) -> str:
    return os.path.join(state, _require_id(sandbox_id))


def meta_path(state: str, sandbox_id: str) -> str:
    return os.path.join(sandbox_dir(state, sandbox_id), "meta.json")


def patch_path(state: str, sandbox_id: str, sha: str) -> str:
    return os.path.join(
        sandbox_dir(state, sandbox_id), "patches", f"{_require_sha(sha)}.patch"
    )


def verify_record_path(state: str, sandbox_id: str, sha: str) -> str:
    return os.path.join(
        sandbox_dir(state, sandbox_id), "verify", f"{_require_sha(sha)}.json"
    )


def attest_record_path(state: str, sandbox_id: str, sha: str, reviewer: str) -> str:
    if not (isinstance(reviewer, str) and _REVIEWER.fullmatch(reviewer)):
        raise ValueError(f"invalid reviewer: {reviewer!r}")
    return os.path.join(
        sandbox_dir(state, sandbox_id), "attest", _require_sha(sha), f"{reviewer}.json"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 2. セキュリティプロファイルと docker create の引数
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SandboxProfile:
    """1 つの役割のコンテナ構成。create の引数と inspect の照合の両方がここを読む。

    network は "sandbox" (`csb-net-<id>` で作る) か "none"。数値は実機 (Docker Desktop
    29.9.3) の inspect で校正してある: memory_bytes / nano_cpus は inspect に出る値。
    上限は実際のテストスイート (npm / pytest / go test の並列実行) が動く大きさにする。
    /tmp の tmpfs に exec を付けるのは、go test や pip のビルドが /tmp に置いた実行
    ファイルを起動するため (ルート FS の /work は元から実行できるので noexec に意味は無い)。
    """

    role: str
    version: str
    network: str
    cap_add: tuple[str, ...]
    pids_limit: int
    memory_bytes: int
    nano_cpus: int
    tmpfs: tuple[tuple[str, str], ...]
    log_driver: str
    log_max_size: str
    ipc_mode: str
    workdir: str


WORK_PROFILE = SandboxProfile(
    role="work",
    version=PROFILE_VERSION,
    network="sandbox",
    cap_add=(),
    pids_limit=2048,
    memory_bytes=4 * 1024**3,
    nano_cpus=2_000_000_000,
    tmpfs=((CONTAINER_TMP, "rw,exec,nosuid,nodev,size=1g"),),
    log_driver="local",
    log_max_size="10m",
    ipc_mode="private",
    workdir=CONTAINER_WORKDIR,
)
# setup は依存の導入でファイルの所有者を整えるため最小限の cap を足し、ネットワークに
# 繋いだまま動く。verify は net none の使い捨て。それ以外は work と同じ。
SETUP_PROFILE = dataclasses.replace(
    WORK_PROFILE,
    role="setup",
    cap_add=("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"),
)
VERIFY_PROFILE = dataclasses.replace(WORK_PROFILE, role="verify", network="none")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _require_unix_id(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid {what}: {value!r} (numeric id required)")
    if not 0 < value <= _MAX_UNIX_ID:
        raise ValueError(f"invalid {what}: {value!r} (non-root id required)")
    return value


def _format_bytes(n: int) -> str:
    for unit, size in (("g", 1024**3), ("m", 1024**2)):
        if n % size == 0:
            return f"{n // size}{unit}"
    return str(n)


def _format_cpus(nano: int) -> str:
    whole, frac = divmod(nano, 10**9)
    return str(whole) if frac == 0 else f"{whole}.{frac:09d}".rstrip("0")


def _create_args(
    role: str,
    sandbox_id: str,
    image: str,
    uid: int,
    gid: int,
    profile: SandboxProfile,
) -> list[str]:
    if profile.role != role:
        raise ValueError(f"profile for {profile.role!r} used to build {role!r}")
    _require_id(sandbox_id)
    if not _is_image(image):
        raise ValueError(f"invalid image: {image!r}")
    _require_unix_id(uid, "uid")
    _require_unix_id(gid, "gid")
    network = network_name(sandbox_id) if profile.network == "sandbox" else "none"
    memory = _format_bytes(profile.memory_bytes)
    args = [
        "create",
        "--name",
        container_name(sandbox_id, role),
        "--label",
        f"{SANDBOX_LABEL}=1",
        "--label",
        f"{LABEL_ID}={sandbox_id}",
        "--label",
        f"{LABEL_ROLE}={role}",
        "--label",
        f"{LABEL_PROFILE}={profile.version}",
        "--network",
        network,
        "--cap-drop",
        "ALL",
    ]
    for cap in profile.cap_add:
        args += ["--cap-add", cap]
    args += [
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        str(profile.pids_limit),
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        _format_cpus(profile.nano_cpus),
        "--ipc",
        profile.ipc_mode,
    ]
    for path, options in profile.tmpfs:
        args += ["--tmpfs", f"{path}:{options}"]
    args += [
        "--init",
        "--restart",
        "no",
        "--no-healthcheck",
        "--entrypoint",
        "sleep",
        "--log-driver",
        profile.log_driver,
        "--log-opt",
        f"max-size={profile.log_max_size}",
        "--user",
        f"{uid}:{gid}",
        "--workdir",
        profile.workdir,
        image,
        "infinity",
    ]
    return args


def work_create_args(
    sandbox_id: str,
    image: str,
    uid: int,
    gid: int,
    profile: SandboxProfile = WORK_PROFILE,
) -> list[str]:
    """work コンテナの `docker create` 引数 (`create` から。docker 本体と -H は含まない)。"""
    return _create_args("work", sandbox_id, image, uid, gid, profile)


def setup_create_args(
    sandbox_id: str,
    image: str,
    uid: int,
    gid: int,
    profile: SandboxProfile = SETUP_PROFILE,
) -> list[str]:
    """setup コンテナの `docker create` 引数 (最小限の cap を足し、ネットワークに繋ぐ)。"""
    return _create_args("setup", sandbox_id, image, uid, gid, profile)


def verify_create_args(
    sandbox_id: str,
    image: str,
    uid: int,
    gid: int,
    profile: SandboxProfile = VERIFY_PROFILE,
) -> list[str]:
    """verify コンテナの `docker create` 引数 (`--network none`)。"""
    return _create_args("verify", sandbox_id, image, uid, gid, profile)


# ---------------------------------------------------------------------------
# 3. inspect JSON の照合
# ---------------------------------------------------------------------------
_MISSING = object()

# inspect に必ず出るキー。null と空は同じ扱いにするが、欠落は「空」ではなく「不明」。
_PRESENT_EMPTY = (
    ("Config", "Volumes"),
    ("HostConfig", "Binds"),
    ("HostConfig", "VolumesFrom"),
    ("HostConfig", "Links"),
    ("HostConfig", "Devices"),
    ("HostConfig", "DeviceRequests"),
    ("HostConfig", "DeviceCgroupRules"),
    ("HostConfig", "PortBindings"),
    ("HostConfig", "ExtraHosts"),
    ("HostConfig", "Dns"),
    ("HostConfig", "GroupAdd"),
    ("HostConfig", "CapAdd"),
    ("Mounts",),
)
# docker が空のとき出力から省く (Go の omitempty) キー。欠落も空として受け入れる。
_OMITEMPTY_EMPTY = (("HostConfig", "Sysctls"), ("HostConfig", "Mounts"))


def _dig(obj: Any, path: Sequence[str]) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return _MISSING
        cur = cur[key]
    return cur


def _eq(expected: Any) -> Callable[[Any], bool]:
    return lambda v: type(v) is type(expected) and v == expected


def _is_true(value: Any) -> bool:
    return value is True


def _is_false(value: Any) -> bool:
    return value is False


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, (list, dict)) and not value)


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _cap_name(cap: str) -> str:
    upper = cap.upper()
    return upper[4:] if upper.startswith("CAP_") else upper


def _cap_drop_all(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(c, str) for c in value)
        and [_cap_name(c) for c in value] == ["ALL"]
    )


def _short(value: Any, limit: int = 60) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(value)
    return display_text(text, limit)


def verify_work_container(
    info: Any,
    meta: Any,
    *,
    now: float,
    profile: SandboxProfile = WORK_PROFILE,
) -> str:
    """work コンテナの inspect JSON がプロファイルどおりなら ""、違えば最初の理由。

    照合は完全一致で、知らない形・欠落したキーは不合格にする (fail closed)。例外は
    _OMITEMPTY_EMPTY のキーだけ。ラベルはイメージ由来のもの (devcontainer の metadata
    など) が Config.Labels に混ざるので、`dev.claude.sandbox` で始まるものだけを照合する。
    NetworkMode を見るのは `--network container:X` が Networks を {} に見せるため。
    """
    try:
        m = validate_meta(meta)
    except MetaError as exc:
        return f"meta: {exc}"
    if not isinstance(info, dict):
        return "inspect: JSON オブジェクトではない"
    if profile.role != "work":
        return f"profile: work ではない ({profile.role})"
    sid = m["id"]
    user = f"{m['uid']}:{m['gid']}"
    net = network_name(sid)
    expected_labels = {
        SANDBOX_LABEL: "1",
        LABEL_ID: sid,
        LABEL_ROLE: "work",
        LABEL_PROFILE: profile.version,
    }

    def labels_ok(labels: Any) -> bool:
        if not isinstance(labels, dict):
            return False
        ours = {
            k: v
            for k, v in labels.items()
            if isinstance(k, str) and k.startswith(SANDBOX_LABEL)
        }
        return ours == expected_labels

    def log_ok(log: Any) -> bool:
        if not isinstance(log, dict) or log.get("Type") != profile.log_driver:
            return False
        config = log.get("Config")
        if not isinstance(config, dict):
            return False
        size = config.get("max-size")
        return isinstance(size, str) and size != ""

    def networks_ok(networks: Any) -> bool:
        if not isinstance(networks, dict):
            return False
        if not networks:
            return True
        return net_window_open(m, now) and set(networks) == {net}

    checks: list[tuple[tuple[str, ...], Callable[[Any], bool], str, bool]] = [
        (("Id",), _eq(m["work_container_id"]), "meta の work_container_id", False),
        (("Name",), _eq("/" + container_name(sid)), "/" + container_name(sid), False),
        (("Image",), _eq(m["setup_image_id"]), "meta の setup_image_id", False),
        (("State", "Status"), _eq("running"), "running", False),
        (("State", "Running"), _is_true, "true", False),
        (("State", "Paused"), _is_false, "false", False),
        (("State", "Restarting"), _is_false, "false", False),
        (("State", "Dead"), _is_false, "false", False),
        (("Config", "Labels"), labels_ok, "サンドボックスのラベル 4 種", False),
        (("Config", "User"), _eq(user), user, False),
        (("HostConfig", "Privileged"), _is_false, "false", False),
    ]
    checks += [(path, _empty, "空", False) for path in _PRESENT_EMPTY]
    checks += [(path, _empty, "空", True) for path in _OMITEMPTY_EMPTY]
    checks += [
        (("HostConfig", "PublishAllPorts"), _is_false, "false", False),
        (("HostConfig", "CapDrop"), _cap_drop_all, '["ALL"]', False),
        (
            ("HostConfig", "SecurityOpt"),
            _eq(["no-new-privileges:true"]),
            '["no-new-privileges:true"]',
            False,
        ),
        (("HostConfig", "NetworkMode"), _eq(net), net, False),
        (("HostConfig", "PidMode"), _eq(""), '""', False),
        (("HostConfig", "UTSMode"), _eq(""), '""', False),
        (("HostConfig", "UsernsMode"), _eq(""), '""', False),
        (("HostConfig", "IpcMode"), _eq(profile.ipc_mode), profile.ipc_mode, False),
        (
            ("HostConfig", "CgroupnsMode"),
            lambda v: isinstance(v, str) and v in ("private", ""),
            'private または ""',
            False,
        ),
        (
            ("HostConfig", "PidsLimit"),
            _eq(profile.pids_limit),
            str(profile.pids_limit),
            False,
        ),
        (
            ("HostConfig", "Memory"),
            _eq(profile.memory_bytes),
            str(profile.memory_bytes),
            False,
        ),
        (
            ("HostConfig", "MemorySwap"),
            _eq(profile.memory_bytes),
            "Memory と同じ値",
            False,
        ),
        (
            ("HostConfig", "NanoCpus"),
            _eq(profile.nano_cpus),
            str(profile.nano_cpus),
            False,
        ),
        (("HostConfig", "Runtime"), _eq("runc"), "runc", False),
        (("HostConfig", "Init"), _is_true, "true", False),
        (("HostConfig", "CgroupParent"), _eq(""), '""', False),
        (("HostConfig", "Isolation"), _eq(""), '""', False),
        (("HostConfig", "ReadonlyPaths"), _nonempty_list, "空でない", False),
        (("HostConfig", "MaskedPaths"), _nonempty_list, "空でない", False),
        (
            ("HostConfig", "Tmpfs"),
            _eq(dict(profile.tmpfs)),
            "プロファイルの tmpfs",
            False,
        ),
        (("HostConfig", "LogConfig"), log_ok, "local かつ max-size あり", False),
        (
            ("HostConfig", "OomKillDisable"),
            lambda v: v is None or v is False,
            "false または null",
            False,
        ),
        (
            ("NetworkSettings", "Networks"),
            networks_ok,
            "{} (net on の期限内だけ " + net + ")",
            False,
        ),
    ]
    for path, ok, want, optional in checks:
        value = _dig(info, path)
        name = ".".join(path)
        if value is _MISSING:
            if optional:
                continue
            return f"{name}: 欠落 (期待: {want})"
        if not ok(value):
            return f"{name}: {want} であるべき (実際: {_short(value)})"
    return ""


# ---------------------------------------------------------------------------
# 4. meta.json
# ---------------------------------------------------------------------------
class MetaError(ValueError):
    """meta.json が読めない・安全でない・形式が不正。"""


_META_REQUIRED = (
    "schema",
    "id",
    "repo_root",
    "base_commit",
    "base_tree",
    "snap_commit",
    "image",
    "setup_image_id",
    "work_container_id",
    "uid",
    "gid",
    "docker_bin",
    "docker_host",
    "created_at",
)
_META_OPTIONAL = ("net_on_until",)


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _is_abs_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("/")
        and not value.startswith("//")
        and not _CONTROL.search(value)
        and posixpath.normpath(value) == value
    )


def validate_meta(obj: Any) -> dict:
    """meta の形を検証し、検証済みの複製を返す。不正なら MetaError。"""
    if not isinstance(obj, dict):
        raise MetaError("JSON オブジェクトではない")
    unknown = sorted(str(k) for k in obj if k not in _META_REQUIRED + _META_OPTIONAL)
    if unknown:
        raise MetaError(f"未知のキー {display_text(unknown[0], 40)}")
    for key in _META_REQUIRED:
        if key not in obj:
            raise MetaError(f"{key} が欠落")

    def bad(key: str) -> MetaError:
        return MetaError(f"{key} が不正")

    if not (_is_int(obj["schema"]) and obj["schema"] == META_SCHEMA):
        raise bad("schema")
    if not is_sandbox_id(obj["id"]):
        raise bad("id")
    if not _is_abs_path(obj["repo_root"]):
        raise bad("repo_root")
    for key in ("base_commit", "base_tree", "snap_commit"):
        if not _matches(_GIT_OID, obj[key]):
            raise bad(key)
    image = obj["image"]
    if not (isinstance(image, dict) and set(image) == {"source", "ref", "id"}):
        raise bad("image")
    if not (isinstance(image["source"], str) and image["source"] in IMAGE_SOURCES):
        raise bad("image.source")
    ref = image["ref"]
    if not (isinstance(ref, str) and 0 < len(ref) <= 512 and not _CONTROL.search(ref)):
        raise bad("image.ref")
    if not _matches(_IMAGE_ID, image["id"]):
        raise bad("image.id")
    if not _matches(_IMAGE_ID, obj["setup_image_id"]):
        raise bad("setup_image_id")
    if not _matches(_CONTAINER_ID, obj["work_container_id"]):
        raise bad("work_container_id")
    for key in ("uid", "gid"):
        value = obj[key]
        if not (_is_int(value) and 0 < value <= _MAX_UNIX_ID):
            raise bad(key)
    docker_bin = obj["docker_bin"]
    if not (_is_abs_path(docker_bin) and posixpath.basename(docker_bin) == "docker"):
        raise bad("docker_bin")
    host = obj["docker_host"]
    if not (
        isinstance(host, str)
        and host.startswith("unix://")
        and _is_abs_path(host[len("unix://") :])
    ):
        raise bad("docker_host")
    if not (_is_number(obj["created_at"]) and obj["created_at"] >= 0):
        raise bad("created_at")
    until = obj.get("net_on_until")
    if until is not None and not (_is_number(until) and until >= 0):
        raise bad("net_on_until")
    out = dict(obj)
    out["image"] = dict(image)
    return out


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid constant {name}")


def _strict_json(data: bytes) -> Any:
    """重複キー・NaN / Infinity を拒む JSON 読み込み (改ざんの曖昧さを残さない)。"""
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=_reject_constant,
    )


class PrivateFileError(ValueError):
    """状態ディレクトリのファイルが読めない・安全でない・大きすぎる・JSON として不正。"""


def read_private_file(path: str, max_bytes: int) -> bytes:
    """状態ディレクトリのファイルを検査付きで読む。不正なら PrivateFileError。

    symlink は開かない (O_NOFOLLOW)。FIFO で止まらないよう O_NONBLOCK で開き、検査
    (通常ファイル・所有者が自分・group / other に書き込み権限なし・max_bytes 以下) は
    開いた fd に対して行う: 検査と読み込みが同じ inode に当たる。
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PrivateFileError(f"開けない ({exc.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PrivateFileError("通常ファイルではない")
        if st.st_uid != os.getuid():
            raise PrivateFileError("所有者が現在のユーザーではない")
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PrivateFileError("group / other に書き込み権限がある")
        if st.st_size > max_bytes:
            raise PrivateFileError("大きすぎる")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, max_bytes + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise PrivateFileError("大きすぎる")
    finally:
        os.close(fd)
    return b"".join(chunks)


def load_private_json(path: str, max_bytes: int = META_MAX_BYTES) -> Any:
    """read_private_file で読み、重複キーと NaN を拒む JSON として解釈する。"""
    data = read_private_file(path, max_bytes)
    try:
        return _strict_json(data)
    except (ValueError, RecursionError):
        raise PrivateFileError("JSON として読めない") from None


def load_meta(path: str) -> dict:
    """meta.json を検査付きで読み、検証済みの dict を返す。不正なら MetaError。

    読み込みの検査は read_private_file / load_private_json と同じ (64 KiB 以下)。
    """
    try:
        obj = load_private_json(path, META_MAX_BYTES)
    except PrivateFileError as exc:
        raise MetaError(str(exc)) from None
    return validate_meta(obj)


# ---------------------------------------------------------------------------
# 5. ホスト側の構文・csb の argv・フック向けの分類
# ---------------------------------------------------------------------------
# 単引用符の外に現れてはならない文字。`~` (チルダ展開) と制御文字 (タブを除く) も
# 落とす。`$` / バッククォート / `\r\n<>` / `$(` / 単独 `&` は既存の
# _UNRESOLVABLE_EXPANSION と COMPLEX_SHELL_SYNTAX も拾う。
_HOST_FORBIDDEN = re.compile(r"[;&|<>\\~\x00-\x08\x0a-\x1f\x7f]")
_COMMENT_START = re.compile(r"(?:^|\s)#")
# zsh の EQUALS 展開 (`=ls` → `/bin/ls`)。単語の先頭の素の `=` だけが対象。
_EQUALS_WORD = re.compile(r"(?:^|\s)=")


def _mask_single_quotes(text: str) -> str | None:
    """単引用符の区間を `_` 1 文字に置き換える。未閉鎖なら None。

    二重引用符の中の `'` は文字として扱う: そうしないと `"a'" ; x ; "'"` の `;` を
    単引用符の中と取り違えて隠してしまう。バックスラッシュは解釈しない: 単引用符の
    外のバックスラッシュは呼び出し側が全て拒否するので、最初のバックスラッシュまでは
    シェルと同じ区切りになり、それより後ろの食い違いは結果に影響しない。
    """
    out: list[str] = []
    in_double = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_double:
            in_double = ch != '"'
            out.append(ch)
        elif ch == '"':
            in_double = True
            out.append(ch)
        elif ch == "'":
            end = text.find("'", i + 1)
            if end < 0:
                return None
            out.append("_")
            i = end + 1
            continue
        else:
            out.append(ch)
        i += 1
    return None if in_double else "".join(out)


def host_syntax_ok(command: str) -> bool:
    """command がホストのシェルで「そのまま 1 つの単純コマンド」として走るか。

    単引用符の外に展開・リダイレクト・区切り・改行・バックスラッシュ・コメント・glob・
    ブレース・チルダ・EQUALS 展開が無く、shlex で語に割れるときだけ True。ペイロードは
    単引用符で囲む前提 (二重引用符の中の `$` も拒否する)。最後の 2 段 (トップレベルの
    分割と shlex) は文字の検査で既に落ちる形の再確認で、層を独立に保つために残す。
    """
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        return False
    masked = _mask_single_quotes(command)
    if masked is None:
        return False
    if (
        _UNRESOLVABLE_EXPANSION.search(masked)
        or COMPLEX_SHELL_SYNTAX.search(masked)
        or _HOST_FORBIDDEN.search(masked)
        or _COMMENT_START.search(masked)
        or _EQUALS_WORD.search(masked)
        or _has_unquoted_glob(command)
        or _has_brace_expansion(masked)
    ):
        return False
    if len(_split_top_level(command, split_ampersand=True)) != 1:
        return False
    try:
        return bool(shlex.split(command))
    except ValueError:
        return False


@dataclass(frozen=True)
class CsbCommand:
    """解析済みの `csb` コマンド。sub は "net on" / "net off" / "image build" も取る。"""

    sub: str
    sandbox_id: str = ""
    image: str = "auto"
    setup: str | None = None
    as_json: bool = False
    workdir: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    argv: tuple[str, ...] = ()
    patch: str = ""
    review_name: str = ""
    review_result: str = ""
    ref: str | None = None
    unresolved: int | None = None
    minutes: int | None = None
    older_than_days: int | None = None


def _options(args: Sequence[str], spec: Mapping[str, str]) -> dict | None:
    """spec (フラグ → "flag" | "value" | "multi") どおりに読む。未知・重複・値欠落は None。

    値は次の語として受け取る (`--opt=value` や密着形は受け付けない)。
    """
    out: dict = {}
    i = 0
    while i < len(args):
        token = args[i]
        kind = spec.get(token)
        if kind is None:
            return None
        if kind == "flag":
            if token in out:
                return None
            out[token] = True
            i += 1
            continue
        if i + 1 >= len(args):
            return None
        value = args[i + 1]
        if kind == "multi":
            out.setdefault(token, []).append(value)
        elif token in out:
            return None
        else:
            out[token] = value
        i += 2
    return out


def _split_id(rest: Sequence[str]) -> tuple[str, list[str]] | None:
    if not rest or not is_sandbox_id(rest[0]):
        return None
    return rest[0], list(rest[1:])


def _bounded_int(text: str, pattern: str, upper: int) -> int | None:
    if not re.fullmatch(pattern, text):
        return None
    value = int(text)
    return value if value <= upper else None


def _parse_create(rest: list[str]) -> CsbCommand | None:
    opts = _options(rest, {"--image": "value", "--setup": "value"})
    if opts is None:
        return None
    image = opts.get("--image", "auto")
    if image not in ("auto", "generic") and not is_image_ref(image):
        return None
    setup = opts.get("--setup")
    if setup is not None and not setup.strip():
        return None
    return CsbCommand("create", image=image, setup=setup)


def _parse_ls(rest: list[str]) -> CsbCommand | None:
    opts = _options(rest, {"--json": "flag"})
    return None if opts is None else CsbCommand("ls", as_json="--json" in opts)


def _parse_status(rest: list[str]) -> CsbCommand | None:
    """`status [ID] [--json]`。ID を省くと全サンドボックスの状態。"""
    split = _split_id(rest)
    sid, tail = split if split is not None else ("", list(rest))
    opts = _options(tail, {"--json": "flag"})
    if opts is None:
        return None
    return CsbCommand("status", sandbox_id=sid, as_json="--json" in opts)


def _parse_exec(rest: list[str]) -> CsbCommand | None:
    split = _split_id(rest)
    if split is None or "--" not in split[1]:
        return None
    sid, tail = split
    k = tail.index("--")
    argv = tail[k + 1 :]
    opts = _options(tail[:k], {"-w": "value", "-e": "multi"})
    if not argv or opts is None:
        return None
    workdir = opts.get("-w")
    if workdir is not None and not (
        workdir.startswith("/") and not _CONTROL.search(workdir)
    ):
        return None
    env: list[tuple[str, str]] = []
    for item in opts.get("-e", []):
        name, sep, value = item.partition("=")
        if not sep or not _ENV_NAME.fullmatch(name):
            return None
        env.append((name, value))
    return CsbCommand(
        "exec", sandbox_id=sid, workdir=workdir, env=tuple(env), argv=tuple(argv)
    )


def _parse_verify(rest: list[str]) -> CsbCommand | None:
    """`verify ID --patch SHA -- ARGV…`。検証の記録はパッチの sha に結びつく。"""
    split = _split_id(rest)
    if split is None or "--" not in split[1]:
        return None
    sid, tail = split
    k = tail.index("--")
    argv = tail[k + 1 :]
    opts = _options(tail[:k], {"--patch": "value"})
    if not argv or opts is None or not _matches(_SHA256, opts.get("--patch")):
        return None
    return CsbCommand("verify", sandbox_id=sid, patch=opts["--patch"], argv=tuple(argv))


def _parse_id_only(sub: str) -> Callable[[list[str]], CsbCommand | None]:
    def parse(rest: list[str]) -> CsbCommand | None:
        split = _split_id(rest)
        if split is None or split[1]:
            return None
        return CsbCommand(sub, sandbox_id=split[0])

    return parse


def _parse_patch_only(sub: str) -> Callable[[list[str]], CsbCommand | None]:
    def parse(rest: list[str]) -> CsbCommand | None:
        split = _split_id(rest)
        opts = None if split is None else _options(split[1], {"--patch": "value"})
        if split is None or opts is None or not _matches(_SHA256, opts.get("--patch")):
            return None
        return CsbCommand(sub, sandbox_id=split[0], patch=opts["--patch"])

    return parse


def _parse_attest(rest: list[str]) -> CsbCommand | None:
    split = _split_id(rest)
    spec = {
        "--patch": "value",
        "--review": "value",
        "--ref": "value",
        "--unresolved": "value",
    }
    opts = None if split is None else _options(split[1], spec)
    if split is None or opts is None or not _matches(_SHA256, opts.get("--patch")):
        return None
    name, sep, result = str(opts.get("--review", "")).partition("=")
    if not (sep and _REVIEWER.fullmatch(name) and result in ("pass", "fail")):
        return None
    ref = opts.get("--ref")
    if ref is not None and (not ref or _CONTROL.search(ref)):
        return None
    unresolved = None
    if "--unresolved" in opts:
        unresolved = _bounded_int(opts["--unresolved"], r"[0-9]{1,6}", 999_999)
        if unresolved is None:
            return None
    return CsbCommand(
        "attest",
        sandbox_id=split[0],
        patch=opts["--patch"],
        review_name=name,
        review_result=result,
        ref=ref,
        unresolved=unresolved,
    )


def _parse_net(rest: list[str]) -> CsbCommand | None:
    if not rest or rest[0] not in ("on", "off"):
        return None
    split = _split_id(rest[1:])
    if split is None:
        return None
    sid, tail = split
    if rest[0] == "off":
        return None if tail else CsbCommand("net off", sandbox_id=sid)
    opts = _options(tail, {"--minutes": "value"})
    if opts is None:
        return None
    minutes = None
    if "--minutes" in opts:
        minutes = _bounded_int(opts["--minutes"], r"[1-9][0-9]{0,3}", NET_MINUTES_MAX)
        if minutes is None:
            return None
    return CsbCommand("net on", sandbox_id=sid, minutes=minutes)


def _parse_gc(rest: list[str]) -> CsbCommand | None:
    opts = _options(rest, {"--older-than": "value"})
    if opts is None:
        return None
    days = None
    if "--older-than" in opts:
        match = re.fullmatch(r"([1-9][0-9]{0,3})d", opts["--older-than"])
        days = int(match.group(1)) if match else None
        if days is None or days > GC_MAX_DAYS:
            return None
    return CsbCommand("gc", older_than_days=days)


def _parse_image(rest: list[str]) -> CsbCommand | None:
    return CsbCommand("image build") if rest == ["build", "--generic"] else None


_SUBCOMMANDS: dict[str, Callable[[list[str]], CsbCommand | None]] = {
    "create": _parse_create,
    "ls": _parse_ls,
    "status": _parse_status,
    "exec": _parse_exec,
    "verify": _parse_verify,
    "export": _parse_id_only("export"),
    "attest": _parse_attest,
    "preflight": _parse_patch_only("preflight"),
    "apply": _parse_patch_only("apply"),
    "net": _parse_net,
    "rm": _parse_id_only("rm"),
    "gc": _parse_gc,
    "image": _parse_image,
}


def parse_csb_argv(args: Sequence[str]) -> CsbCommand | None:
    """`csb` の後ろの argv を厳密に解析する。許可リスト外の形は None。"""
    if not args:
        return None
    parse = _SUBCOMMANDS.get(args[0])
    return parse(list(args[1:])) if parse else None


def parse_csb(command: str) -> CsbCommand | None:
    """コマンド文字列を解析する。先頭の語が `csb` そのものでなければ None。"""
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] != "csb":
        return None
    return parse_csb_argv(tokens[1:])


_ASK_REASONS = {
    "create": (
        "csb create はネットワーク接続中にリポジトリ定義の setup "
        "(postCreateCommand / lockfile の install) を実行するため確認が必要"
    ),
    "net on": "csb net on は作業コンテナをネットワークへ接続するため確認が必要",
    "apply": "csb apply はサンドボックスのパッチをホストのリポジトリへ書き込むため確認が必要",
    "image build": (
        "csb image build はネットワーク接続中にリポジトリの Dockerfile "
        "(containers/sandbox) をビルドするため確認が必要"
    ),
}

# CLI の語。claude_sandbox は .py の無い語幹でも数え、大文字小文字を区別しない
# (macOS の既定の FS では `CSB` も ~/.local/bin/csb を実行する)。
_CLI_TOKEN = re.compile(
    r"(?<![\w.-])(?:csb|claude_sandbox(?:\.py)?)(?![\w.-])", re.IGNORECASE
)
_RISKY_SUB_WORD = re.compile(
    r"(?<![\w.-])(?:apply|create)(?![\w.-])|(?<![\w.-])net\s+on(?![\w.-])"
    r"|(?<![\w.-])image\s+build(?![\w.-])"
)
_SEGMENT_SPLIT = re.compile(r"[;&|()\n\r`]|\$\(")
_CSB_REF = re.compile(r"(?:^|[^\w.-])csb-")
_DOCKER_TOUCH_VERBS = frozenset(
    {"rename", "update", "commit", "export", "exec", "attach"}
)
_STATE_DIR_REF = re.compile(r"(?<![\w.-])(?:\.local/state/)?claude-sandbox(?![\w.-])")
_STATE_WRITERS = frozenset(
    {
        "cp",
        "mv",
        "rsync",
        "tar",
        "bsdtar",
        "install",
        "ln",
        "rm",
        "tee",
        "dd",
        "truncate",
        "touch",
        "chmod",
        "chown",
        "patch",
        "ditto",
        "unzip",
        "sed",
        "perl",
        "ruby",
        "node",
        "docker",
    }
)
_WORD_SPLIT = re.compile(r"[\s;&|()`<>]+")


def _dynamic_subcommand(command: str) -> bool:
    """`csb` / claude_sandbox.py の直後のサブコマンドが展開を含み、静的に確定できないか。

    見るのは `$` とバッククォートだけ。引用符での分割 (`ap''ply`) は _normalize_cmd を
    通した綴りの語照合が拾う。引用符まで数えると `git commit -m "add csb CLI"` のような
    言及まで確認に回してしまう。
    """
    for match in _CLI_TOKEN.finditer(command):
        words = command[match.end() :].split(None, 2)
        if not words:
            continue
        if _UNRESOLVABLE_EXPANSION.search(words[0]):
            return True
        if (
            words[0] in _BOUNDARY_PAIRS
            and len(words) > 1
            and _UNRESOLVABLE_EXPANSION.search(words[1])
        ):
            return True
    return False


def _noncanonical_csb_reason(command: str) -> str:
    views = (command, _normalize_cmd(command))
    if not any(_CLI_TOKEN.search(v) for v in views):
        return ""
    if any(_RISKY_SUB_WORD.search(v) for v in views) or _dynamic_subcommand(command):
        return (
            "csb の apply / net on / create / image build (またはサブコマンドを確定できない"
            "呼び出し) を"
            "正規形 `csb <sub> …` 以外 (ラッパー・展開・連結・直接実行) で実行しようとしている"
        )
    return ""


def _raw_docker_reason(command: str) -> str:
    for view in (command, _normalize_cmd(command)):
        for segment in _SEGMENT_SPLIT.split(view):
            tokens = segment.split()
            idx = next(
                (i for i, t in enumerate(tokens) if posixpath.basename(t) == "docker"),
                None,
            )
            if idx is None:
                continue
            rest = tokens[idx + 1 :]
            words = {t for t in rest if not t.startswith("-")}
            csb_ref = any(_CSB_REF.search(t) for t in rest)
            label_ref = any(SANDBOX_LABEL in t for t in rest)
            if "network" in words and csb_ref:
                return (
                    "管理下のサンドボックスのネットワークを docker で直接操作している。"
                    "接続と切断は csb net on / off を使う"
                )
            if "cp" in words and csb_ref:
                return "管理下のサンドボックスとホストの間で docker cp している"
            if words & _DOCKER_TOUCH_VERBS and csb_ref:
                return (
                    "管理下のサンドボックスのコンテナを docker で直接操作している。"
                    "コンテナ内の実行は `csb exec ID -- ARGV` を使う"
                )
            if (words & {"run", "create"} and (csb_ref or label_ref)) or (
                words & {"build", "buildx"} and label_ref
            ):
                return (
                    "csb 以外からサンドボックスのラベル・名前・名前空間を使っている"
                    " (偽装または干渉)"
                )
    return ""


def _state_dir_reason(command: str) -> str:
    views = (command, _normalize_cmd(command))
    if not any(_STATE_DIR_REF.search(v) for v in views):
        return ""
    reason = (
        "サンドボックスの状態ディレクトリ (~/.local/state/claude-sandbox) のファイルを"
        "書き込み・適用・持ち出しする形。パッチの反映は csb apply を使う"
    )
    if ">" in command:
        return reason
    for view in views:
        names = [posixpath.basename(t) for t in _WORD_SPLIT.split(view) if t]
        if any(n in _STATE_WRITERS or n.startswith("python") for n in names):
            return reason
        if "git" in names and ("apply" in names or "am" in names):
            return reason
    return ""


# --- 静的に読めない綴りの境界 (glob・ブレース・引用符・$・\・大文字小文字・ラッパー) ---
# ASK を増やす方向にだけ使う検出で、静的 allow の文法 (host_syntax_ok + parse_csb) は
# 緩めない。fnmatch はシェルの展開の再現ではない: 当たりうるものを広めに拾うためだけに使う。
_OBFUSCATED_REASON = (
    "csb の apply / net on / create にあたりうる呼び出しを、サブコマンドを静的に確定"
    "できない形 (glob・ブレース・引用符・$・バックスラッシュ・大文字小文字・ラッパー越し)"
    " で実行しようとしている"
)
_CLI_STEMS = ("csb", "claude_sandbox", "claude_sandbox.py")
_PY_STEMS = ("claude_sandbox", "claude_sandbox.py")
_BOUNDARY_SUBS = frozenset({"apply", "create"})
# 2 語の境界 (`net on`、`image build`): 1 語目と、続かなければならない 2 語目。
_BOUNDARY_PAIRS = {"net": "on", "image": "build"}
_WORD_SPECIAL = frozenset("*?[]{}'\"$\\`")
_GLOBBY = re.compile(r"[*?\[{]")
_DYNAMIC = re.compile(r"[$`]")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=.*", re.DOTALL)
_KEYWORDS = frozenset(
    {"!", "{", "}", "if", "then", "else", "elif", "do", "while", "until", "coproc"}
)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish"})
_PYTHON = re.compile(r"python[0-9.]*")
_PY_VALUE_OPTS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
# ラッパー名 → 値を取るオプション。timeout は最初の非オプション (時間) も読み飛ばす。
_WRAPPERS: dict[str, frozenset[str]] = {
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "nohup": frozenset(),
    "exec": frozenset({"-a"}),
    "command": frozenset(),
    "builtin": frozenset(),
    "noglob": frozenset(),
    "nocorrect": frozenset(),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "xargs": frozenset(
        {"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a"}
        | {"--max-args", "--max-procs", "--max-lines", "--max-chars"}
        | {"--delimiter", "--arg-file", "--eof", "--replace"}
    ),
    "sudo": frozenset({"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U"}),
    "doas": frozenset({"-u", "-C"}),
    "caffeinate": frozenset({"-t", "-w"}),
    "arch": frozenset({"-arch", "-d", "-e"}),
    "script": frozenset({"-t", "-T", "-O", "-I", "-B", "-E", "-m"}),
    "flock": frozenset({"-w", "--timeout", "-E", "--conflict-exit-code"}),
    "watch": frozenset({"-n", "--interval", "-q", "--equexit"}),
    "unbuffer": frozenset(),
    "setsid": frozenset(),
    "taskset": frozenset(),
    "chrt": frozenset({"-T", "--sched-runtime", "-P", "--sched-period"})
    | frozenset({"-D", "--sched-deadline"}),
    "ionice": frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid"}),
}
# ラッパーの後ろで読み飛ばす位置引数の数 (timeout の時間、script / flock のファイル、
# taskset のマスク、chrt の優先度)。
_WRAPPER_POSITIONALS = {"timeout": 1, "script": 1, "flock": 1, "taskset": 1, "chrt": 1}
# 文字列をシェルのコードとして実行するラッパーのオプション (`script -c` / `flock -c`)。
_WRAPPER_CODE_OPTS = {"script": ("-c", "--command"), "flock": ("-c", "--command")}
# find / fd が後ろの語をコマンドとして実行するオプション。
_EXEC_OPTS = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir"}),
    "fd": frozenset({"-x", "--exec", "-X", "--exec-batch"}),
}
_EXEC_END = frozenset({";", "+"})
_ANSI_C = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}


_HEX_DIGITS = {width: re.compile(r"[0-9a-fA-F]{1,%d}" % width) for width in (2, 4, 8)}
_OCTAL_DIGITS = re.compile(r"[0-7]{1,3}")


def _ansi_c_word(text: str, i: int) -> tuple[str, int]:
    """`$'…'` の中身 (i は開きの `'` の次) を復号し、(文字列, 閉じの次の位置) を返す。"""
    out: list[str] = []
    n = len(text)
    while i < n and text[i] != "'":
        ch = text[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in _ANSI_C:
            out.append(_ANSI_C[nxt])
            i += 2
            continue
        width = {"x": 2, "u": 4, "U": 8}.get(nxt)
        if width:
            digits = _HEX_DIGITS[width].match(text, i + 2)
            if digits:
                out.append(chr(min(int(digits.group(), 16), 0x10FFFF)))
                i += 2 + len(digits.group())
                continue
        octal = _OCTAL_DIGITS.match(text, i + 1)
        if octal:
            out.append(chr(int(octal.group(), 8) & 0xFF))
            i += 1 + len(octal.group())
            continue
        out.append(text[i : i + 2])
        i += 2
    return "".join(out), i + 1


def _shell_words(text: str) -> list[tuple[str, bool]]:
    """(引用符・`\\`・`$'…'` を復号した語, 特殊文字を含んでいたか) の列。

    展開はしない (`$x` や glob はそのまま残る)。閉じていない引用符は末尾までとみなす。
    """
    words: list[tuple[str, bool]] = []
    buf: list[str] = []
    special = in_word = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            if in_word:
                words.append(("".join(buf), special))
                buf, special, in_word = [], False, False
            i += 1
            continue
        in_word = True
        if ch == "\\":
            special = True
            buf.append(text[i + 1 : i + 2])
            i += 2
        elif ch == "'":
            special = True
            end = text.find("'", i + 1)
            end = n if end < 0 else end
            buf.append(text[i + 1 : end])
            i = end + 1
        elif ch == "$" and text[i + 1 : i + 2] == "'":
            special = True
            decoded, i = _ansi_c_word(text, i + 2)
            buf.append(decoded)
        elif ch == '"':
            special = True
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n and text[j + 1] in '"\\$`':
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            i = j + 1
        else:
            special = special or ch in _WORD_SPECIAL
            buf.append(ch)
            i += 1
    if in_word:
        words.append(("".join(buf), special))
    return words


def _quoted_segments(text: str) -> list[str]:
    """引用符の外の `; & | ( ) 改行 ` $(` で区切った単純コマンドの並び (近似)。"""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            buf.append(text[i : i + 2])
            i += 2
            continue
        if ch == "'" or (ch == "$" and text[i + 1 : i + 2] == "'") or ch == '"':
            quote = '"' if ch == '"' else "'"
            j = i + (2 if ch == "$" else 1)
            while j < n and text[j] != quote:
                j += 2 if text[j] == "\\" and quote == '"' else 1
            buf.append(text[i : j + 1])
            i = j + 1
            continue
        if ch in ";&|()\n\r`" or (ch == "$" and text[i + 1 : i + 2] == "("):
            out.append("".join(buf))
            buf = []
            i += 2 if ch == "$" else 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [seg for seg in out if seg.strip()]


def _brace_alternatives(word: str, limit: int = 64) -> list[str]:
    """素朴なブレース展開 ({a,b})。展開できない `{…}` は `*` に置き換えた形も足す。"""
    match = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if match is None:
        loose = re.sub(r"\{[^{}]*\}", "*", word)
        return [word] if loose == word else [word, loose]
    out: list[str] = []
    for alt in match.group(1).split(","):
        for expanded in _brace_alternatives(
            word[: match.start()] + alt + word[match.end() :], limit
        ):
            out.append(expanded)
            if len(out) >= limit:
                return out
    return out


def _cli_like(
    word: tuple[str, bool], *, glob_ok: bool, stems: Sequence[str] = _CLI_STEMS
) -> bool:
    base = word[0].rsplit("/", 1)[-1].lower()
    if base in stems:
        return True
    if not (glob_ok and _GLOBBY.search(base)):
        return False
    return any(
        fnmatch.fnmatchcase(stem, alt)
        for alt in _brace_alternatives(base)
        for stem in stems
    )


def _boundary_after(words: list[tuple[str, bool]], k: int, stdin_args: bool) -> bool:
    """words[k] を CLI の語とみて、境界 (apply / create / net on / image build) にあたりうるか。"""
    rest = words[k + 1 :]
    if not rest:
        return stdin_args
    sub, sub_special = rest[0]
    if words[k][1] or sub_special or sub in _BOUNDARY_SUBS:
        return True
    if sub not in _BOUNDARY_PAIRS:
        return False
    if len(rest) < 2:
        return stdin_args
    return rest[1][1] or rest[1][0] == _BOUNDARY_PAIRS[sub]


def _skip_wrapper(words: list[tuple[str, bool]], i: int, name: str) -> int:
    opts = _WRAPPERS[name]
    positional = _WRAPPER_POSITIONALS.get(name, 0)
    i += 1
    while i < len(words):
        word = words[i][0]
        if word == "--":
            return i + 1
        if word.startswith("-") and len(word) > 1:
            i += 2 if word in opts else 1
        elif name == "env" and _ASSIGNMENT.fullmatch(word):
            i += 1
        elif positional:
            positional -= 1
            i += 1
        else:
            break
    return i


def _shell_code(words: list[tuple[str, bool]], i: int) -> str | None:
    """`sh|bash|zsh … -c CODE` の CODE (無ければ None)。"""
    has_c = False
    j = i + 1
    while j < len(words):
        word = words[j][0]
        if word in ("-o", "+o", "-O", "+O"):
            j += 2
            continue
        if word[:1] in ("-", "+") and len(word) > 1 and word != "--":
            has_c = has_c or "c" in word[1:]
            j += 1
            continue
        break
    return words[j][0] if has_c and j < len(words) else None


def _skip_python_opts(words: list[tuple[str, bool]], j: int) -> int:
    while j < len(words) and words[j][0].startswith("-") and words[j][0] != "-":
        if words[j][0] in ("-c", "-m"):
            return j
        j += 2 if words[j][0] in _PY_VALUE_OPTS else 1
    return j


def _segment_asks(words: list[tuple[str, bool]], depth: int) -> bool:
    i = 0
    while i < len(words) and (
        words[i][0] in _KEYWORDS or _ASSIGNMENT.fullmatch(words[i][0])
    ):
        i += 1
    stdin_args = False
    while i < len(words) and words[i][0].rsplit("/", 1)[-1] in _WRAPPERS:
        name = words[i][0].rsplit("/", 1)[-1]
        stdin_args = stdin_args or name == "xargs"
        code = _wrapper_code(words, i, name)
        if code is not None and depth < 3 and _obfuscated_boundary(code, depth + 1):
            return True
        i = _skip_wrapper(words, i, name)
    if i >= len(words):
        return False
    name = words[i][0].rsplit("/", 1)[-1].lower()
    if name in _EXEC_OPTS and depth < 3 and _exec_asks(words, i, name, depth):
        return True
    if name in _SHELLS:
        code = _shell_code(words, i)
        if code is not None and depth < 3 and _obfuscated_boundary(code, depth + 1):
            return True
    if _PYTHON.fullmatch(name):
        j = _skip_python_opts(words, i + 1)
        if j < len(words) and words[j][0] == "-m":
            j += 1
        if j < len(words) and _cli_like(words[j], glob_ok=True, stems=_PY_STEMS):
            return _boundary_after(words, j, stdin_args)
    if _cli_like(words[i], glob_ok=True):
        return _boundary_after(words, i, stdin_args)
    if _DYNAMIC.search(words[i][0]) and len(words) > i + 1:
        sub = words[i + 1][0]
        nxt = words[i + 2][0] if len(words) > i + 2 else ""
        if sub in _BOUNDARY_SUBS or _BOUNDARY_PAIRS.get(sub) == nxt:
            return True
    # 後ろの語: CLI の語そのもの (語幹・大文字小文字・復号) なら同じ規則で見る。glob や
    # ブレースで CLI に当たりうる語は、すぐ後に境界のサブコマンドがそのまま続くときだけ
    # 数える (`cp -r * /tmp/x` のような日常の glob を CLI とみなさない)。
    return any(
        (_cli_like(words[k], glob_ok=False) and _boundary_after(words, k, False))
        or (_cli_like(words[k], glob_ok=True) and _boundary_next(words, k))
        for k in range(i + 1, len(words))
    )


def _could_be(word: tuple[str, bool], names: Sequence[str]) -> bool:
    """word (glob・ブレース・引用符を含みうる) が names のどれかに当たりうるか。"""
    text = word[0].lower()
    if not word[1]:
        return text in names
    return any(
        fnmatch.fnmatchcase(name, alt)
        for alt in _brace_alternatives(text)
        for name in names
    )


def _boundary_next(words: list[tuple[str, bool]], k: int) -> bool:
    """words[k] の直後が境界のサブコマンド (apply / create / net on / image build) か。

    glob で CLI に当たりうる後ろの語にだけ使う。後ろの語は日常の glob (`cp -r * x`)
    とも当たるので、サブコマンドの語は「そのままか、fnmatch で当たりうる」ときだけ数える。
    """
    rest = words[k + 1 :]
    if not rest:
        return False
    if _could_be(rest[0], tuple(_BOUNDARY_SUBS)):
        return True
    return len(rest) > 1 and any(
        _could_be(rest[0], (first,)) and _could_be(rest[1], (second,))
        for first, second in _BOUNDARY_PAIRS.items()
    )


def _wrapper_code(words: list[tuple[str, bool]], i: int, name: str) -> str | None:
    """`script -c CODE` / `flock … -c CODE` の CODE、`watch ARGS…` の連結 (無ければ None)。

    watch は `-x` / `--exec` が無ければ、残りの引数をつないで `sh -c` に渡す。
    """
    if name == "watch":
        j = _skip_wrapper(words, i, name)
        if {w for w, _ in words[i + 1 : j]} & {"-x", "--exec"} or j >= len(words):
            return None
        return " ".join(w for w, _ in words[j:])
    opts = _WRAPPER_CODE_OPTS.get(name, ())
    for j in range(i + 1, len(words) - 1):
        if words[j][0] in opts:
            return words[j + 1][0]
    return None


def _exec_asks(words: list[tuple[str, bool]], i: int, name: str, depth: int) -> bool:
    """`find … -exec CMD … ;` / `fd -x CMD …` の CMD を、コマンドの位置として見る。"""
    j = i + 1
    while j < len(words):
        if words[j][0] not in _EXEC_OPTS[name]:
            j += 1
            continue
        end = j + 1
        while end < len(words) and words[end][0] not in _EXEC_END:
            end += 1
        if _segment_asks(words[j + 1 : end], depth + 1):
            return True
        j = end
    return False


def _obfuscated_boundary(command: str, depth: int = 0) -> bool:
    segments = dict.fromkeys(_quoted_segments(command) + _SEGMENT_SPLIT.split(command))
    return any(_segment_asks(_shell_words(seg), depth) for seg in segments)


def _obfuscated_csb_reason(command: str) -> str:
    return _OBFUSCATED_REASON if _obfuscated_boundary(command) else ""


def classify_sandbox_command(command: str) -> tuple[str, str]:
    """フック向けの判定 ("allow" | "ask" | "", 理由)。"" は通常のレビューに回す。

    1. 構文が単純で `csb` の正規形に解析できるもの: create / net on / apply は ask、
       それ以外は allow (サンドボックスの中で完結する)。
    2. 正規形でない `csb` / claude_sandbox(.py) で、apply / net on / create に見える
       もの、またはサブコマンドを確定できないもの (glob・ブレース・引用符・$・バックスラッシュ・
       大文字小文字・ラッパー越しの綴り。_obfuscated_csb_reason): ask。
    3. 管理下のサンドボックスに触れる素の docker (exec / cp / network / 偽装): ask。
    4. 状態ディレクトリのパッチや記録を書き込み・適用する形: ask。

    2〜4 は部分一致の保守的な判定で、結果は ask にしかならない (allow を広げない)。
    """
    if not isinstance(command, str):
        return "", ""
    if host_syntax_ok(command):
        cmd = parse_csb(command)
        if cmd is not None:
            if cmd.sub in ("net on", "apply", "image build") or (
                cmd.sub == "create" and CREATE_REQUIRES_CONFIRMATION
            ):
                return "ask", _ASK_REASONS[cmd.sub]
            return "allow", f"csb {cmd.sub}: サンドボックスの中で完結する操作"
    for check in (
        _noncanonical_csb_reason,
        _obfuscated_csb_reason,
        _raw_docker_reason,
        _state_dir_reason,
    ):
        reason = check(command)
        if reason:
            return "ask", reason
    return "", ""


# ---------------------------------------------------------------------------
# 6. パッチの解析と apply の最終確認
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PatchFile:
    """パッチ内の 1 ファイル。status は added / deleted / renamed / copied / modified。"""

    status: str
    old_path: str | None
    new_path: str | None
    old_mode: str | None
    new_mode: str | None
    added: int
    removed: int
    binary: bool
    symlink_target: str | None

    @property
    def path(self) -> str:
        return self.new_path if self.new_path is not None else (self.old_path or "")


@dataclass(frozen=True)
class PatchSummary:
    """analyze_patch の結果。errors / invalid_paths / submodules / too_large は apply 不可。"""

    sha256: str
    size: int
    files: tuple[PatchFile, ...] = ()
    added: int = 0
    removed: int = 0
    errors: tuple[str, ...] = ()
    invalid_paths: tuple[tuple[str, str], ...] = ()
    risky: tuple[tuple[str, tuple[str, ...]], ...] = ()
    secrets: tuple[tuple[str, str], ...] = ()
    secrets_scanned: bool = False
    too_large: bool = False

    @property
    def binary_files(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if f.binary)

    @property
    def executable_files(self) -> tuple[str, ...]:
        return tuple(
            f.path
            for f in self.files
            if f.new_mode == "100755" and f.old_mode != "100755"
        )

    @property
    def symlinks(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if f.new_mode == "120000")

    @property
    def submodules(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if "160000" in (f.old_mode, f.new_mode))


RISKY_LABELS = {
    "harness": "ハーネス設定",
    "ci": "CI",
    "deps": "依存マニフェスト / lock",
    "hooks": "git フック / 自動実行設定",
    "container": "コンテナ定義",
}

_HARNESS_ROOTS = frozenset({".claude", ".codex", ".gemini"})
_HARNESS_FILES = frozenset(
    {"claude.md", "claude.local.md", "agents.md", "gemini.md", ".mcp.json"}
)
_CI_ROOTS = frozenset({".github", ".gitlab", ".circleci", ".buildkite", ".woodpecker"})
_CI_FILES = frozenset(
    {
        ".gitlab-ci.yml",
        ".travis.yml",
        "azure-pipelines.yml",
        "jenkinsfile",
        "bitbucket-pipelines.yml",
        ".drone.yml",
        "appveyor.yml",
        ".woodpecker.yml",
        "cloudbuild.yaml",
        "cloudbuild.yml",
    }
)
_DEP_FILES = frozenset(
    {
        "package.json",
        "npm-shrinkwrap.json",
        "pnpm-workspace.yaml",
        ".npmrc",
        ".yarnrc",
        ".yarnrc.yml",
        ".pnpmfile.cjs",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "pip.conf",
        "uv.toml",
        "go.mod",
        "go.sum",
        "go.work",
        "go.work.sum",
        "cargo.toml",
        "gemfile",
        "composer.json",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "podfile",
        "package.swift",
        "package.resolved",
        "mix.exs",
        "deno.json",
        "deno.jsonc",
        "flake.nix",
    }
)
_DEP_LOCK = re.compile(r"(?:^|[._-])lock(?:file)?(?:\.[a-z0-9]+)?$|\.lockb?$")
_DEP_REQUIREMENTS = re.compile(
    r"(?:^|[._-])(?:requirements|constraints)(?:[._-][\w.-]*)?\.(?:txt|in)$"
)
_HOOK_ROOTS = frozenset({".githooks", ".husky"})
_HOOK_FILES = frozenset(
    {
        ".pre-commit-config.yaml",
        ".pre-commit-hooks.yaml",
        ".gitattributes",
        ".gitmodules",
        ".gitconfig",
        ".envrc",
        "lefthook.yml",
        "lefthook.yaml",
        ".lefthook.yml",
        ".lefthook.yaml",
    }
)
_HOOK_PATHS = frozenset({".vscode/tasks.json", ".vscode/settings.json"})
_CONTAINER_FILE = re.compile(
    r"(?:dockerfile|containerfile)(?:[._-].*)?|.+\.(?:dockerfile|containerfile)"
    r"|(?:docker-)?compose(?:[._-][\w.-]*)?\.ya?ml|\.devcontainer\.json|\.dockerignore"
)


def risk_category(path: str) -> str:
    """パスの RISKY 分類 (RISKY_LABELS のキー)。該当しなければ ""。

    名前は大文字小文字と HFS+ が無視する文字を畳んでから比べる: ホスト (APFS と
    core.ignorecase) では `.CLAUDE/agents/x.md` も `.claude/agents/x.md` に書き込まれる。
    `.claude` / `.codex` / `.gemini` / `.devcontainer` はどの深さでも数える。
    """
    parts = [_IGNORABLE.sub("", p).lower() for p in path.split("/")]
    folded = "/".join(parts)
    base = parts[-1]
    if (
        _is_harness_target(path)
        or _is_harness_target(folded)
        or any(p in _HARNESS_ROOTS for p in parts)
        or base in _HARNESS_FILES
    ):
        return "harness"
    if parts[0] in _CI_ROOTS or base in _CI_FILES:
        return "ci"
    if (
        base in _DEP_FILES
        or _DEP_LOCK.search(base)
        or _DEP_REQUIREMENTS.search(base)
        or (len(parts) > 1 and parts[-2] == "requirements")
    ):
        return "deps"
    if parts[0] in _HOOK_ROOTS or base in _HOOK_FILES or folded in _HOOK_PATHS:
        return "hooks"
    if _CONTAINER_FILE.fullmatch(base) or ".devcontainer" in parts:
        return "container"
    return ""


def _char_class(ranges: Iterable[tuple[int, int]]) -> re.Pattern[str]:
    return re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges) + "]")


# 制御文字・双方向制御・ゼロ幅文字・行/段落区切り・サロゲート (surrogateescape で戻した
# 非 UTF-8 のバイト)。表示の偽装に使えるので人に見せる前にエスケープする。
_UNSAFE_DISPLAY = _char_class(
    (
        (0x00, 0x1F),
        (0x7F, 0x9F),
        (0x061C, 0x061C),
        (0x200B, 0x200F),
        (0x2028, 0x202E),
        (0x2060, 0x206F),
        (0xFEFF, 0xFEFF),
        (0xD800, 0xDFFF),
    )
)
# HFS+ が無視する文字。`.git` の間に U+200C を挟んだ名前は macOS で `.git` に解決される。
_IGNORABLE = _char_class(
    ((0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0xFEFF, 0xFEFF))
)


# 空白に見える・見えない文字のうち、_UNSAFE_DISPLAY の範囲の外にあるもの: Hangul /
# Braille の filler。カテゴリで拾うもの (U+0020 以外の Zs、Cf、Zl / Zp、タグ文字) と
# 異体字セレクタは _display_unsafe が見る。
_DISPLAY_FILLERS = frozenset({0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0})


def _display_unsafe(ch: str) -> bool:
    code = ord(ch)
    if _UNSAFE_DISPLAY.match(ch) or code in _DISPLAY_FILLERS:
        return True
    category = unicodedata.category(ch)
    return (
        (category == "Zs" and ch != " ")
        or category in ("Cf", "Zl", "Zp", "Cc", "Cs")
        or 0xFE00 <= code <= 0xFE0F
        or 0xE0100 <= code <= 0xE01EF
        or 0x180B <= code <= 0x180F
        or 0xE0000 <= code <= 0xE007F
    )


def _display_escape(ch: str) -> str:
    code = ord(ch)
    if code < 0x100:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def display_text(text: str, limit: int = 160) -> str:
    """人に見せる文字列の表示偽装に使える文字を `\\x..` / `\\u....` / `\\U........` にする。

    端末制御・双方向制御・ゼロ幅に加え、U+0020 以外の空白 (Zs)、書式文字 (Cf)、
    Hangul / Braille の filler、タグ文字、異体字セレクタもエスケープする: どれも
    setup の本文や理由文を見た目の上でずらし、別の行や空欄に見せられる。
    """
    if text.isascii():
        out = _UNSAFE_DISPLAY.sub(lambda m: _display_escape(m.group()), text)
    else:
        out = "".join(_display_escape(ch) if _display_unsafe(ch) else ch for ch in text)
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _path_problem(path: str) -> str:
    if not path:
        return "空のパス"
    if _CONTROL.search(path):
        return "制御文字を含む"
    if path.startswith("/"):
        return "絶対パス"
    if "\\" in path:
        return "バックスラッシュを含む"
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            return "空 / `.` / `..` のパス要素"
        folded = _IGNORABLE.sub("", segment).lower().rstrip(". ")
        if folded in (".git", "git~1"):
            return "`.git` を含む"
    return ""


def _symlink_problem(link_path: str, target: str) -> str:
    if not target or _CONTROL.search(target):
        return "symlink の行き先が空か制御文字を含む"
    if target.startswith("/"):
        return "symlink の行き先が絶対パス"
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(link_path), target))
    if resolved == ".." or resolved.startswith("../"):
        return "symlink の行き先がリポジトリの外"
    if resolved != "." and _path_problem(resolved):
        return "symlink の行き先が不正 (" + _path_problem(resolved) + ")"
    return ""


_C_ESCAPES = {
    "a": 7,
    "b": 8,
    "t": 9,
    "n": 10,
    "v": 11,
    "f": 12,
    "r": 13,
    '"': 34,
    "\\": 92,
}
_OCTAL3 = re.compile(r"[0-3][0-7]{2}")


def _c_unquote(text: str, start: int) -> tuple[str, int] | None:
    """text[start] の `"` から git の C 風クォートを読み、(値, 閉じ `"` の次) を返す。"""
    out = bytearray()
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return out.decode("utf-8", "surrogateescape"), i + 1
        if ch != "\\":
            out += ch.encode("utf-8", "surrogateescape")
            i += 1
            continue
        esc = text[i + 1 : i + 2]
        if esc in _C_ESCAPES:
            out.append(_C_ESCAPES[esc])
            i += 2
        elif _OCTAL3.fullmatch(text[i + 1 : i + 4]):
            out.append(int(text[i + 1 : i + 4], 8))
            i += 4
        else:
            return None
    return None


def _parse_name(text: str) -> str | None:
    if not text.startswith('"'):
        return text
    parsed = _c_unquote(text, 0)
    if parsed is None or parsed[1] != len(text):
        return None
    return parsed[0]


def _header_names(rest: str) -> tuple[str, str] | None:
    """`diff --git ` の後ろの 2 つの名前。曖昧なら None (他のヘッダ行に頼る)。"""
    if rest.startswith('"'):
        parsed = _c_unquote(rest, 0)
        if parsed is None or rest[parsed[1] : parsed[1] + 1] != " ":
            return None
        new = _parse_name(rest[parsed[1] + 1 :])
        return None if new is None else (parsed[0], new)
    if rest.endswith('"'):
        k = rest.find(' "')
        while k >= 0:
            parsed = _c_unquote(rest, k + 1)
            if parsed is not None and parsed[1] == len(rest):
                return rest[:k], parsed[0]
            k = rest.find(' "', k + 1)
        return None
    candidates = []
    k = rest.find(" b/")
    while k >= 0:
        candidates.append((rest[:k], rest[k + 1 :]))
        k = rest.find(" b/", k + 1)
    if len(candidates) == 1:
        return candidates[0]
    same = [c for c in candidates if c[0][2:] == c[1][2:]]
    return same[0] if len(same) == 1 else None


_HUNK = re.compile(r"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?: .*)?")
_INDEX = re.compile(r"[0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: ([0-7]{6}))?")
_BINARY_BLOCK = re.compile(rb"(?:literal|delta) [0-9]+")
_BASE85_LINE = re.compile(rb"[A-Za-z][0-9A-Za-z!#$%&()*+;<=>?@^_`{|}~-]+")
_MODES = frozenset({"100644", "100755", "120000", "160000"})
_EXT_HEADERS = (
    "old mode",
    "new mode",
    "deleted file mode",
    "new file mode",
    "similarity index",
    "dissimilarity index",
    "rename from",
    "rename to",
    "copy from",
    "copy to",
    "index",
)


class _PatchError(Exception):
    pass


class _FileBuilder:
    def __init__(self, names: tuple[str, str] | None) -> None:
        self.names = names
        self.fields: dict[str, str] = {}
        self.minus: str | None = None
        self.plus: str | None = None
        self.hunks = 0
        self.added = 0
        self.removed = 0
        self.binary = False
        self.added_lines: list[bytes] = []

    def header(self, text: str, lineno: int) -> None:
        for key in _EXT_HEADERS:
            if text.startswith(key + " "):
                if key in self.fields or self.minus is not None:
                    raise _PatchError(f"{lineno} 行目: ヘッダの位置か重複が不正")
                self.fields[key] = text[len(key) + 1 :]
                return
        raise _PatchError(f"{lineno} 行目: 未知のヘッダ行")

    @staticmethod
    def _side(label: str | None, prefix: str) -> str | None:
        if label is None or label == "/dev/null":
            return None
        if not label.startswith(prefix):
            raise _PatchError(f"--- / +++ のパスに {prefix} が無い")
        return label[2:]

    def finish(self) -> PatchFile:
        f = self.fields
        is_new = "new file mode" in f
        is_del = "deleted file mode" in f
        rename = "rename from" in f or "rename to" in f
        copy = "copy from" in f or "copy to" in f
        if sum((is_new, is_del, rename, copy)) > 1:
            raise _PatchError("相反するヘッダ (追加・削除・rename・copy)")
        for a, b in (("rename from", "rename to"), ("copy from", "copy to")):
            if (a in f) != (b in f):
                raise _PatchError(f"{a} と {b} が揃っていない")
        if ("old mode" in f) != ("new mode" in f):
            raise _PatchError("old mode と new mode が揃っていない")
        if self.plus is not None and self.hunks == 0:
            raise _PatchError("--- / +++ の後に hunk が無い")
        if is_new and self.minus not in (None, "/dev/null"):
            raise _PatchError("新規ファイルの --- が /dev/null ではない")
        if is_del and self.plus not in (None, "/dev/null"):
            raise _PatchError("削除ファイルの +++ が /dev/null ではない")
        if not is_new and self.minus == "/dev/null":
            raise _PatchError("--- が /dev/null なのに新規ファイルではない")
        if not is_del and self.plus == "/dev/null":
            raise _PatchError("+++ が /dev/null なのに削除ファイルではない")

        old_c: list[str | None] = []
        new_c: list[str | None] = []
        if self.names is not None:
            old, new = self.names
            if not (old.startswith("a/") and new.startswith("b/")):
                raise _PatchError("diff --git の名前に a/ b/ が無い")
            old_c.append(old[2:])
            new_c.append(new[2:])
            if is_new:
                new_c.append(old[2:])
            if is_del:
                old_c.append(new[2:])
        old_c.append(self._side(self.minus, "a/"))
        new_c.append(self._side(self.plus, "b/"))
        for kind in ("rename", "copy"):
            if f"{kind} from" in f:
                old_c.append(_parse_name(f[f"{kind} from"]) or "")
                new_c.append(_parse_name(f[f"{kind} to"]) or "")
        old_path = None if is_new else _unique(old_c, "変更前")
        new_path = None if is_del else _unique(new_c, "変更後")

        index_mode = None
        if "index" in f:
            m = _INDEX.fullmatch(f["index"])
            if m is None:
                raise _PatchError("index 行が不正")
            index_mode = m.group(1)
        old_mode = (
            None
            if is_new
            else (f.get("old mode") or f.get("deleted file mode") or index_mode)
        )
        new_mode = (
            None
            if is_del
            else (f.get("new mode") or f.get("new file mode") or index_mode)
        )
        for mode in (old_mode, new_mode):
            if mode is not None and mode not in _MODES:
                raise _PatchError(f"未知のファイルモード {display_text(mode, 12)}")
        if rename:
            status = "renamed"
        elif copy:
            status = "copied"
        elif is_new:
            status = "added"
        elif is_del:
            status = "deleted"
        else:
            status = "modified"
        target = None
        if new_mode == "120000" and len(self.added_lines) == 1:
            target = self.added_lines[0].decode("utf-8", "surrogateescape")
        return PatchFile(
            status=status,
            old_path=old_path,
            new_path=new_path,
            old_mode=old_mode,
            new_mode=new_mode,
            added=self.added,
            removed=self.removed,
            binary=self.binary,
            symlink_target=target,
        )


def _unique(candidates: list[str | None], side: str) -> str:
    values = {c for c in candidates if c is not None}
    if not values:
        raise _PatchError(f"{side}のファイル名を決められない")
    if len(values) > 1:
        raise _PatchError(f"{side}のファイル名が一致しない")
    return values.pop()


def _label(text: str, lineno: int) -> str:
    # git は空白を含む名前の後ろに TAB を付ける (diff.c の name_a_tab)。
    name = _parse_name(text[:-1] if text.endswith("\t") else text)
    if name is None:
        raise _PatchError(f"{lineno} 行目: クォートされた名前が不正")
    return name


def _parse_patch(lines: list[bytes]) -> tuple[list[tuple[PatchFile, list[bytes]]], str]:
    """git diff を git apply と同じ規則で読む。(ファイル群, 最初のエラー) を返す。

    hunk は件数どおりに読む。ファイルの外で知らない行 (先頭の説明文、ファイル間の
    ゴミ、`diff --git` の無い伝統的な diff) は全てエラーにする: git apply が適用する
    伝統的 diff を見落とさないため。エラーの文面は行番号だけでパッチの中身を含めない。
    """
    files: list[tuple[PatchFile, list[bytes]]] = []
    cur: _FileBuilder | None = None
    state = "outside"
    old_left = new_left = 0
    i = 0

    def close() -> None:
        nonlocal cur
        if cur is not None:
            files.append((cur.finish(), cur.added_lines))
            cur = None

    try:
        while i < len(lines):
            raw = lines[i]
            lineno = i + 1
            if state == "hunk":
                if old_left == 0 and new_left == 0:
                    state = "after_hunk"
                    continue
                assert cur is not None  # nosec B101
                tag = raw[:1]
                if tag in (b" ", b""):
                    old_left -= 1
                    new_left -= 1
                elif tag == b"-":
                    old_left -= 1
                    cur.removed += 1
                elif tag == b"+":
                    new_left -= 1
                    cur.added += 1
                    cur.added_lines.append(raw[1:])
                elif tag != b"\\":
                    raise _PatchError(f"{lineno} 行目: hunk 内の不正な行")
                if old_left < 0 or new_left < 0:
                    raise _PatchError(f"{lineno} 行目: hunk の行数が合わない")
                i += 1
                continue
            text = raw.decode("utf-8", "surrogateescape")
            if state in ("binary_data", "binary_data2"):
                if raw == b"":
                    state = "binary_between" if state == "binary_data" else "done"
                elif not _BASE85_LINE.fullmatch(raw):
                    raise _PatchError(f"{lineno} 行目: binary パッチのデータが不正")
                i += 1
                continue
            if state == "binary_start":
                if not _BINARY_BLOCK.fullmatch(raw):
                    raise _PatchError(f"{lineno} 行目: binary パッチの見出しが不正")
                state = "binary_data"
                i += 1
                continue
            if state == "binary_between" and _BINARY_BLOCK.fullmatch(raw):
                state = "binary_data2"
                i += 1
                continue
            if state == "after_hunk" and text.startswith("\\"):
                i += 1
                continue
            if state in ("after_hunk", "header") and text.startswith("@@"):
                assert cur is not None  # nosec B101
                m = _HUNK.fullmatch(text)
                if cur.minus is None or cur.plus is None or m is None:
                    raise _PatchError(f"{lineno} 行目: hunk の見出しが不正")
                old_left = 1 if m.group(2) is None else int(m.group(2))
                new_left = 1 if m.group(4) is None else int(m.group(4))
                if old_left == 0 and new_left == 0:
                    raise _PatchError(f"{lineno} 行目: 空の hunk")
                cur.hunks += 1
                state = "hunk"
                i += 1
                continue
            if text.startswith("diff "):
                close()
                if not text.startswith("diff --git "):
                    raise _PatchError(f"{lineno} 行目: git 形式以外の diff")
                cur = _FileBuilder(_header_names(text[len("diff --git ") :]))
                state = "header"
                i += 1
                continue
            if state != "header":
                raise _PatchError(f"{lineno} 行目: パッチの外に不正な行")
            assert cur is not None  # nosec B101
            if text.startswith("--- "):
                if cur.minus is not None:
                    raise _PatchError(f"{lineno} 行目: --- が重複")
                cur.minus = _label(text[4:], lineno)
            elif text.startswith("+++ "):
                if cur.minus is None or cur.plus is not None:
                    raise _PatchError(f"{lineno} 行目: +++ の位置が不正")
                cur.plus = _label(text[4:], lineno)
            elif text == "GIT binary patch":
                cur.binary = True
                state = "binary_start"
            elif text.startswith("Binary files "):
                raise _PatchError(
                    f"{lineno} 行目: binary の中身が無い (--binary で作る)"
                )
            else:
                cur.header(text, lineno)
            i += 1
        if state == "hunk" and (old_left or new_left):
            raise _PatchError("最後の hunk が途中で切れている")
        if state in ("binary_start", "binary_data", "binary_data2"):
            raise _PatchError("binary パッチが途中で切れている")
        close()
    except _PatchError as exc:
        return files, str(exc)
    return files, ""


def analyze_patch(patch: bytes) -> PatchSummary:
    """`git diff --binary --full-index` の出力 (a/ b/ 接頭辞付き) を解析する。

    4 MiB を超えるパッチは解析しない (too_large)。秘密スキャンは追加行だけに掛け、
    種別ラベルだけを残す (値は残さない)。
    """
    sha = sha256_hex(patch)
    if len(patch) > PATCH_SUMMARY_LIMIT:
        return PatchSummary(sha256=sha, size=len(patch), too_large=True)
    lines = patch.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    parsed, error = _parse_patch(lines)
    invalid: list[tuple[str, str]] = []
    risky: dict[str, list[str]] = {}
    secrets: list[tuple[str, str]] = []
    for pf, added_lines in parsed:
        for path in dict.fromkeys(
            p for p in (pf.old_path, pf.new_path) if p is not None
        ):
            problem = _path_problem(path)
            if problem:
                invalid.append((path, problem))
            category = risk_category(path)
            if category and path not in risky.setdefault(category, []):
                risky[category].append(path)
        if pf.new_mode == "120000" and pf.new_path is not None:
            if pf.symlink_target is None:
                problem = "symlink の行き先を検証できない"
            else:
                problem = _symlink_problem(pf.new_path, pf.symlink_target)
                # リポジトリ内のハーネス設定などを指す link は、link 自体を同じ分類に
                # 数える: apply 後に link へ書くと、保護パスを書かずに行き先が変わる。
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(pf.new_path), pf.symlink_target)
                )
                category = risk_category(resolved)
                if category and pf.new_path not in risky.setdefault(category, []):
                    risky[category].append(pf.new_path)
            if problem:
                invalid.append((pf.new_path, problem))
        if added_lines and not pf.binary:
            text = "\n".join(b.decode("utf-8", "replace") for b in added_lines)
            hit, label = scan_secrets(text, None)
            if hit:
                secrets.append((pf.path, label))
    return PatchSummary(
        sha256=sha,
        size=len(patch),
        files=tuple(pf for pf, _ in parsed),
        added=sum(pf.added for pf, _ in parsed),
        removed=sum(pf.removed for pf, _ in parsed),
        errors=(error,) if error else (),
        invalid_paths=tuple(invalid),
        risky=tuple((c, tuple(risky[c])) for c in RISKY_LABELS if c in risky),
        secrets=tuple(secrets),
        secrets_scanned=True,
    )


def required_reviews(summary: PatchSummary) -> list[str]:
    """apply に必要なレビュー。RISKY・秘密の検出・未解析のときは security-reviewer も。"""
    out = list(REQUIRED_REVIEWERS)
    if summary.risky or summary.secrets or not summary.secrets_scanned:
        out.append(SECURITY_REVIEWER)
    return out


_VERIFY_KEYS = frozenset(
    {
        "schema",
        "id",
        "patch_sha256",
        "base_tree",
        "setup_image_id",
        "argv",
        "exit_code",
        "finished_at",
    }
)
_ATTEST_KEYS = frozenset(
    {
        "schema",
        "patch_sha256",
        "reviewer",
        "result",
        "unresolved",
        "ref",
        "recorded_at",
    }
)


def _verify_record_ok(record: Any) -> bool:
    if not (isinstance(record, dict) and set(record) == _VERIFY_KEYS):
        return False
    argv = record["argv"]
    return (
        _is_int(record["schema"])
        and record["schema"] == VERIFY_SCHEMA
        and is_sandbox_id(record["id"])
        and _matches(_SHA256, record["patch_sha256"])
        and _matches(_GIT_OID, record["base_tree"])
        and _matches(_IMAGE_ID, record["setup_image_id"])
        and isinstance(argv, list)
        and bool(argv)
        and all(isinstance(a, str) for a in argv)
        and _is_int(record["exit_code"])
        and _is_number(record["finished_at"])
    )


def verify_status(record: Any, sha: str, meta: Any) -> str:
    """verify 記録の状態: PASS / FAIL / MISSING (無い・壊れている) / STALE (別のパッチ・base)。"""
    if not _verify_record_ok(record):
        return "MISSING"
    m = meta if isinstance(meta, dict) else {}
    if (
        record["patch_sha256"] != sha
        or record["id"] != m.get("id")
        or record["base_tree"] != m.get("base_tree")
        or record["setup_image_id"] != m.get("setup_image_id")
    ):
        return "STALE"
    return "PASS" if record["exit_code"] == 0 else "FAIL"


def verify_record_valid(record: Any, sha: str, meta: Any) -> bool:
    """record が、この sha と meta に結びついた exit 0 の verify 記録か。"""
    return verify_status(record, sha, meta) == "PASS"


def _attest_ok(record: Any) -> bool:
    if not (isinstance(record, dict) and set(record) == _ATTEST_KEYS):
        return False
    ref = record["ref"]
    ref_ok = ref is None or (
        isinstance(ref, dict)
        and set(ref) == {"path", "sha256"}
        and isinstance(ref["path"], str)
        and ref["path"] != ""
        and not _CONTROL.search(ref["path"])
        and _matches(_SHA256, ref["sha256"])
    )
    return (
        _is_int(record["schema"])
        and record["schema"] == ATTEST_SCHEMA
        and _matches(_SHA256, record["patch_sha256"])
        and _matches(_REVIEWER, record["reviewer"])
        and record["result"] in ("pass", "fail")
        and _is_int(record["unresolved"])
        and record["unresolved"] >= 0
        and ref_ok
        and _is_number(record["recorded_at"])
    )


def attest_record_valid(record: Any, sha: str) -> bool:
    """record が、この sha に結びついた形式どおりの attest 記録か。"""
    return _attest_ok(record) and record["patch_sha256"] == sha


def _latest_attest(attests: Iterable[Any] | None, sha: str, reviewer: str) -> Any:
    bound = [
        r
        for r in (attests or ())
        if attest_record_valid(r, sha) and r["reviewer"] == reviewer
    ]
    return max(bound, key=lambda r: r["recorded_at"]) if bound else None


def review_status(attests: Iterable[Any] | None, sha: str, reviewer: str) -> str:
    """reviewer の状態: pass / fail / unresolved / stale (別の sha だけ) / missing。"""
    records = list(attests or ())
    latest = _latest_attest(records, sha, reviewer)
    if latest is None:
        stale = any(_attest_ok(r) and r["reviewer"] == reviewer for r in records)
        return "stale" if stale else "missing"
    if latest["result"] == "fail":
        return "fail"
    return "unresolved" if latest["unresolved"] else "pass"


_REVIEW_TEXT = {
    "pass": "pass",
    "fail": "fail",
    "stale": "失効 (別のパッチに対する記録しか無い)",
    "missing": "未実施",
}
_STATUS_LETTER = {
    "added": "A",
    "deleted": "D",
    "modified": "M",
    "renamed": "R",
    "copied": "C",
}
_MAX_LISTED = 40


def _review_text(records: list[Any], sha: str, reviewer: str) -> str:
    status = review_status(records, sha, reviewer)
    if status == "unresolved":
        latest = _latest_attest(records, sha, reviewer)
        return f"未解決の指摘 {latest['unresolved']} 件"
    return _REVIEW_TEXT[status]


def _join_paths(paths: Iterable[str], limit: int = 8) -> str:
    items = list(paths)
    shown = ", ".join(display_text(p, 120) for p in items[:limit])
    return shown + (f" … 他 {len(items) - limit} 件" if len(items) > limit else "")


# 切らない (長い setup や verify の argv の末尾を黙って落とさない)。表示枠に収まるかは
# 描く側 (render_preview) が決め、収まらなければ承認させない。
_NO_LIMIT = 10**9


def _argv_text(argv: Sequence[str]) -> str:
    return display_text(shlex.join(argv), _NO_LIMIT)


def _file_lines(summary: PatchSummary) -> list[str]:
    counts = {s: 0 for s in _STATUS_LETTER}
    for pf in summary.files:
        counts[pf.status] += 1
    lines = [
        f"ファイル: {len(summary.files)} 件 (追加 {counts['added']} / 削除 "
        f"{counts['deleted']} / 変更 {counts['modified']} / リネーム "
        f"{counts['renamed']} / コピー {counts['copied']})  "
        f"+{summary.added} / -{summary.removed} 行"
    ]
    for pf in summary.files[:_MAX_LISTED]:
        name = display_text(pf.path, 120)
        if pf.status in ("renamed", "copied") and pf.old_path is not None:
            name = f"{display_text(pf.old_path, 120)} -> {name}"
        flag = " [binary]" if pf.binary else ""
        lines.append(
            f"  {_STATUS_LETTER[pf.status]} {name} (+{pf.added} -{pf.removed}){flag}"
        )
    if len(summary.files) > _MAX_LISTED:
        lines.append(f"  … 他 {len(summary.files) - _MAX_LISTED} 件")
    if summary.binary_files:
        lines.append(f"binary: {_join_paths(summary.binary_files)}")
    if summary.executable_files:
        lines.append(f"実行権限 (+x): {_join_paths(summary.executable_files)}")
    if summary.symlinks:
        targets = [
            f"{pf.path} -> {pf.symlink_target or '?'}"
            for pf in summary.files
            if pf.new_mode == "120000"
        ]
        lines.append(f"symlink: {_join_paths(targets)}")
    if summary.submodules:
        lines.append(f"submodule (非対応): {_join_paths(summary.submodules)}")
    if summary.risky:
        parts = [
            f"{RISKY_LABELS[c]} ({c}): {_join_paths(paths)}"
            for c, paths in summary.risky
        ]
        lines.append("RISKY: " + " / ".join(parts))
    else:
        lines.append("RISKY: なし")
    if summary.secrets:
        found = [f"{display_text(p, 120)}: {label}" for p, label in summary.secrets]
        lines.append("秘密スキャン (追加行): " + ", ".join(found))
    else:
        lines.append("秘密スキャン (追加行): 検出なし")
    if summary.invalid_paths:
        bad = [f"{display_text(p, 120)} ({why})" for p, why in summary.invalid_paths]
        lines.append("不正なパス (apply は拒否される): " + ", ".join(bad[:8]))
    if summary.errors:
        lines.append(f"形式エラー (apply は拒否される): {summary.errors[0]}")
    return lines


def patch_summary_text(summary: PatchSummary) -> str:
    """パッチだけの要約 (ファイル・RISKY・秘密スキャン・不正なパス)。export が表示する。"""
    lines = [f"パッチ: sha256 {summary.sha256} ({summary.size} bytes)"]
    if summary.too_large:
        lines.append(
            f"要約: パッチが {PATCH_SUMMARY_LIMIT // (1024 * 1024)} MiB を超えるため"
            "解析していない (apply は拒否される)"
        )
    else:
        lines += _file_lines(summary)
    lines.append("apply に必要なレビュー: " + ", ".join(required_reviews(summary)))
    return "\n".join(lines)


def apply_summary_text(
    summary: PatchSummary,
    meta: Any,
    verify_record: Any,
    attests: Iterable[Any] | None,
) -> str:
    """apply の確認に出す要約。パッチ由来の文字列は display_text で無害化する。"""
    records = list(attests or ())
    try:
        m: dict | None = validate_meta(meta)
    except MetaError:
        m = None
    if m is not None:
        lines = [
            f"csb apply: サンドボックス {m['id']} のパッチをホストのリポジトリへ反映する",
            f"リポジトリ: {display_text(m['repo_root'])}",
            f"base: {m['base_commit'][:12]} (tree {m['base_tree'][:12]})",
        ]
    else:
        lines = ["csb apply: meta が不正なため検証できない"]
    lines.append(f"パッチ: sha256 {summary.sha256} ({summary.size} bytes)")
    if summary.too_large:
        lines.append(
            f"要約: パッチが {PATCH_SUMMARY_LIMIT // (1024 * 1024)} MiB を超えるため"
            "解析していない (apply は拒否される)"
        )
    else:
        lines += _file_lines(summary)
    status = verify_status(verify_record, summary.sha256, m or {})
    if status == "MISSING":
        lines.append("verify: MISSING")
    else:
        lines.append(
            f"verify: {status} (exit {verify_record['exit_code']}, argv: "
            f"{_argv_text(verify_record['argv'])})"
        )
    lines.append(
        "レビュー (自己申告の記録。抜けと古いレビューの使い回しを見つけるためのもの):"
    )
    for reviewer in required_reviews(summary):
        note = " (RISKY のため必須)" if reviewer == SECURITY_REVIEWER else ""
        text = _review_text(records, summary.sha256, reviewer)
        lines.append(f"  {reviewer}{note}: {text}")
    return "\n".join(lines)


HARNESS_BLOCKER = (
    "ハーネス設定に触れるパッチは csb apply では適用しない ({count} 件: {paths})。"
    "人が自分で git apply する"
)


def harness_manual_steps(repo_root: str, patch_file: str, sha: str) -> list[str]:
    """ハーネスに触れるパッチを人が自分で適用する手順 (対象のリポジトリと patch の絶対パス)。

    先にパッチの sha を照合する: 確認の画面を見てから手で適用するまでの間に、状態
    ディレクトリのパッチを差し替えられても気付けるように。
    """
    if not _matches(_SHA256, sha):
        raise ValueError("harness_manual_steps: not a sha256")
    repo, patch = shlex.quote(repo_root), shlex.quote(patch_file)
    return [
        display_text(
            f"shasum -a 256 {patch}  # 出力が {sha} で始まることを確かめる", _NO_LIMIT
        ),
        display_text(f"git -C {repo} apply --check {patch}", _NO_LIMIT),
        display_text(f"git -C {repo} apply {patch}", _NO_LIMIT),
    ]


def apply_blockers(
    summary: PatchSummary,
    meta: Any,
    verify_record: Any,
    attests: Iterable[Any] | None,
    host_base_tree: str | None,
    *,
    host_checked: bool = True,
) -> list[str]:
    """apply を拒否する理由の一覧。空なら適用してよい (人間の確認は別に必要)。

    ハーネス設定 (RISKY の harness。_is_harness_target と同じ集合を含む) に触れる
    パッチは、レビューが揃っていても常に拒否する: エージェントの作ったパッチで
    ゲート自身を書き換えさせない (ユーザー決定)。人が自分で適用する手順は
    harness_manual_steps。host_checked=False (preflight / 確認の要約) はホストの
    base tree を見ていないので、その照合を拒否理由に数えない (未確認として別に出す)。
    """
    records = list(attests or ())
    blockers: list[str] = []
    harness = dict(summary.risky).get("harness", ())
    if harness:
        blockers.append(
            HARNESS_BLOCKER.format(count=len(harness), paths=_join_paths(harness, 3))
        )
    try:
        m: dict | None = validate_meta(meta)
    except MetaError as exc:
        blockers.append(f"meta が不正: {exc}")
        m = None
    if summary.too_large:
        blockers.append(
            f"パッチが {PATCH_SUMMARY_LIMIT // (1024 * 1024)} MiB を超えるため検査できない"
            " (分割する)"
        )
    else:
        if summary.errors:
            blockers.append(f"パッチの形式が不正: {summary.errors[0]}")
        if summary.invalid_paths:
            path, why = summary.invalid_paths[0]
            blockers.append(
                f"不正なパスが {len(summary.invalid_paths)} 件 "
                f"(例: {display_text(path, 120)}: {why})"
            )
        if summary.submodules:
            blockers.append("submodule の変更は非対応")
        if not summary.files and not summary.errors:
            blockers.append("パッチが空")
    if host_checked and m is not None and host_base_tree != m["base_tree"]:
        blockers.append(
            "ホストの base tree が作成時と一致しない (サンドボックスを作り直す)"
        )
    status = verify_status(verify_record, summary.sha256, m or {})
    if status != "PASS":
        blockers.append(f"verify が {status}")
    for reviewer in required_reviews(summary):
        if review_status(records, summary.sha256, reviewer) != "pass":
            text = _review_text(records, summary.sha256, reviewer)
            blockers.append(f"{reviewer} のレビューが {text}")
    return blockers


def net_window_open(meta: Any, now: float) -> bool:
    """meta の net_on_until が now より後か (net on の期限内か)。"""
    if not isinstance(meta, dict):
        return False
    until = meta.get("net_on_until")
    return isinstance(until, (int, float)) and _is_number(until) and until > now


# ---------------------------------------------------------------------------
# 7. create の計画
# ---------------------------------------------------------------------------
FileSource = Union[Mapping[str, bytes], Callable[[str], Optional[bytes]]]


@dataclass(frozen=True)
class SetupStep:
    """setup で実行する 1 手順。origin は出どころ (`postCreateCommand[x]` 等)。"""

    origin: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class CreatePlan:
    """create の計画。image_source は devcontainer / dockerfile / generic / explicit。"""

    repo_root: str
    image_source: str
    image_ref: str
    dockerfile: str
    build_context: str
    devcontainer: str
    setup: tuple[SetupStep, ...]
    setup_source: str
    notices: tuple[str, ...]
    unsupported: tuple[str, ...]
    base_commit: str = ""
    untracked: int = 0

    @property
    def refused(self) -> bool:
        return bool(self.unsupported)


_DC_SUBDIR = re.compile(r"\.devcontainer/[^/]+/devcontainer\.json")
# create が読む devcontainer のキー。ここに無いキーは、たとえ ${localEnv:…} を含んでいても
# 使わないので計画を変えない。
_DC_USED_KEYS = (
    "image",
    "build",
    "dockerFile",
    "context",
    "onCreateCommand",
    "updateContentCommand",
    "postCreateCommand",
)
# 実行時の権限・マウント・環境を広げうるキー。サンドボックスのプロファイルが優先する
# (使わない) ことを要約で知らせる。
_DC_IGNORED_KEYS = (
    "runArgs",
    "mounts",
    "workspaceMount",
    "privileged",
    "capAdd",
    "securityOpt",
    "containerEnv",
    "remoteEnv",
    "appPort",
    "forwardPorts",
)
_VARIABLE = re.compile(r"\$\{([^}]*)\}")
_KNOWN_VARIABLES = {
    "containerWorkspaceFolder": CONTAINER_WORKDIR,
    "containerWorkspaceFolderBasename": posixpath.basename(CONTAINER_WORKDIR),
}
_LFS_FILTER = re.compile(rb"(?<![\w-])filter=lfs(?![\w-])")
_LOCKFILE_GROUPS = (
    (
        ("pnpm-lock.yaml", ("pnpm", "install", "--frozen-lockfile")),
        ("yarn.lock", ("yarn", "install", "--immutable")),
        ("package-lock.json", ("npm", "ci")),
        ("npm-shrinkwrap.json", ("npm", "ci")),
    ),
    (
        ("uv.lock", ("uv", "sync", "--frozen")),
        ("requirements.txt", ("pip", "install", "-r", "requirements.txt")),
    ),
    (("go.mod", ("go", "mod", "download")),),
    (("Cargo.lock", ("cargo", "fetch")),),
)


def _scan_json_text(text: str, comments: bool) -> str:
    """JSON 文字列の外側だけを処理する走査。comments なら // と /* */ を、
    そうでなければ閉じ括弧の直前のカンマを取り除く。文字列の中は触らない。"""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\":
                out.append(text[i + 1 : i + 2])
                i += 2
                continue
            in_string = ch != '"'
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif comments and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif comments and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated comment")
            out.append(" ")
            i = end + 2
            continue
        elif not comments and ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if text[j : j + 1] not in ("}", "]"):
                out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _parse_jsonc(data: bytes) -> Any:
    """devcontainer.json (JSONC: コメントと末尾カンマを許す JSON) を読む。"""
    if len(data) > DEVCONTAINER_MAX_BYTES:
        raise ValueError("too large")
    text = _scan_json_text(data.decode("utf-8"), comments=True)
    return json.loads(_scan_json_text(text, comments=False))


def _substitute(value: str) -> str | None:
    """devcontainer の変数を置換する。対応外 (localEnv / localWorkspaceFolder 等) は None。"""
    unknown = False

    def repl(match: re.Match[str]) -> str:
        nonlocal unknown
        name = match.group(1)
        if name in _KNOWN_VARIABLES:
            return _KNOWN_VARIABLES[name]
        unknown = True
        return ""

    out = _VARIABLE.sub(repl, value)
    return None if unknown or "${" in out else out


def _repo_relative(base_dir: str, value: Any) -> str | None:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return None
    if _CONTROL.search(value) or "\\" in value or "${" in value:
        return None
    joined = posixpath.normpath(posixpath.join(base_dir, value))
    if joined == ".." or joined.startswith("../"):
        return None
    if joined != "." and _path_problem(joined):
        return None
    return joined


def _command_steps(key: str, value: Any, notices: list[str]) -> list[SetupStep] | None:
    """onCreateCommand 等を手順にする。対応外の変数を含むなら None。"""

    def one(origin: str, v: Any) -> list[SetupStep] | None:
        if isinstance(v, str) and v.strip():
            text = _substitute(v)
            if text is None:
                return None
            return [SetupStep(origin, ("/bin/sh", "-c", text))]
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            argv = [_substitute(x) for x in v]
            if any(a is None for a in argv):
                return None
            return [SetupStep(origin, tuple(a for a in argv if a is not None))]
        notices.append(f"{display_text(origin, 80)} の形式が不正なため無視する")
        return []

    items = (
        [(f"{key}[{name}]", v) for name, v in value.items()]
        if isinstance(value, dict)
        else [(key, value)]
    )
    out: list[SetupStep] = []
    for origin, v in items:
        steps = one(origin, v)
        if steps is None:
            return None
        out += steps
    return out


def _find_devcontainer(
    read: Callable[[str], Optional[bytes]],
    listing: list[str] | None,
    notices: list[str],
) -> tuple[str, bytes] | None:
    for candidate in (".devcontainer/devcontainer.json", ".devcontainer.json"):
        data = read(candidate)
        if data is not None:
            return candidate, data
    subdirs = sorted(p for p in (listing or ()) if _DC_SUBDIR.fullmatch(p))
    if len(subdirs) > 1:
        notices.append(
            f"devcontainer 定義が複数 ({_join_paths(subdirs, 4)}) あるため汎用イメージを使う"
        )
        return None
    if subdirs:
        data = read(subdirs[0])
        if data is not None:
            return subdirs[0], data
    return None


def _resolve_devcontainer(
    read: Callable[[str], Optional[bytes]],
    listing: list[str] | None,
    notices: list[str],
) -> tuple[str, str, str, str, str, tuple[SetupStep, ...]] | None:
    """devcontainer から (source, ref, dockerfile, context, 定義のパス, setup) を決める。

    使えない定義 (compose / features / 読めない / リポジトリの外を指す / 対応外の変数) は
    None を返し、理由を notices に足す。呼び出し側は汎用イメージに切り替える。Dockerfile が
    build context の外にある定義は、ここでは返し、create_plan が拒否する (汎用には
    切り替えない)。
    """
    found = _find_devcontainer(read, listing, notices)
    if found is None:
        return None
    path, data = found
    fallback = "ため汎用イメージを使う"
    try:
        cfg = _parse_jsonc(data)
    except (ValueError, RecursionError):
        notices.append(f"{path} を JSONC として読めない" + fallback)
        return None
    if not isinstance(cfg, dict):
        notices.append(f"{path} が JSON オブジェクトではない" + fallback)
        return None
    if "dockerComposeFile" in cfg:
        notices.append("compose を使う devcontainer は非対応の" + fallback)
        return None
    if cfg.get("features"):
        notices.append("features を使う devcontainer は非対応の" + fallback)
        return None
    if "initializeCommand" in cfg:
        notices.append("initializeCommand はホストで実行される定義のため実行しない")
    for key in ("postStartCommand", "postAttachCommand"):
        if key in cfg:
            notices.append(
                f"{key} は実行しない (setup に使うのは on/update/postCreate)"
            )
    used = {k: cfg[k] for k in _DC_USED_KEYS if k in cfg}
    if "${localEnv:" in json.dumps(used):
        notices.append("${localEnv:…} は展開しない" + fallback)
        return None
    ignored = [k for k in _DC_IGNORED_KEYS if k in cfg]
    if ignored:
        notices.append(
            f"devcontainer の {', '.join(ignored)} は使わない"
            " (サンドボックスのプロファイルが優先)"
        )
    base_dir = posixpath.dirname(path)
    dockerfile = context = ""
    if "build" in cfg or "dockerFile" in cfg:
        build = cfg.get("build")
        if build is None:
            build = {
                "dockerfile": cfg["dockerFile"],
                "context": cfg.get("context", "."),
            }
        if not isinstance(build, dict):
            notices.append("build の形式が不正な" + fallback)
            return None
        resolved_df = _repo_relative(base_dir, build.get("dockerfile"))
        resolved_ctx = _repo_relative(base_dir, build.get("context", "."))
        if resolved_df is None or resolved_ctx is None:
            notices.append(
                "Dockerfile か build context がリポジトリの外を指す" + fallback
            )
            return None
        if read(resolved_df) is None:
            notices.append(f"{display_text(resolved_df, 120)} が無い" + fallback)
            return None
        if "args" in build:
            notices.append("build.args は渡さない")
        source, ref = "dockerfile", ""
        dockerfile, context = resolved_df, resolved_ctx
    else:
        image = cfg.get("image")
        text = _substitute(image) if isinstance(image, str) else None
        if text is None or not is_image_ref(text):
            notices.append("image も build も使えない" + fallback)
            return None
        source, ref = "devcontainer", text
    steps: list[SetupStep] = []
    for key in ("onCreateCommand", "updateContentCommand", "postCreateCommand"):
        if key in cfg:
            parsed = _command_steps(key, cfg[key], notices)
            if parsed is None:
                notices.append(f"{key} が対応外の変数を含む" + fallback)
                return None
            steps += parsed
    return source, ref, dockerfile, context, path, tuple(steps)


def _lockfile_steps(
    read: Callable[[str], Optional[bytes]], notices: list[str]
) -> tuple[SetupStep, ...]:
    steps = []
    for group in _LOCKFILE_GROUPS:
        present = [(name, argv) for name, argv in group if read(name) is not None]
        if not present:
            continue
        name, argv = present[0]
        steps.append(SetupStep(f"lockfile:{name}", argv))
        others = [n for n, a in present[1:] if a != argv]
        if others:
            notices.append(
                f"lockfile が複数あるため {name} を使う (無視: {', '.join(others)})"
            )
    return tuple(steps)


def _unsupported_markers(
    read: Callable[[str], Optional[bytes]], listing: list[str] | None
) -> tuple[str, ...]:
    out = []
    if read(".gitmodules") is not None:
        out.append("submodule (.gitmodules) は非対応")
    attrs = [".gitattributes"] + sorted(
        p for p in (listing or ()) if p.endswith("/.gitattributes")
    )
    for path in attrs:
        data = read(path)
        if data is not None and _LFS_FILTER.search(data):
            out.append(f"Git LFS ({display_text(path, 120)}) は非対応")
            break
    return tuple(out)


def dockerfile_inside_context(dockerfile: str, context: str) -> bool:
    """リポジトリ相対の Dockerfile が build context の中にあるか (CLI と同じ判定)。

    どちらも正規化してから比べる (`a/../../x` のような値を中と読まない)。
    """
    dockerfile = posixpath.normpath(dockerfile)
    context = posixpath.normpath(context)
    rel = dockerfile if context == "." else posixpath.relpath(dockerfile, context)
    return not (rel == ".." or rel.startswith("../"))


def create_plan(
    repo_root: str,
    files: FileSource,
    *,
    image: str = "auto",
    setup: str | None = None,
    paths: Iterable[str] | None = None,
    base_commit: str = "",
    untracked: int = 0,
) -> CreatePlan:
    """create の計画を立てる (initializeCommand は決して使わない)。

    files はリポジトリ相対パス → 中身の Mapping か、パスを受けて中身 (無ければ None) を
    返す関数。関数のときは `.devcontainer/*/devcontainer.json` と入れ子の
    `.gitattributes` を探すために paths (追跡ファイルの一覧) を渡す。
    """
    if image not in ("auto", "generic") and not is_image_ref(image):
        raise ValueError(f"invalid image: {image!r}")
    if setup is not None and (not isinstance(setup, str) or not setup.strip()):
        raise ValueError("empty --setup")
    read: Callable[[str], Optional[bytes]]
    if isinstance(files, Mapping):
        read = files.get
        listing: list[str] | None = list(paths) if paths is not None else list(files)
    else:
        read = files
        listing = list(paths) if paths is not None else None
    notices: list[str] = []
    unsupported = _unsupported_markers(read, listing)
    source, ref, dockerfile, context, dc_path = "generic", GENERIC_IMAGE_REF, "", "", ""
    dc_steps: tuple[SetupStep, ...] = ()
    if image == "auto":
        resolved = _resolve_devcontainer(read, listing, notices)
        if resolved is not None:
            source, ref, dockerfile, context, dc_path, dc_steps = resolved
        if source == "dockerfile" and not dockerfile_inside_context(
            dockerfile, context
        ):
            # CLI もビルドの前に同じ理由で拒否する (claude_sandbox._resolve_image)。
            # 汎用イメージには切り替えない: 定義の作者が選んだイメージと違うものになる。
            unsupported += (
                f"Dockerfile ({display_text(dockerfile, 120)}) が build context"
                f" ({display_text(context, 120)}) の外にある (非対応)",
            )
    elif image != "generic":
        source, ref = "explicit", image
    steps: tuple[SetupStep, ...]
    if setup is not None:
        steps = (SetupStep("--setup", ("/bin/sh", "-c", setup)),)
        setup_source = "explicit"
    elif dc_steps:
        steps, setup_source = dc_steps, "devcontainer"
    else:
        steps = _lockfile_steps(read, notices)
        setup_source = "lockfile" if steps else "none"
    return CreatePlan(
        repo_root=repo_root,
        image_source=source,
        image_ref=ref,
        dockerfile=dockerfile,
        build_context=context,
        devcontainer=dc_path,
        setup=steps,
        setup_source=setup_source,
        notices=tuple(notices),
        unsupported=unsupported,
        base_commit=base_commit,
        untracked=untracked,
    )


def create_summary_text(plan: CreatePlan) -> str:
    """create の確認に出す要約。リポジトリ由来の文字列は display_text で無害化する。"""
    lines = [
        "csb create: サンドボックスを作成する (setup の間だけネットワークに接続する)",
        f"リポジトリ: {display_text(plan.repo_root)}",
    ]
    base = display_text(plan.base_commit[:12]) if plan.base_commit else "(不明)"
    lines.append(
        f"base: {base} (untracked {plan.untracked} 件はスナップショットに含めない)"
    )
    if plan.image_source == "devcontainer":
        image = (
            f"devcontainer の image {display_text(plan.image_ref)} "
            f"({display_text(plan.devcontainer)})"
        )
    elif plan.image_source == "dockerfile":
        image = (
            f"Dockerfile {display_text(plan.dockerfile)} を build "
            f"(context {display_text(plan.build_context)}, "
            f"定義 {display_text(plan.devcontainer)})"
        )
    elif plan.image_source == "explicit":
        image = f"指定イメージ {display_text(plan.image_ref)}"
    else:
        image = f"汎用イメージ {display_text(plan.image_ref)}"
    lines.append(f"イメージ: {image}")
    lines.append("setup (ネットワーク接続中にコンテナ内で実行する):")
    if plan.setup:
        for step in plan.setup:
            origin = display_text(step.origin, 80)
            lines.append(f"  - {origin}: {_argv_text(step.argv)}")
    else:
        lines.append("  (なし)")
    if plan.notices:
        lines.append("注意:")
        lines += [f"  - {display_text(n, 300)}" for n in plan.notices]
    if plan.unsupported:
        lines.append("非対応の要素があるため作成を拒否する:")
        lines += [f"  - {u}" for u in plan.unsupported]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. bash-review の確認に出す要約 (子プロセスが data を作り、フックが描く)
# ---------------------------------------------------------------------------
# 子プロセス (claude_sandbox.preview_main) は構造化した data を返し、親 (フック) が
# render_preview で固定の見出しと項目ごとの表示枠に描く。判定に効く項目 (実体・拒否理由・
# RISKY の全分類と件数・秘密スキャン・verify・attest・setup の全文と出所) は先頭に置いて
# 省略しない。それが表示枠に収まらなければ、この画面では承認できない (deny)。
# 省略してよいのはファイル一覧だけで、切ったら印を付ける (印は切った後に足すので切れない)。
PREVIEW_SCHEMA = 1
# 判定に効く部分の目安。確認ダイアログが reason をどこまで出すかは公式に書かれていない。
PREVIEW_DECISION_CHARS = 1500
PREVIEW_DECISION_LINES = 25
PREVIEW_TOTAL_CHARS = 4000
_PATH_SHOWN = 160
_OTHER_RISKY_SHOWN = 3
_SECRETS_SHOWN = 5

# create の要約に必ず付ける 1 行 (判定の枠の外)。承認の後に CLI が計画を立て直すので、
# ここで見せた計画と実際に走る計画は結び付いていない (結び付ける仕組みは無い)。
CREATE_ADVISORY = (
    "この要約は参考表示です。承認の後に CLI が計画を立て直すため、"
    "表示と実行の一致は保証されません"
)
PREVIEW_IDENTITY_LINE = (
    "csb の実体: 確認済み (固定のラッパーが、フックと同じ claude_sandbox.py を起動する)"
)
APPLY_UNCHECKED = (
    "ホストの作業ツリーが作成時の base tree と同じか (違えば拒否する)",
    "適用先の filter 属性 (あれば拒否する)",
)
APPLY_OVERFLOW = (
    "csb apply の判定に要る項目 (拒否理由・RISKY・秘密スキャン・verify・attest) が"
    "確認の画面に収まらないため、この画面では承認できない。csb preflight で全文を"
    "確かめ、パッチを分けてから実行し直す"
)
# image build の要約に必ず付ける 1 行 (判定の枠の外)。承認した要約と、ビルドの時に
# CLI が読み直す Dockerfile は結び付けていない (CREATE_ADVISORY と同じ)。
IMAGE_BUILD_ADVISORY = (
    "この要約は参考表示です。承認の後に CLI が Dockerfile を読み直してビルドするため、"
    "表示と実行の一致は保証されません"
)
IMAGE_BUILD_BLOCKED = (
    "csb image build の Dockerfile などに、要約に出せない (または確かめられない) 要素が"
    "あるため、この画面では承認できない"
)
IMAGE_BUILD_OVERFLOW = (
    "csb image build の判定に要る項目 (Dockerfile・ベースイメージ・docker) が確認の"
    "画面に収まらないため、この画面では承認できない。containers/sandbox/Dockerfile の"
    "FROM を減らしてから実行し直す"
)
CREATE_OVERFLOW = (
    "csb create の setup などが確認の画面に収まらないため、この画面では承認できない。"
    "短い --setup を指定するか、devcontainer の postCreateCommand などを短くしてから"
    "実行し直す"
)
_HEAD_TEXT = {
    "same": "作成時と同じ",
    "moved": "作成時から動いた",
    "unknown": "確かめられない",
}


def apply_preview_data(
    summary: PatchSummary,
    meta: Any,
    verify_record: Any,
    attests: Iterable[Any] | None,
    *,
    notes: Sequence[str],
    head: str,
    patch_file: str,
    extra_blockers: Sequence[str] = (),
) -> dict:
    """apply の確認の data (JSON にできる値だけ)。ホストの base tree は見ない。"""
    records = list(attests or ())
    try:
        m: dict | None = validate_meta(meta)
    except MetaError:
        m = None
    risky = dict(summary.risky)
    status = verify_status(verify_record, summary.sha256, m or {})
    verify: dict = {"status": status, "exit_code": None, "argv": None}
    if status != "MISSING":
        verify["exit_code"] = verify_record["exit_code"]
        verify["argv"] = list(verify_record["argv"])
    return {
        "schema": PREVIEW_SCHEMA,
        "kind": "apply",
        "sandbox": m["id"] if m else None,
        "repo_root": m["repo_root"] if m else None,
        "base_commit": m["base_commit"] if m else None,
        "head": head,
        "patch": {"sha256": summary.sha256, "size": summary.size, "file": patch_file},
        "too_large": summary.too_large,
        "blockers": apply_blockers(
            summary, meta, verify_record, records, None, host_checked=False
        )
        + list(extra_blockers),
        "notes": list(notes),
        "unchecked": list(APPLY_UNCHECKED),
        "risky": [[c, list(risky.get(c, ()))] for c in RISKY_LABELS],
        "secrets": {
            "scanned": summary.secrets_scanned,
            "found": [[path, label] for path, label in summary.secrets],
        },
        "verify": verify,
        "reviews": [
            [r, _review_text(records, summary.sha256, r), r == SECURITY_REVIEWER]
            for r in required_reviews(summary)
        ],
        "files": {
            "count": len(summary.files),
            "added": summary.added,
            "removed": summary.removed,
            "list": [
                [
                    _STATUS_LETTER[pf.status],
                    pf.path,
                    pf.old_path if pf.status in ("renamed", "copied") else None,
                    pf.added,
                    pf.removed,
                    pf.binary,
                ]
                for pf in summary.files
            ],
        },
        "flags": {
            "binary": list(summary.binary_files),
            "executable": list(summary.executable_files),
            "symlinks": [
                [pf.path, pf.symlink_target or "?"]
                for pf in summary.files
                if pf.new_mode == "120000"
            ],
            "submodules": list(summary.submodules),
        },
        "manual_steps": (
            harness_manual_steps(m["repo_root"], patch_file, summary.sha256)
            if m is not None and risky.get("harness")
            else []
        ),
    }


def create_preview_data(
    plan: CreatePlan,
    *,
    head: str,
    blockers: Sequence[str] = (),
    docker: Mapping[str, str] | None = None,
) -> dict:
    """create の確認の data。dirty / 未追跡 / 秘密スキャンは作成時に検査する (ここでは見ない)。

    docker は固定する docker の realpath・所有者・署名の確認 ({"path", "owner",
    "signature"})。固定できなければ None (理由は blockers に入る)。
    """
    return {
        "schema": PREVIEW_SCHEMA,
        "kind": "create",
        "repo_root": plan.repo_root,
        "head": head,
        "docker": None if docker is None else dict(docker),
        "image": {
            "source": plan.image_source,
            "ref": plan.image_ref,
            "dockerfile": plan.dockerfile,
            "context": plan.build_context,
            "devcontainer": plan.devcontainer,
        },
        "setup": {
            "source": plan.setup_source,
            "steps": [[step.origin, list(step.argv)] for step in plan.setup],
        },
        "notices": list(plan.notices),
        "blockers": list(plan.unsupported) + list(blockers),
    }


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("preview data: expected a string")
    return value


def _int(value: Any) -> int:
    if not _is_int(value):
        raise ValueError("preview data: expected an integer")
    return int(value)


def _strs(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("preview data: expected a list")
    return [_str(v) for v in value]


def _shown(value: Any, limit: int = 300) -> str:
    return display_text(_str(value), limit)


def _blocker_lines(blockers: list[str], tool: str) -> list[str]:
    if not blockers:
        return [f"拒否理由: なし (未確認の項目は {tool} の時に検査する)"]
    return [f"拒否理由 ({len(blockers)} 件。csb {tool} は拒否する):"] + [
        f"  - {_shown(b)}" for b in blockers
    ]


def _render_apply(d: dict) -> tuple[list[str], list[str], list[str], str]:
    patch = d["patch"]
    sha = _str(patch["sha256"])
    if not _SHA256.fullmatch(sha):
        raise ValueError("preview data: bad sha")
    sid = d["sandbox"]
    repo, base = d["repo_root"], d["base_commit"]
    files = d["files"]
    lines = [
        f"リポジトリ: {_shown(repo) if repo is not None else '(meta が不正)'} / base "
        f"{_shown(base)[:12] if base is not None else '?'} / HEAD: "
        f"{_HEAD_TEXT[_str(d['head'])]}",
        f"パッチ: sha256 {sha} ({_int(patch['size'])} bytes, {_int(files['count'])} "
        f"ファイル +{_int(files['added'])} / -{_int(files['removed'])} 行)",
    ]
    lines += _blocker_lines(_strs(d["blockers"]), "apply")
    if d["too_large"] is True:
        lines += [
            f"RISKY: 未解析 (パッチが {PATCH_SUMMARY_LIMIT // (1024 * 1024)} MiB を超える)",
            "秘密スキャン (追加行): 未解析",
        ]
    else:
        risky = [(_str(c), _strs(paths)) for c, paths in d["risky"]]
        if [c for c, _ in risky] != list(RISKY_LABELS):
            raise ValueError("preview data: risky categories")
        lines.append(
            "RISKY: " + " / ".join(f"{RISKY_LABELS[c]} {len(p)}" for c, p in risky)
        )
        for c, paths in risky:
            if c == "harness" and paths:
                lines.append(
                    f"  {RISKY_LABELS[c]} (全 {len(paths)} 件): "
                    + ", ".join(_shown(p, _PATH_SHOWN) for p in paths)
                )
            elif paths:
                more = len(paths) - _OTHER_RISKY_SHOWN
                lines.append(
                    f"  {RISKY_LABELS[c]}: "
                    + ", ".join(
                        _shown(p, _PATH_SHOWN) for p in paths[:_OTHER_RISKY_SHOWN]
                    )
                    + (f" 他 {more} 件 (ファイル欄)" if more > 0 else "")
                )
        sec = d["secrets"]
        found = [(_str(p), _str(label)) for p, label in sec["found"]]
        if sec["scanned"] is not True:
            lines.append("秘密スキャン (追加行): 未解析")
        elif found:
            more = len(found) - _SECRETS_SHOWN
            lines.append(
                f"秘密スキャン (追加行): {len(found)} 件: "
                + ", ".join(
                    f"{_shown(p, 120)} ({_shown(label, 60)})"
                    for p, label in found[:_SECRETS_SHOWN]
                )
                + (f" 他 {more} 件" if more > 0 else "")
            )
        else:
            lines.append("秘密スキャン (追加行): 検出なし")
    verify = d["verify"]
    status = _str(verify["status"])
    if status == "MISSING":
        lines.append("verify: MISSING (検証を実行していない)")
    else:
        argv = _strs(verify["argv"])
        lines.append(
            f"verify: {_shown(status, 20)} (exit {_int(verify['exit_code'])}, argv: "
            f"{_argv_text(argv)}) — この argv が exit 0 だっただけで、検証の中身は保証しない"
        )
    reviews = [
        f"{_shown(r, 64)}{' (RISKY のため必須)' if risky_only is True else ''}"
        f"={_shown(text, 80)}"
        for r, text, risky_only in d["reviews"]
    ]
    lines.append("attest (自己申告の記録): " + " / ".join(reviews))
    flags = d["flags"]
    counts = [
        ("実行権限 (+x)", len(_strs(flags["executable"]))),
        ("symlink", len(flags["symlinks"])),
        ("binary", len(_strs(flags["binary"]))),
        ("submodule", len(_strs(flags["submodules"]))),
    ]
    if any(n for _, n in counts):
        lines.append("ほか: " + " / ".join(f"{label} {n} 件" for label, n in counts))
    lines.append("未確認 (apply の時に検査する): " + " / ".join(_strs(d["unchecked"])))
    lines += [f"注意: {_shown(n)}" for n in _strs(d["notes"])]
    # 人が自分で実行する手順は判定の項目ではないので、判定の枠の外に置いて切らない。
    steps = _strs(d["manual_steps"])
    howto = []
    if steps:
        howto.append(
            "人が自分で対象のリポジトリへ適用する手順 (エージェントには実行させない):"
        )
        howto += [f"  {_shown(step, _NO_LIMIT)}" for step in steps]
    rest = []
    if files["list"]:
        rest.append("ファイル:")
    for letter, path, old, plus, minus, binary in files["list"]:
        name = _shown(path, _PATH_SHOWN)
        if old is not None:
            name = f"{_shown(old, _PATH_SHOWN)} -> {name}"
        flag = " [binary]" if binary is True else ""
        rest.append(
            f"  {_shown(letter, 1)} {name} (+{_int(plus)} -{_int(minus)}){flag}"
        )
    for label, items in (
        ("実行権限 (+x)", _strs(flags["executable"])),
        ("binary", _strs(flags["binary"])),
        ("submodule", _strs(flags["submodules"])),
    ):
        rest += [f"{label}: {_shown(item, _PATH_SHOWN)}" for item in items]
    rest += [
        f"symlink: {_shown(path, _PATH_SHOWN)} -> {_shown(target, _PATH_SHOWN)}"
        for path, target in flags["symlinks"]
    ]
    shown_sid = _str(sid) if sid is not None else "ID"
    mark = (
        f"… ファイル欄の残り {{count}} 行を省いた。全文は csb preflight "
        f"{display_text(shown_sid, 20)} --patch {sha} で見る"
    )
    return lines, howto, rest, mark


def _image_text(image: dict) -> list[str]:
    source = _str(image["source"])
    ref = _shown(image["ref"])
    if source == "generic":
        return [
            f"イメージ: 汎用イメージ {ref} (作成時にローカルのイメージ ID で固定する)"
        ]
    if source == "devcontainer":
        return [
            f"イメージ: devcontainer の image {ref} ({_shown(image['devcontainer'])})。"
            "作成時に pull して ID を固定する"
        ]
    if source == "explicit":
        return [f"イメージ: 指定イメージ {ref}。作成時に pull して ID を固定する"]
    if source == "dockerfile":
        return [
            f"イメージ: Dockerfile {_shown(image['dockerfile'])} を作成時に build する"
            f" (context {_shown(image['context'])}, 定義 {_shown(image['devcontainer'])})。"
            "build の RUN はネットワークに接続する"
        ]
    raise ValueError("preview data: image source")


def _docker_text(docker: Any) -> str:
    """固定する docker の行。判定に効くので切らない (長すぎれば枠を超えて承認できない)。"""
    if docker is None:
        return "docker: 固定できない (作成しない。理由は下の非対応の要素)"
    if not isinstance(docker, dict):
        raise ValueError("preview data: docker")
    path, owner = _str(docker["path"]), _str(docker["owner"])
    signature = _str(docker["signature"])
    return (
        f"docker: {display_text(path, PREVIEW_DECISION_CHARS)}"
        f" (所有者: {display_text(owner, 64)}、署名: {display_text(signature, 80)})"
    )


def _render_create(d: dict) -> tuple[list[str], list[str], list[str], str]:
    head = _str(d["head"])
    lines = [
        f"リポジトリ: {_shown(d['repo_root'])} / "
        + (f"HEAD {display_text(head[:12])}" if head else "HEAD (コミット無し)"),
        "スナップショット: 追跡済みのファイルを作業ツリーの中身で写す"
        " (未コミットの変更を含む。未追跡と ignore は含めない)",
        "未確認 (作成時に検査し、秘密・LFS・submodule があれば中止する):"
        " 未コミットの変更・未追跡の件数・秘密スキャン",
    ]
    lines += _image_text(d["image"])
    lines.append(_docker_text(d["docker"]))
    setup = d["setup"]
    steps = [(_str(origin), _strs(argv)) for origin, argv in setup["steps"]]
    lines.append(
        f"setup ({len(steps)} 件, 出所: {_shown(setup['source'], 20)}。"
        "ネットワークに接続した setup コンテナで実行する):"
    )
    if steps:
        lines += [
            f"  {i}. {display_text(origin, 80)}: {_argv_text(argv)}"
            for i, (origin, argv) in enumerate(steps, 1)
        ]
    else:
        lines.append("  (なし)")
    lines += [
        "ネットワーク: setup の間だけ接続する (作業用コンテナは切断して起動する)",
        "ホストのマウント: なし (スナップショットは git bundle で写す)",
    ]
    lines += _blocker_lines(_strs(d["blockers"]), "create")
    lines += [f"注意: {_shown(n)}" for n in _strs(d["notices"])]
    return lines, [], [], ""


_FROM_LINE = re.compile(r"(?i)^\s*FROM\s+(.*)$")
_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")


def dockerfile_bases(text: str) -> list[tuple[str, bool]]:
    """Dockerfile が取ってくるイメージ ((参照, digest で固定しているか)) を書いた順に。

    先頭の `# syntax=` の frontend (BuildKit が取ってくる。字下げしたものと `//syntax=`
    も読む: _leading_directives) と、各 FROM の参照。前の
    stage の名前を指す FROM (`FROM build`) は数えない。`--platform=…` は読み飛ばす。
    ARG での差し込み (`FROM $BASE`) は書いたままの字面を出す。
    """
    out: list[tuple[str, bool]] = []
    stages: set[str] = set()
    for _kind, name, value in _leading_directives(text):
        if name == "syntax" and value:
            ref = value.split()[0]
            out.append((ref, bool(_PINNED.search(ref))))
            break
    for line in _physical_lines(text):
        match = _FROM_LINE.match(line)
        if not match:
            continue
        words = [w for w in match.group(1).split() if not w.startswith("--")]
        if not words:
            continue
        ref = words[0]
        if len(words) >= 3 and words[1].lower() == "as":
            stage = words[2].lower()
        else:
            stage = ""
        if ref.lower() not in stages:
            out.append((ref, bool(_PINNED.search(ref))))
        if stage:
            stages.add(stage)
    return out


# ビルドで中身が実行される (または中身を決める) 命令。FROM はベースイメージとして別に出す。
_EXECUTED_INSTRUCTIONS = frozenset(
    {
        "RUN",
        "COPY",
        "ADD",
        "ENTRYPOINT",
        "CMD",
        "USER",
        "ARG",
        "ENV",
        "SHELL",
        "ONBUILD",
        "HEALTHCHECK",
    }
)
# 先頭の parser directive (`# name=value`。空白は許される)。最初の、directive でない
# 行 (コメント・空行・命令) の後は、Docker と同じく directive として読まない。
_DOCKER_DIRECTIVE = re.compile(r"^#\s*([A-Za-z]+)\s*=\s*(.*?)\s*$")
# BuildKit の frontend の検出は `//` の directive も読む (推測)。要約は読んだうえで拒否する。
_DOCKER_SLASH_DIRECTIVE = re.compile(r"^//\s*([A-Za-z]+)\s*=\s*(.*?)\s*$")
DOCKERFILE_INDENTED_DIRECTIVE_BLOCKER = (
    "字下げした parser directive (`# name=…`) は要約に出せないため非対応"
)
DOCKERFILE_SLASH_DIRECTIVE_BLOCKER = (
    "`//` の parser directive (`//name=…`) は要約に出せないため非対応"
)
DOCKERFILE_JSON_BLOCKER = "JSON の Dockerfile は要約に出せないため非対応"
DOCKERFILE_DUPLICATE_DIRECTIVE_BLOCKER = "同じ名前の parser directive が 2 つ以上ある (どちらが効くか要約と BuildKit で食い違いうる)"
# directive に見えるコメントの行 (字下げがあっても、`\s` は Unicode の空白も含む)。
# _character_blockers は、この行の ASCII 以外の空白を許さない。
_DOCKER_DIRECTIVE_LIKE = re.compile(r"^[ \t]*#\s*[A-Za-z]+\s*=")
# heredoc は安全側に倒す。BuildKit の読み方 (語の分け方・`$X` の展開) を写す
# のではなく、RUN / COPY / ADD (ONBUILD の中のものも) に `<<<` の一部でない `<<` が
# 1 つでもあれば blocker にする。空白も展開も含まない `$((…))` の中のシフトだけは除く。継続行は、表示用に
# 空白 1 つでつないだ形と、BuildKit と同じく区切りを足さずにつないだ形の両方で見る
# (`RUN sh <<\` と次の行の `EOF` が `<<EOF` になる)。
_DOCKER_BARE_HEREDOC = re.compile(r"(?<!<)<<(?!<)")
_DOCKER_HEREDOC_INSTRUCTIONS = frozenset({"RUN", "COPY", "ADD"})
_DOCKER_CONTINUATION = re.compile(r"\\[ \t]*$")
_DOCKER_FIRST_WORD = re.compile(r"[ \t]*(\S+)[ \t]*(.*)", re.S)
_BOM = "\ufeff"
DOCKERFILE_CONTROL_BLOCKER = "制御文字 ({}) は要約に出せないため非対応"
DOCKERFILE_SPACE_BLOCKER = "ASCII 以外の空白 ({}) は要約に出せないため非対応"
DOCKERFILE_BOM_BLOCKER = "2 文字目以降の BOM (U+FEFF) は要約に出せないため非対応"
DOCKERFILE_ESCAPE_BLOCKER = "`# escape=` の指定は要約に出せないため非対応"
DOCKERFILE_HEREDOC_BLOCKER = "heredoc (`<<EOF`) の本文は要約に出せないため非対応"


def _physical_lines(text: str) -> list[str]:
    """行 (BuildKit と同じく `\\n` だけで分け、行末の `\\r` を 1 つ外す)。"""
    return [
        line[:-1] if line.endswith("\r") else line
        for line in _without_bom(text).split("\n")
    ]


def _dockerfile_logical(text: str) -> list[tuple[str, str]]:
    """命令ごとに (表示用につないだ行, 区切りを足さずにつないだ行)。

    継続は行末の `\\` (後ろに空白とタブだけ) で、BuildKit と同じ判定。表示用は継続の
    区切りを空白 1 つにし、行の中の空白はそのまま残す。継続の途中のコメント行と空行は
    Docker と同じく読み飛ばす。継続の文字はバックスラッシュだけを解釈する (ほかの
    `# escape=` は dockerfile_blockers が拒否にする)。
    """
    out: list[tuple[str, str]] = []
    shown: list[str] = []
    raw: list[str] = []
    for line in _physical_lines(text):
        stripped = line.strip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        cont = _DOCKER_CONTINUATION.search(line)
        if cont is not None:
            shown.append(line[: cont.start()].strip(" \t"))
            raw.append(line[: cont.start()])
            continue
        shown.append(stripped)
        raw.append(line)
        out.append((" ".join(p for p in shown if p), "".join(raw).strip(" \t")))
        shown, raw = [], []
    if shown:
        out.append((" ".join(p for p in shown if p), "".join(raw).strip(" \t")))
    return out


def _dockerfile_lines(text: str) -> list[str]:
    """命令の行 (表示用につないだもの)。"""
    return [shown for shown, _ in _dockerfile_logical(text)]


def _without_bom(text: str) -> str:
    """先頭の BOM (U+FEFF) を外す (BuildKit も 1 行目の BOM を外してから読む)。"""
    return text[1:] if text.startswith(_BOM) else text


# 除いてよい `$(( … ))`: 中に空白 (エスケープしたものも)・`$`・`{ }`・引用符・`\\`・
# 括弧・バッククォートを含まないものだけ。そうした区間は `$` から始まる 1 つの語の中に
# あり、BuildKit の語の判定 `^\d*<<` には当たらない (`$((1<<2))`・`$((x<<y))`)。
# `$(( 1 <<true ))` や `$((${X:- }<<true))` は、BuildKit の lexer が `$((` をただの
# 文字として読むので `<<true` が語になり heredoc になる (推測)。除かずに blocker にする。
_DOCKER_ARITHMETIC = re.compile(r"\$\(\([^\s$\{\}'\"\\`()]*\)\)")


def _leading_directives(text: str) -> list[tuple[str, str, str]]:
    """先頭の parser directive の行 ((種類, 名前 (小文字), 値))。種類は "#" (字下げ
    なし)・"#indented" (字下げあり)・"//"。

    BuildKit は行の字下げを外してから directive を探す (推測) ので、`line.lstrip(" \\t")`
    を読む。最初の命令の行で止める。安全側に倒し、空行とただのコメントの行 (`#!…`
    など) では止めずに読み進める (後ろの `# syntax=` もベースイメージの欄に出す)。
    """
    out: list[tuple[str, str, str]] = []
    for line in _physical_lines(text):
        stripped = line.lstrip(" \t")
        hashed = _DOCKER_DIRECTIVE.match(stripped)
        if hashed is not None:
            kind = "#" if stripped == line else "#indented"
            out.append((kind, hashed.group(1).lower(), hashed.group(2)))
            continue
        slashed = _DOCKER_SLASH_DIRECTIVE.match(stripped)
        if slashed is not None:
            out.append(("//", slashed.group(1).lower(), slashed.group(2)))
            continue
        if not stripped or stripped.startswith(("#", "//")):
            continue
        break
    return out


def _looks_like_json(text: str) -> bool:
    """JSON として読める (または BOM と空白の後の先頭が `{`) か。BuildKit の frontend の
    検出は JSON の `{"syntax": …}` も読む (推測)。

    BuildKit は 1 行目の `#!` の行 (shebang) を外してから読む (推測) ので、先頭の BOM の
    後の `#!` の行も外してから判定する。
    """
    body = _without_bom(text)
    if body.startswith("#!"):
        body = body.partition("\n")[2]
    body = body.lstrip()
    if body.startswith("{"):
        return True
    try:
        json.loads(body)
    except ValueError:
        return False
    return True


def _without_arithmetic(text: str) -> str:
    """空白も展開も含まない `$((…))` を除いた文字列 (中のシフトを heredoc と数えない)。"""
    return _DOCKER_ARITHMETIC.sub("", text)


def _has_heredoc(instruction: str) -> bool:
    match = _DOCKER_FIRST_WORD.match(instruction)
    if match is None:
        return False
    keyword, rest = match.group(1).upper(), match.group(2)
    if keyword == "ONBUILD":
        match = _DOCKER_FIRST_WORD.match(rest)
        if match is None:
            return False
        keyword, rest = match.group(1).upper(), match.group(2)
    if keyword not in _DOCKER_HEREDOC_INSTRUCTIONS:
        return False
    return _DOCKER_BARE_HEREDOC.search(_without_arithmetic(rest)) is not None


def _character_blockers(text: str) -> list[str]:
    """制御文字 (`\\t` / `\\n` と、`\\n` の直前の `\\r` は除く)、ASCII 以外の空白、
    2 文字目以降の U+FEFF。行の分け方や継続の判定を、BuildKit とずらせる文字。

    コメントの行 (_dockerfile_logical が読み飛ばす行と同じく、`strip(" \\t")` の後に
    `#` で始まる行) の `#` より後ろの Zs の空白 (全角空白・NBSP など) は数えない
    (日本語のコメントで誤って止めない)。次は除外しない:
    - `#` より前の文字 (NBSP や U+3000 があればコメントの行ではない)
    - parser directive とそれに見える行 (`# syntax=` / `# escape=` / `# check=` など。
      `\\s` を Unicode で読むので `#<U+3000>escape=` も含む): 要約が読む値と BuildKit が
      読む値を食い違わせられる
    - 制御文字 (行末の 1 つを除く `\\r`・VT・FF・NEL など)、途中の U+FEFF、U+2028 /
      U+2029 (Zs ではない)
    """
    out: list[str] = []
    lines = _without_bom(text).split("\n")
    control = space = bom = ""
    for number, line in enumerate(lines):
        last = number == len(lines) - 1
        body = line.lstrip(" \t")
        comment_from = (
            len(line) - len(body)
            if body.startswith("#") and not _DOCKER_DIRECTIVE_LIKE.match(line)
            else -1
        )
        for i, ch in enumerate(line):
            code = ord(ch)
            if ch == "\t" or (ch == "\r" and i == len(line) - 1 and not last):
                continue
            if ch == _BOM:
                bom = bom or "U+FEFF"
            elif unicodedata.category(ch) == "Cc":
                control = control or f"U+{code:04X}"
            elif ch.isspace() and code > 0x7F:
                excused = 0 <= comment_from < i and unicodedata.category(ch) == "Zs"
                if not excused:
                    space = space or f"U+{code:04X}"
    if control:
        out.append(DOCKERFILE_CONTROL_BLOCKER.format(control))
    if space:
        out.append(DOCKERFILE_SPACE_BLOCKER.format(space))
    if bom:
        out.append(DOCKERFILE_BOM_BLOCKER)
    return out


def dockerfile_instructions(text: str) -> list[str]:
    """ビルドで実行される (または中身を決める) 命令の行 (_EXECUTED_INSTRUCTIONS)。"""
    return [
        instr
        for instr in _dockerfile_lines(text)
        if instr.split(None, 1)[0].upper() in _EXECUTED_INSTRUCTIONS
    ]


def dockerfile_blockers(text: str) -> list[str]:
    """要約が中身を正しく出せない Dockerfile の要素 (あれば承認させない)。

    - 先頭の directive (`line.lstrip(" \\t")` で読む) のうち、字下げしたもの、`//` の
      もの、`\\` 以外の `escape`、同じ名前 (大文字・小文字は区別しない) が 2 つ以上
      あるもの。JSON として読めるファイル。BuildKit の読み方とずれうる
    - `\\` 以外の `# escape=`: 継続や引用の解釈が変わり、一覧に出ない命令を作れる
      (`LABEL a=b `` の次の行の RUN など)
    - RUN / COPY / ADD (ONBUILD の中のものも) の `<<` (`<<<` の一部でないもの。
      空白も展開も含まない `$((…))` の中は除く): heredoc の本文 (`RUN <<EOF` の中身、`COPY <<EOF /x.sh` の
      中身) は一覧に出せない。BuildKit の語の分け方は写さず、安全側に倒す
    - 制御文字 (`\\t` / `\\n` と `\\n` の直前の `\\r` は除く)、ASCII 以外の空白、2 文字目
      以降の U+FEFF: 行の分け方や継続の判定をずらせる
    先頭の BOM は外してから読む。
    """
    out: list[str] = []
    seen: set[str] = set()
    for kind, name, value in _leading_directives(text):
        # 名前は _leading_directives が小文字にしている (BuildKit も小文字にして読む)。
        if name in seen and DOCKERFILE_DUPLICATE_DIRECTIVE_BLOCKER not in out:
            out.append(DOCKERFILE_DUPLICATE_DIRECTIVE_BLOCKER)
        seen.add(name)
        if kind == "#indented" and DOCKERFILE_INDENTED_DIRECTIVE_BLOCKER not in out:
            out.append(DOCKERFILE_INDENTED_DIRECTIVE_BLOCKER)
        if kind == "//" and DOCKERFILE_SLASH_DIRECTIVE_BLOCKER not in out:
            out.append(DOCKERFILE_SLASH_DIRECTIVE_BLOCKER)
        if name == "escape" and value != "\\" and DOCKERFILE_ESCAPE_BLOCKER not in out:
            out.append(DOCKERFILE_ESCAPE_BLOCKER)
    if _looks_like_json(text):
        out.append(DOCKERFILE_JSON_BLOCKER)
    if any(
        _has_heredoc(shown) or _has_heredoc(raw)
        for shown, raw in _dockerfile_logical(text)
    ):
        out.append(DOCKERFILE_HEREDOC_BLOCKER)
    out += _character_blockers(text)
    return out


def image_build_preview_data(
    *,
    context: str,
    dockerfile: str,
    sha256: str,
    bases: Sequence[Sequence[Any]],
    instructions: Sequence[str],
    context_diff: Mapping[str, Any],
    docker: Mapping[str, str] | None,
    blockers: Sequence[str],
) -> dict:
    """`csb image build` の確認の data (gate.render_preview が描く)。

    context_diff は build context と HEAD の差 ({"state": "none" / "changed" /
    "unknown", "count": 変わったファイルの数})。
    """
    return {
        "schema": PREVIEW_SCHEMA,
        "kind": "image",
        "context": context,
        "dockerfile": dockerfile,
        "sha256": sha256,
        "bases": [list(b) for b in bases],
        "instructions": list(instructions),
        "context_diff": dict(context_diff),
        "docker": None if docker is None else dict(docker),
        "blockers": list(blockers),
    }


def _context_diff_text(diff: Any) -> str:
    if not isinstance(diff, dict):
        raise ValueError("preview data: context_diff")
    state, count = diff.get("state"), diff.get("count")
    if not (isinstance(count, int) and not isinstance(count, bool) and count >= 0):
        raise ValueError("preview data: context_diff.count")
    if state == "none":
        return "HEAD との差: なし"
    if state == "changed":
        return f"HEAD との差: あり (未コミットの変更 {count} ファイル)"
    if state == "unknown":
        return "HEAD との差: 調べられない"
    raise ValueError("preview data: context_diff.state")


def _render_image(d: dict) -> tuple[list[str], list[str], list[str], str]:
    sha = _str(d["sha256"])
    if sha and not _SHA256.fullmatch(sha):
        raise ValueError("preview data: bad sha")
    bases = d["bases"]
    if not isinstance(bases, list):
        raise ValueError("preview data: bases")
    instructions = _strs(d["instructions"])
    lines = [
        f"build context: {_shown(d['context'])}",
        _context_diff_text(d["context_diff"]),
        f"Dockerfile: {display_text(_str(d['dockerfile']), PREVIEW_DECISION_CHARS)}"
        + (f" (sha256 {sha})" if sha else " (読めない)"),
        f"ビルドで実行する命令 ({len(instructions)} 件):",
        *(f"  {display_text(i, PREVIEW_DECISION_CHARS)}" for i in instructions),
        f"ベースイメージ ({len(bases)} 件。ビルドの時に取ってくる):",
    ]
    for item in bases:
        if not (
            isinstance(item, list) and len(item) == 2 and isinstance(item[1], bool)
        ):
            raise ValueError("preview data: base")
        pinned = "digest で固定" if item[1] else "digest で固定していない"
        lines.append(f"  - {display_text(_str(item[0]), 200)} ({pinned})")
    lines += [
        "ネットワーク: ビルドの間ずっと接続する (RUN の命令がネットワークを使える)",
        _docker_text(d["docker"]),
        "docker の設定: ユーザーの docker 設定 (~/.docker の credential helper・"
        "cli-plugins。保護の外) を使う",
    ]
    lines += _blocker_lines(_strs(d["blockers"]), "image build")
    return lines, [], [], ""


def render_preview(data: Any, headline: str) -> tuple[str, str]:
    """data を確認の文面にする。(文面, "") か、承認させないときは ("", 固定の理由)。

    data は子プロセスの出力 (JSON) なので型を確かめ、合わなければ ValueError。文字列は
    すべて display_text を通す。判定に効く部分が PREVIEW_DECISION_CHARS 文字か
    PREVIEW_DECISION_LINES 行を超えたら、この画面では承認できない。人が自分で実行する
    手順 (ハーネスに触れるパッチ) は判定の枠の外に置き、切らない。
    """
    if not isinstance(data, dict) or data.get("schema") != PREVIEW_SCHEMA:
        raise ValueError("preview data: schema")
    kind = data.get("kind")
    try:
        if kind == "apply":
            body, howto, rest, mark = _render_apply(data)
            overflow = APPLY_OVERFLOW
        elif kind == "create":
            body, howto, rest, mark = _render_create(data)
            overflow = CREATE_OVERFLOW
        elif kind == "image":
            body, howto, rest, mark = _render_image(data)
            overflow = IMAGE_BUILD_OVERFLOW
        else:
            raise ValueError("preview data: kind")
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("preview data: shape") from exc
    lines = [display_text(_str(headline)), PREVIEW_IDENTITY_LINE, *body]
    text = "\n".join(lines)
    if len(text) > PREVIEW_DECISION_CHARS or len(lines) > PREVIEW_DECISION_LINES:
        return "", overflow
    if kind == "image" and data["blockers"]:
        # image build は CLI が Dockerfile を確かめ直さないので、ここで承認させない
        found = _strs(data["blockers"])
        listed = "、".join(_shown(b, 200) for b in found[:5])
        more = f" ほか {len(found) - 5} 件" if len(found) > 5 else ""
        return "", f"{IMAGE_BUILD_BLOCKED}: {listed}{more}"
    advisory = {"create": CREATE_ADVISORY, "image": IMAGE_BUILD_ADVISORY}.get(kind)
    if advisory:
        # 判定の枠の外 (この 1 行で、枠の近くの要約が承認できなくならないように)。
        text = "\n".join([*lines[:2], advisory, *lines[2:]])
    text = "\n".join([text, *howto])
    budget = PREVIEW_TOTAL_CHARS - len(text) - len(mark.format(count=len(rest))) - 2
    shown: list[str] = []
    for line in rest:
        if len(line) + 1 > budget:
            break
        shown.append(line)
        budget -= len(line) + 1
    parts = [text, *shown]
    if len(shown) < len(rest):
        parts.append(mark.format(count=len(rest) - len(shown)))
    return "\n".join(parts), ""


# ---------------------------------------------------------------------------
# 9. ~/.local/bin/csb のラッパー
# ---------------------------------------------------------------------------
# symlink ではなく固定の内容の通常ファイル (0755)。symlink だと、~/.local/bin を宛先に
# した cp が link を辿って claude_sandbox.py を上書きでき、realpath の照合はそのまま
# 通っていた。install.sh はこの関数で中身を書き、フックは parse_csb_wrapper で
# バイト列の完全一致を確かめる (テンプレートはここ 1 か所)。
CSB_LINK = (".local", "bin", "csb")
_WRAPPER_PATH = r"/[A-Za-z0-9._+@/-]*"
# 起動の前に環境から外す変数 (ランチャー 2 つと csb のラッパーが同じ一覧を unset する。
# テストで固定)。macOS の framework 版 python は __PYVENV_LAUNCHER__ / PYTHONEXECUTABLE で
# sys.executable と pyvenv.cfg の探し先を変え (-I でも止まらない)、/usr/bin の xcrun の
# shim (python3・git) は DEVELOPER_DIR / TOOLCHAINS / SDKROOT で別の実体を選ぶ。
LAUNCH_ENV_DROP = (
    "DEVELOPER_DIR",
    "TOOLCHAINS",
    "SDKROOT",
    "__PYVENV_LAUNCHER__",
    "PYTHONEXECUTABLE",
    "PYTHONHOME",
    "PYTHONPATH",
)
_WRAPPER_UNSET = "unset " + " ".join(LAUNCH_ENV_DROP)
_WRAPPER_TEXT = re.compile(
    rf"#!/bin/sh\n{_WRAPPER_UNSET}\n"
    rf"exec '({_WRAPPER_PATH})' -I -S '({_WRAPPER_PATH})' \"\$@\"\n"
)


def csb_wrapper_text(python: str, home: str) -> str:
    """`unset …` と `exec '<python>' -I -S '<home>/.claude/hooks/claude_sandbox.py' "$@"`。

    ラッパーはエージェントのシェルから起動されるので、ランチャーと同じ LAUNCH_ENV_DROP を
    ここで外す (フックの環境を整えても csb には届かない)。

    python と home は絶対パスで、単引用符の中にそのまま置ける文字だけ (英数字と
    `._+@/-`)。それ以外は ValueError: 引用符や空白を含むパスを無理に引用しない。
    """
    cli = posixpath.join(home, ".claude", "hooks", "claude_sandbox.py")
    for path in (python, cli):
        if not (
            isinstance(path, str)
            and re.fullmatch(_WRAPPER_PATH, path)
            and path.startswith("/")
            and len(path) > 1
        ):
            raise ValueError(f"unsafe path for the csb wrapper: {path!r}")
    if not (isinstance(home, str) and home.startswith("/") and len(home) > 1):
        raise ValueError(f"unsafe home for the csb wrapper: {home!r}")
    return f"#!/bin/sh\n{_WRAPPER_UNSET}\nexec '{python}' -I -S '{cli}' \"$@\"\n"


def parse_csb_wrapper(data: bytes, home: str) -> str | None:
    """data が csb_wrapper_text(python, home) と完全に一致するとき、その python。"""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return None
    match = _WRAPPER_TEXT.fullmatch(text)
    if match is None:
        return None
    try:
        expected = csb_wrapper_text(match.group(1), home)
    except ValueError:
        return None
    return match.group(1) if text == expected else None


# ---------------------------------------------------------------------------
# 10. 信頼できるシステムの python3 (フックと csb を動かすインタプリタ)
# ---------------------------------------------------------------------------
# フックと csb は、root 所有のシステムの python3 でだけ動かす。PATH・pyenv の shim・
# .python-version には従わない (shim は cwd の .python-version に従い、~/.pyenv の下に
# 作った名前も選ぶので、偽のインタプリタでフック自身を乗っ取れた)。
#
# 判定はここ (trusted_path / trusted_python) と、ランチャーの _trusted_path /
# _trusted_python (シェル) の 2 か所にある。ランチャーは信頼する前のインタプリタを
# 起動できないので、シェルで同じ規則を持つ。2 つが食い違わないことは
# tests/test_bash_review_launcher.py が同じ fixture で確かめる。
TRUSTED_PYTHON_MIN = (3, 9)
# 版の確認。ランチャーも同じ文字列で確かめる (テストで固定)。
TRUSTED_PYTHON_PROBE = "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
TRUSTED_PYTHON_PROBE_TIMEOUT = 5.0
_TRUST_HOPS = 40
_ROOT_ONLY = frozenset({0})


def trusted_python_candidates(platform: str | None = None) -> tuple[str, ...]:
    """固定の候補 (この順に見る)。macOS は CommandLineTools の python3 を先に見る。

    /usr/bin/python3 は xcrun の shim で、起動すると sys.executable が developer dir の
    python3 になる。CommandLineTools を先に置くと、ランチャーが選ぶパスとフックの
    sys.executable が同じになる (csb_is_ours がこの一致を求める)。
    """
    name = sys.platform if platform is None else platform
    if name == "darwin":
        return (
            "/Library/Developer/CommandLineTools/usr/bin/python3",
            "/usr/bin/python3",
        )
    if name.startswith("linux"):
        return ("/usr/bin/python3",)
    return ()


def _trust_parent(path: str) -> str:
    return posixpath.dirname(path) or "/"


def _trust_owned(st: os.stat_result, owners: frozenset[int]) -> bool:
    return st.st_uid in owners


def trusted_path(
    path: str,
    *,
    owners: Iterable[int] = _ROOT_ONLY,
    top: str = "/",
    write_groups: Iterable[int] = (),
) -> str:
    """path が信頼できる実行ファイルか。信頼できれば ""、できなければ理由。

    path を 1 要素ずつ辿り (symlink は宛先を展開して続ける)、次をすべて求める:
    - path は絶対パスで top の下。`..` も symlink の宛先も top の外に出ない
    - 通るディレクトリ (top を含む) は symlink でない本物のディレクトリで、owners の
      誰かの所有で、group / other が書けない
    - 通る symlink は owners の誰かの所有 (mode は見ない。Linux の symlink は常に 777)
    - 行き着いた先は owners の誰かの所有の通常ファイルで、group / other が書けず、
      実行の bit がある
    owners の既定は root だけ、top の既定は /。ほかの値はテストのためにある。
    ランチャーの _trusted_path と同じ規則 (変えるときは両方を変える)。

    write_groups は「group が書けても構わない」gid (既定は無し)。インタプリタの判定
    (ランチャーと同じ規則) では使わない。docker の実体の確認 (claude_sandbox) が、
    macOS の admin (sudo できる人の group) が書ける /Applications と Homebrew の prefix
    のために使う。other が書けるものは、どの場合も通さない。
    """
    owner_set = frozenset(owners)
    group_ok = frozenset(write_groups)

    def writable(st: os.stat_result) -> bool:
        if st.st_mode & 0o002:
            return True
        return bool(st.st_mode & 0o020) and st.st_gid not in group_ok

    if not (isinstance(path, str) and path.startswith("/")):
        return "絶対パスでない"
    if not (isinstance(top, str) and top.startswith("/")):
        return "top が絶対パスでない"
    base = top
    if base != "/" and (posixpath.normpath(base) != base or base.startswith("//")):
        return "top が正規化されていない"
    queue = _trust_parts(path, base)
    if queue is None:
        return "top の外"
    try:
        st = os.lstat(base)
    except OSError:
        return f"{display_text(base, 120)} を調べられない"
    if not stat.S_ISDIR(st.st_mode):
        return f"{display_text(base, 120)} が本物のディレクトリでない"
    if not _trust_owned(st, owner_set) or writable(st):
        return f"{display_text(base, 120)} の所有者か書き込み権限が不正"
    prefix = base
    hops = 0
    while queue:
        part = queue.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if prefix == base:
                return "top の外"
            prefix = _trust_parent(prefix)
            continue
        here = posixpath.join(prefix, part)
        try:
            st = os.lstat(here)
        except OSError:
            return f"{display_text(here, 120)} が無い"
        if not _trust_owned(st, owner_set):
            return f"{display_text(here, 120)} の所有者が不正"
        if stat.S_ISLNK(st.st_mode):
            hops += 1
            if hops > _TRUST_HOPS:
                return "symlink が多すぎる"
            try:
                target = os.readlink(here)
            except OSError:
                return f"{display_text(here, 120)} を読めない"
            if target.startswith("/"):
                rest = _trust_parts(target, base)
                if rest is None:
                    return "symlink の宛先が top の外"
                prefix = base
                queue = rest + queue
            else:
                queue = target.split("/") + queue
            continue
        if writable(st):
            return f"{display_text(here, 120)} は group / other が書ける"
        if any(q not in ("", ".") for q in queue):
            if not stat.S_ISDIR(st.st_mode):
                return f"{display_text(here, 120)} がディレクトリでない"
            prefix = here
            continue
        if queue:
            # 残りが "." と "" だけ: ディレクトリを指している
            return f"{display_text(here, 120)} が通常ファイルでない"
        if not stat.S_ISREG(st.st_mode):
            return f"{display_text(here, 120)} が通常ファイルでない"
        if not st.st_mode & 0o111:
            return f"{display_text(here, 120)} を実行できない"
        return ""
    return "通常ファイルに行き着かない"


def _trust_parts(path: str, base: str) -> list[str] | None:
    """base の下の要素 (base より上なら None)。base が / なら全部。"""
    if base == "/":
        return path.split("/")
    if path == base:
        return []
    if path.startswith(base + "/"):
        return path[len(base) + 1 :].split("/")
    return None


def _probe_version(python: str) -> bool:
    env = {k: v for k, v in os.environ.items() if k not in LAUNCH_ENV_DROP}
    try:
        res = subprocess.run(  # nosec B603 - 信頼を確かめた固定のパス
            [python, "-I", "-S", "-c", TRUSTED_PYTHON_PROBE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=TRUSTED_PYTHON_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def trusted_python(
    candidates: Sequence[str] | None = None,
    *,
    owners: Iterable[int] = _ROOT_ONLY,
    top: str = "/",
    version_ok: Callable[[str], bool] | None = None,
) -> str | None:
    """候補を順に見て、trusted_path を満たし版が 3.9 以上の最初のもの。無ければ None。

    版は trusted_path を満たした候補だけを起動して確かめる (信頼する前に起動しない)。
    ランチャーの _trusted_python と同じ選び方。
    """
    probe = _probe_version if version_ok is None else version_ok
    names = trusted_python_candidates() if candidates is None else candidates
    for name in names:
        if trusted_path(name, owners=owners, top=top) == "" and probe(name):
            return name
    return None


def running_python_problem(python: str, executable: str, version: Sequence[Any]) -> str:
    """ラッパーの python が、いまフックを動かしているインタプリタそのものか。

    満たせば ""。求めるもの: 固定の候補のどれかで、trusted_path を満たし、起動した
    パス (sys.executable) と文字列として同じで、版が 3.9 以上。realpath では比べない:
    別の venv の bin/python が同じ実体を指すと、realpath は一致しても別の pyvenv.cfg や
    site-packages を読む。
    """
    if python not in trusted_python_candidates():
        return "固定の候補でない"
    if python != executable:
        return "フックのインタプリタと違う"
    if tuple(version[:2]) < TRUSTED_PYTHON_MIN:
        return "版が古い"
    return trusted_path(python)
