"""Tests for the container sandbox CLI (.claude/hooks/claude_sandbox.py).

docker is replaced by FakeDocker, a small in-process emulator monkeypatched onto
`claude_sandbox._docker` (the CLI's only docker entry point). git runs for real:
on the host in tmp repositories, and for `docker exec` in a per-container
directory that stands in for the container's filesystem (/work, /csb and /tmp are
mapped under the container's root). So create -> exec -> export -> verify ->
attest -> apply runs the CLI's real git command sequences end to end.

`_docker` itself is tested against a stub `docker` shell script.
"""

import copy
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _sandbox_gate as gate  # noqa: E402
import claude_sandbox as csb  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sandbox"
DOCKER_BIN = "/usr/local/bin/docker"
DOCKER_HOST = "unix:///var/run/docker.sock"
EP = csb.Endpoint(DOCKER_BIN, DOCKER_HOST)
T0 = 1_800_000_000.0
# Built at runtime so no literal in this file looks like a live credential.
FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
IDS = ["5e1f0c2a9b3d", "7a7a7a7a7a7a", "0b0b0b0b0b0b", "1c1c1c1c1c1c"]


def _hex(n):
    return hashlib.sha256(f"fake-{n}".encode()).hexdigest()


def _ok(out=b""):
    return csb.DockerResult(0, out if isinstance(out, bytes) else out.encode(), b"")


def _fail(msg="boom", rc=1):
    return csb.DockerResult(rc, b"", msg.encode())


def _extract(data, dest):
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="fully_trusted")  # nosec B202
        else:  # pragma: no cover - Python < 3.12
            tar.extractall(dest)  # nosec B202


class FakeDocker:
    """Just enough of the docker CLI for the commands claude_sandbox.py issues."""

    # Container paths, mapped under the fake container root.
    MAPPED = ("/work", "/csb", "/tmp")  # nosec B108
    VALUE_FLAGS = {
        "--name",
        "--label",
        "--network",
        "--cap-drop",
        "--cap-add",
        "--security-opt",
        "--pids-limit",
        "--memory",
        "--memory-swap",
        "--cpus",
        "--ipc",
        "--tmpfs",
        "--restart",
        "--entrypoint",
        "--log-driver",
        "--log-opt",
        "--user",
        "--workdir",
    }

    def __init__(self, base, template):
        self.configs = []
        self.base = Path(base)
        self.template = template
        self.calls = []
        self.counter = 0
        self.containers = {}
        self.images = {}
        self.networks = {}
        self.context = {"Host": DOCKER_HOST, "SkipTLSVerify": False}
        self.fail = {}
        self.exec_hook = None
        self.inspect_hook = None
        self.build_inputs = []
        self.generic = self.add_image(gate.GENERIC_IMAGE_REF, user="1000:1000")

    # -- state helpers -------------------------------------------------------
    def _id(self):
        self.counter += 1
        return _hex(self.counter)

    def add_image(self, ref, *, user="", volumes=None, labels=None, root=None):
        iid = "sha256:" + self._id()
        image = {
            "Id": iid,
            "refs": {ref} if ref else set(),
            "User": user,
            "Volumes": volumes,
            "Labels": dict(labels or {}),
            "root": root,
        }
        self.images[iid] = image
        return image

    def find_image(self, ref):
        for image in self.images.values():
            if ref == image["Id"] or ref in image["refs"]:
                return image
        return None

    def find_container(self, ref):
        for c in self.containers.values():
            if ref in (c["Id"], c["name"]):
                return c
        return None

    def add_container(self, name, labels, *, running=True):
        cid = self._id()
        root = self.base / "ctr" / cid[:12]
        root.mkdir(parents=True)
        self.containers[cid] = {
            "Id": cid,
            "name": name,
            "image": self.generic["Id"],
            "labels": dict(labels),
            "user": "1000:1000",
            "network_mode": "none",
            "networks": set(),
            "running": running,
            "root": root,
        }
        return self.containers[cid]

    def work(self, sid):
        return self.find_container(gate.container_name(sid))

    def image_json(self, image):
        config = {"User": image["User"], "Env": ["PATH=/usr/bin:/bin"]}
        if image["Volumes"]:
            config["Volumes"] = image["Volumes"]
        if image["Labels"]:
            config["Labels"] = image["Labels"]
        return {"Id": image["Id"], "RepoTags": sorted(image["refs"]), "Config": config}

    def container_json(self, c):
        info = copy.deepcopy(self.template)
        info["Id"] = c["Id"]
        info["Name"] = "/" + c["name"]
        info["Image"] = c["image"]
        info["State"]["Status"] = "running" if c["running"] else "created"
        info["State"]["Running"] = c["running"]
        info["Config"]["Labels"] = dict(c["labels"])
        info["Config"]["User"] = c["user"]
        info["HostConfig"]["NetworkMode"] = c["network_mode"]
        info["NetworkSettings"]["Networks"] = {n: {} for n in sorted(c["networks"])}
        if self.inspect_hook is not None:
            self.inspect_hook(info)
        return info

    def commands(self, *prefix):
        return [a for _, a in self.calls if a[: len(prefix)] == list(prefix)]

    # -- dispatch -----------------------------------------------------------
    def __call__(
        self,
        target,
        args,
        *,
        input=None,
        input_path=None,
        capture=True,
        timeout=None,
        on_spawn=None,
        output_to_stderr=False,
        user_config=False,
    ):
        ep = csb._endpoint(target)
        args = list(args)
        self.calls.append((ep, args))
        self.configs.append((args[:2], user_config))
        for key in (" ".join(args[:2]), args[0]):
            if key in self.fail:
                res = self.fail[key]
                return res(args) if callable(res) else res
        if input_path is not None:
            input = Path(input_path).read_bytes()
        handler = getattr(self, "do_" + args[0])
        res = handler(args, input, on_spawn)
        if capture:
            return res
        out = sys.stderr if output_to_stderr else sys.stdout
        out.write(res.stdout.decode("utf-8", "replace"))
        sys.stderr.write(res.stderr.decode("utf-8", "replace"))
        return csb.DockerResult(res.returncode, b"", b"")

    @staticmethod
    def _filters(args):
        out = []
        for i, a in enumerate(args):
            if a == "--filter":
                out.append(args[i + 1])
        return out

    def _match_labels(self, labels, filters, name=None):
        for f in filters:
            kind, _, value = f.partition("=")
            if kind == "label":
                key, _, want = value.partition("=")
                if labels.get(key) != want:
                    return False
            elif kind == "name":
                if f"^/{name}$" != value:
                    return False
        return True

    def do_context(self, args, _input, _spawn):
        assert args[:2] == ["context", "inspect"]
        return _ok(json.dumps(self.context))

    def do_image(self, args, _input, _spawn):
        assert args[1] == "inspect"
        found = [self.find_image(r) for r in args[2:]]
        if not all(found):
            return _fail("Error: No such image")
        return _ok(json.dumps([self.image_json(i) for i in found]))

    def do_images(self, args, _input, _spawn):
        ids = [
            i["Id"]
            for i in self.images.values()
            if self._match_labels(i["Labels"], self._filters(args))
        ]
        return _ok("\n".join(ids))

    def do_pull(self, args, _input, _spawn):
        self.add_image(args[1], user=getattr(self, "pull_user", ""))
        return _ok(f"pulled {args[1]}\n")

    def do_build(self, args, data, _spawn):
        labels, tags, iidfile = {}, set(), None
        i = 1
        while i < len(args) - 1:
            if args[i] == "--label":
                k, _, v = args[i + 1].partition("=")
                labels[k] = v
            elif args[i] == "-t":
                tags.add(args[i + 1])
            elif args[i] == "--iidfile":
                iidfile = args[i + 1]
            if args[i] in ("--label", "-t", "--iidfile", "-f"):
                i += 2
            else:
                i += 1
        root = None
        if args[-1] == "-":
            self.build_inputs.append((args, data))
            root = self.base / "built" / str(self.counter)
            root.mkdir(parents=True)
        image = self.add_image(None, labels=labels, root=root)
        image["refs"] = tags
        if iidfile:
            Path(iidfile).write_text(image["Id"])
        return _ok("built\n")

    def do_network(self, args, _input, _spawn):
        sub = args[1]
        if sub == "create":
            labels = {}
            for i, a in enumerate(args):
                if a == "--label":
                    k, _, v = args[i + 1].partition("=")
                    labels[k] = v
            nid = self._id()
            self.networks[args[-1]] = {"Id": nid, "Name": args[-1], "Labels": labels}
            return _ok(nid + "\n")
        if sub in ("connect", "disconnect"):
            c = self.find_container(args[3])
            if c is None or args[2] not in self.networks:
                return _fail("no such network or container")
            if sub == "connect":
                if args[2] in c["networks"]:
                    return _fail("endpoint already exists")
                c["networks"].add(args[2])
            else:
                if args[2] not in c["networks"]:
                    return _fail("is not connected")
                c["networks"].discard(args[2])
            return _ok()
        if sub == "ls":
            filters = self._filters(args)
            ids = [
                n["Id"]
                for n in self.networks.values()
                if self._match_labels(n["Labels"], filters)
            ]
            return _ok("\n".join(ids))
        if sub == "inspect":
            items = [n for n in self.networks.values() if n["Id"] in args[2:]]
            return _ok(json.dumps(items))
        if sub == "rm":
            for nid in args[2:]:
                for name, n in list(self.networks.items()):
                    if n["Id"] == nid:
                        del self.networks[name]
            return _ok()
        raise AssertionError(args)

    def do_create(self, args, _input, _spawn):
        opts = {"--label": []}
        i = 1
        while i < len(args) - 2:
            a = args[i]
            if a in self.VALUE_FLAGS:
                if a == "--label":
                    opts["--label"].append(args[i + 1])
                else:
                    opts[a] = args[i + 1]
                i += 2
            else:
                assert a in ("--init", "--no-healthcheck"), a
                i += 1
        image = self.find_image(args[-2])
        if image is None:
            return _fail("No such image")
        name = opts["--name"]
        if self.find_container(name) is not None:
            return _fail("Conflict. The container name is already in use")
        cid = self._id()
        root = self.base / "ctr" / cid[:12]
        if image["root"] is not None:
            import shutil

            shutil.copytree(image["root"], root, symlinks=True)
        else:
            root.mkdir(parents=True)
        labels = dict(image["Labels"])
        for item in opts["--label"]:
            k, _, v = item.partition("=")
            labels[k] = v
        network = opts["--network"]
        self.containers[cid] = {
            "Id": cid,
            "name": name,
            "image": image["Id"],
            "labels": labels,
            "user": opts["--user"],
            "network_mode": network,
            "networks": set() if network == "none" else {network},
            "running": False,
            "root": root,
        }
        return _ok(cid + "\n")

    def do_cp(self, args, data, _spawn):
        assert args[1:3] == ["-a", "-"], args
        c = self.find_container(args[3].split(":")[0])
        _extract(data, c["root"])
        return _ok()

    def do_start(self, args, _input, _spawn):
        self.find_container(args[1])["running"] = True
        return _ok(args[1] + "\n")

    def do_stop(self, args, _input, _spawn):
        self.find_container(args[-1])["running"] = False
        return _ok()

    def do_rm(self, args, _input, _spawn):
        for ref in args[2:]:
            c = self.find_container(ref)
            if c is not None:
                del self.containers[c["Id"]]
        return _ok()

    def do_rmi(self, args, _input, _spawn):
        for ref in args[2:]:
            image = self.find_image(ref)
            if image is not None:
                del self.images[image["Id"]]
        return _ok()

    def do_commit(self, args, _input, _spawn):
        import shutil

        c = self.find_container(args[1])
        root = self.base / "img" / str(self.counter)
        shutil.copytree(c["root"], root, symlinks=True)
        image = self.add_image(args[2], user=c["user"], labels=c["labels"], root=root)
        return _ok(image["Id"] + "\n")

    def do_inspect(self, args, _input, _spawn):
        assert args[1:3] == ["--type", "container"]
        found = [self.find_container(r) for r in args[3:]]
        if not all(found):
            return _fail("Error: No such container")
        return _ok(json.dumps([self.container_json(c) for c in found]))

    def do_ps(self, args, _input, _spawn):
        filters = self._filters(args)
        ids = [
            c["Id"]
            for c in self.containers.values()
            if self._match_labels(c["labels"], filters, c["name"])
        ]
        return _ok("\n".join(ids))

    def map(self, c, value):
        for prefix in self.MAPPED:
            if value == prefix or value.startswith(prefix + "/"):
                return str(c["root"]) + value
        return value

    def do_exec(self, args, _input, on_spawn):
        i, env, workdir, user = 1, {}, "/work", None
        while args[i].startswith("-"):
            if args[i] in ("-i", "-t"):
                i += 1
                continue
            if args[i] == "-e":
                k, _, v = args[i + 1].partition("=")
                env[k] = v
            elif args[i] == "-w":
                workdir = args[i + 1]
            elif args[i] == "-u":
                user = args[i + 1]
            i += 2
        c = self.find_container(args[i])
        argv = args[i + 1 :]
        assert user is not None, "every exec names its user"
        if c is None or not c["running"]:
            return _fail("container is not running")
        if on_spawn is not None:
            on_spawn()
        if self.exec_hook is not None:
            res = self.exec_hook(c, argv)
            if res is not None:
                return res
        home = c["root"] / "home"
        home.mkdir(exist_ok=True)
        cwd = Path(self.map(c, workdir))
        cwd.mkdir(parents=True, exist_ok=True)
        run_env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        run_env.update({k: self.map(c, v) for k, v in env.items()})
        proc = subprocess.run(  # noqa: S603
            [self.map(c, a) for a in argv],
            cwd=cwd,
            env=run_env,
            capture_output=True,
            check=False,
        )
        return csb.DockerResult(proc.returncode, proc.stdout, proc.stderr)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def git(repo, *args, input=None):
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],
        input=input,
        capture_output=True,
        check=True,
    ).stdout


def make_repo(path, files, *, commit=True):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
    if commit:
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "init")
    return Path(os.path.realpath(path))


BASE_FILES = {
    "README.md": "hello\n",
    "src/app.py": "print('app')\n",
    "old.txt": "to be removed\n",
    ".gitignore": "build/\nnode_modules/\n",
}


class Env:
    def __init__(self, tmp_path, monkeypatch, capsys):
        self.tmp = tmp_path
        self.capsys = capsys
        self.monkeypatch = monkeypatch
        gitconfig = tmp_path / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = Test\n\temail = test@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgSign = false\n"
        )
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            monkeypatch.delenv(key, raising=False)
        self.state = str(tmp_path / "state")
        monkeypatch.setattr(csb, "_state_root", lambda: self.state)
        template = json.loads((FIXTURES / "inspect-work.json").read_text("utf-8"))
        self.fake = FakeDocker(tmp_path / "docker", template)
        monkeypatch.setattr(csb, "_docker", self.fake)
        monkeypatch.setattr(csb, "_find_docker_binary", lambda: DOCKER_BIN)
        # the fake DOCKER_BIN is not on this machine: the re-check at every use
        # is pinned in TestBinaryAndEndpoint
        monkeypatch.setattr(csb, "_check_pinned_docker", lambda path: None)
        self.clock = Clock(T0)
        monkeypatch.setattr(csb, "_now", self.clock)
        self.tty = False
        monkeypatch.setattr(csb, "_isatty", lambda fd: self.tty)
        ids = iter(IDS)
        monkeypatch.setattr(csb, "_new_id", lambda: next(ids))
        self.repo = make_repo(tmp_path / "repo", BASE_FILES)
        monkeypatch.chdir(self.repo)

    def run(self, *argv):
        self.capsys.readouterr()
        rc = csb.main(list(argv))
        out = self.capsys.readouterr()
        return rc, out.out, out.err

    def meta(self, sid):
        return gate.load_meta(gate.meta_path(self.state, sid))

    def create(self, *argv):
        rc, out, err = self.run("create", *argv)
        assert rc == 0, err
        sid = out.strip().splitlines()[-1].split()[-1]
        assert gate.is_sandbox_id(sid)
        return sid

    def work_root(self, sid):
        return self.fake.work(sid)["root"] / "work"

    def export(self, sid):
        rc, out, err = self.run("export", sid)
        assert rc == 0, err
        return out.splitlines()[0]

    def attest_all(self, sid, sha, reviewers=gate.REQUIRED_REVIEWERS):
        for reviewer in reviewers:
            rc, _, err = self.run(
                "attest", sid, "--patch", sha, "--review", f"{reviewer}=pass"
            )
            assert rc == 0, err


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    return Env(tmp_path, monkeypatch, capsys)


@pytest.fixture
def sandbox(env):
    return env.create("--image", "generic")


def change_work(env, sid):
    work = env.work_root(sid)
    (work / "README.md").write_text("hello\nworld\n")
    (work / "new.txt").write_text("brand new\n")
    (work / "old.txt").unlink()
    (work / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    (work / "build").mkdir(exist_ok=True)
    (work / "build" / "ignored.o").write_text("ignored\n")
    os.chmod(work / "src" / "app.py", 0o755)  # nosec B103


# ---------------------------------------------------------------------------
# main / usage
# ---------------------------------------------------------------------------
class TestMain:
    def test_help(self, env):
        rc, out, _ = env.run("--help")
        assert rc == 0 and "usage: csb" in out

    @pytest.mark.parametrize(
        "argv",
        [[], ["bogus"], ["exec", "BAD", "--", "x"], ["verify", IDS[0], "--", "true"]],
    )
    def test_bad_arguments_exit_2_with_usage(self, env, argv):
        rc, _, err = env.run(*argv)
        assert rc == 2
        assert "引数が不正" in err and "usage" in err
        assert env.fake.calls == []

    def test_keyboard_interrupt_is_130(self, env, monkeypatch):
        def boom(_cmd):
            raise KeyboardInterrupt

        monkeypatch.setitem(csb._HANDLERS, "ls", boom)
        assert env.run("ls")[0] == 130

    def test_main_reads_sys_argv(self, env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["csb", "ls"])
        assert csb.main() == 0

    def test_script_has_shebang(self):
        text = (REPO_ROOT / ".claude" / "hooks" / "claude_sandbox.py").read_text()
        assert text.startswith("#!/usr/bin/env python3\n")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
class TestCreate:
    def test_generic_end_to_end(self, env):
        rc, out, err = env.run("create", "--image", "generic")
        assert rc == 0, err
        lines = out.splitlines()
        assert lines[0].startswith("csb create:")
        sid = lines[-1].split()[-1]
        assert sid == IDS[0]
        meta = env.meta(sid)
        generic = env.fake.generic["Id"]
        assert meta["image"] == {
            "source": "generic",
            "ref": gate.GENERIC_IMAGE_REF,
            "id": generic,
        }
        assert (meta["uid"], meta["gid"]) == (1000, 1000)
        assert meta["docker_bin"] == DOCKER_BIN and meta["docker_host"] == DOCKER_HOST
        assert meta["repo_root"] == str(env.repo)
        assert (
            meta["base_commit"] == git(env.repo, "rev-parse", "HEAD").decode().strip()
        )
        assert (
            meta["base_tree"]
            == git(env.repo, "rev-parse", "HEAD^{tree}").decode().strip()
        )
        # setup and work containers are created with the gate's exact argv
        creates = env.fake.commands("create")
        assert creates[0] == gate.setup_create_args(sid, generic, 1000, 1000)
        assert creates[1] == gate.work_create_args(
            sid, meta["setup_image_id"], 1000, 1000
        )
        # every daemon call carries the pinned endpoint
        assert all(ep == EP for ep, a in env.fake.calls if a[0] != "context")
        # the work container is disconnected before it ever starts
        work = env.fake.work(sid)
        order = [a for _, a in env.fake.calls if work["Id"] in a]
        assert order[0][:3] == ["network", "disconnect", gate.network_name(sid)]
        assert order[1] == ["start", work["Id"]]
        assert work["networks"] == set() and work["running"]
        # the setup container is gone, the setup image carries the sandbox labels
        assert env.fake.find_container(gate.container_name(sid, "setup")) is None
        setup_image = env.fake.find_image(meta["setup_image_id"])
        assert setup_image["Labels"][gate.LABEL_ID] == sid
        # /work in the work container is the snapshot, the staging dir is removed
        root = work["root"]
        assert (root / "work" / "src" / "app.py").read_text() == "print('app')\n"
        assert list((root / "csb").iterdir()) == []
        assert (
            git(root / "work", "rev-parse", "HEAD").decode().strip()
            == meta["snap_commit"]
        )
        # state is private
        sdir = gate.sandbox_dir(env.state, sid)
        assert stat.S_IMODE(os.stat(sdir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(env.state).st_mode) == 0o700
        mpath = gate.meta_path(env.state, sid)
        assert stat.S_IMODE(os.stat(mpath).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(os.path.join(sdir, "snap.bundle")).st_mode) == 0o600
        # the temporary ref used for the bundle is deleted again
        assert git(env.repo, "for-each-ref", "refs/csb").strip() == b""

    def test_snapshot_commit_is_deterministic_for_a_tree(self, env):
        tree = git(env.repo, "rev-parse", "HEAD^{tree}").decode().strip()
        a = csb.make_bundle(str(env.repo), tree, IDS[1], str(env.tmp / "a.bundle"))
        b = csb.make_bundle(str(env.repo), tree, IDS[2], str(env.tmp / "b.bundle"))
        assert a == b
        assert git(env.repo, "rev-parse", a + "^{tree}").decode().strip() == tree

    def test_dirty_tree_is_snapshotted_and_untracked_is_counted(self, env):
        (env.repo / "README.md").write_text("dirty\n")
        (env.repo / "untracked.txt").write_text("not included\n")
        rc, out, err = env.run("create", "--image", "generic")
        assert rc == 0, err
        assert "untracked 1 件" in out
        sid = out.strip().splitlines()[-1].split()[-1]
        work = env.work_root(sid)
        assert (work / "README.md").read_text() == "dirty\n"
        assert not (work / "untracked.txt").exists()
        assert (
            env.meta(sid)["base_tree"]
            != git(env.repo, "rev-parse", "HEAD^{tree}").decode().strip()
        )
        assert env.meta(sid)["base_tree"] == csb.host_base_tree(str(env.repo))

    def test_setup_runs_and_ignored_outputs_reach_the_work_container(self, env):
        sid = env.create(
            "--image", "generic", "--setup", "mkdir -p build && echo ok > build/out"
        )
        assert (env.work_root(sid) / "build" / "out").read_text() == "ok\n"
        steps = [a for a in env.fake.commands("exec") if "/bin/sh" in a]
        assert steps and steps[0][-3:] == [
            "/bin/sh",
            "-c",
            "mkdir -p build && echo ok > build/out",
        ]

    @pytest.mark.parametrize(
        "setup, message",
        [
            ("exit 7", "exit 7 で失敗"),
            ("touch junk.txt", "git status が空でない"),
            ("echo x >> README.md", "git status が空でない"),
            ("git -c user.name=a -c user.email=b commit -q --allow-empty -m x", "HEAD"),
        ],
    )
    def test_setup_failures_abort_and_clean_up(self, env, setup, message):
        rc, _, err = env.run("create", "--image", "generic", "--setup", setup)
        assert rc == 1
        assert message in err
        self.assert_cleaned(env, IDS[0])

    def assert_cleaned(self, env, sid, generic=True):
        assert not os.path.exists(gate.sandbox_dir(env.state, sid))
        assert [
            c
            for c in env.fake.containers.values()
            if c["labels"].get(gate.LABEL_ID) == sid
        ] == []
        assert gate.network_name(sid) not in env.fake.networks
        assert [
            i for i in env.fake.images.values() if i["Labels"].get(gate.LABEL_ID) == sid
        ] == []
        assert (env.fake.find_image(gate.GENERIC_IMAGE_REF) is not None) is generic

    def test_status_output_is_shown_safely(self, env):
        rc, _, err = env.run(
            "create", "--image", "generic", "--setup", "touch 'evil\x1b[2J'"
        )
        assert rc == 1
        assert "\x1b" not in err and "\\x1b" in err

    def test_secret_in_snapshot_is_refused_before_any_docker_call(self, env):
        (env.repo / "conf.py").write_text(f"KEY = '{FAKE_AWS_KEY}'\n")
        git(env.repo, "add", "conf.py")
        git(env.repo, "commit", "-q", "-m", "c")
        rc, out, err = env.run("create")
        assert rc == 2
        assert "秘密らしき値" in out and "conf.py" in out and FAKE_AWS_KEY not in out
        assert "非対応の要素" in err
        assert env.fake.calls == []
        assert not os.path.exists(env.state)

    def test_dirty_secret_is_refused_too(self, env):
        (env.repo / "README.md").write_text(f"{FAKE_AWS_KEY}\n")
        assert env.run("create")[0] == 2

    def test_submodule_is_refused(self, env):
        git(
            env.repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'1' * 40},vendor/lib",
        )
        git(env.repo, "commit", "-q", "-m", "sub")
        rc, out, _ = env.run("create")
        assert rc == 2 and "submodule" in out

    def test_lfs_pointer_is_refused(self, env):
        (env.repo / "big.bin").write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:"
            + "a" * 64
            + "\nsize 9\n"
        )
        git(env.repo, "add", "big.bin")
        git(env.repo, "commit", "-q", "-m", "lfs")
        rc, out, _ = env.run("create")
        assert rc == 2 and "LFS" in out

    def test_large_and_binary_files_skip_the_secret_scan(self, env, monkeypatch):
        monkeypatch.setattr(csb, "SCAN_MAX_BLOB", 10)
        (env.repo / "blob.bin").write_bytes(b"\x00" + FAKE_AWS_KEY.encode())
        git(env.repo, "add", "blob.bin")
        git(env.repo, "commit", "-q", "-m", "b")
        rc, out, err = env.run("create", "--image", "generic")
        assert rc == 0, err
        assert "秘密スキャンを省いた" in out

    def test_binary_file_is_not_scanned(self, env):
        (env.repo / "blob.bin").write_bytes(b"\x00" + FAKE_AWS_KEY.encode())
        git(env.repo, "add", "blob.bin")
        git(env.repo, "commit", "-q", "-m", "b")
        assert env.run("create", "--image", "generic")[0] == 0

    def test_outside_a_repository_is_refused(self, env, monkeypatch, tmp_path):
        outside = tmp_path / "plain"
        outside.mkdir()
        monkeypatch.chdir(outside)
        rc, _, err = env.run("create")
        assert rc == 2 and "作業ツリー" in err

    def test_repository_without_commits_is_refused(self, env, monkeypatch, tmp_path):
        empty = make_repo(tmp_path / "empty", {"a": "x"}, commit=False)
        monkeypatch.chdir(empty)
        rc, _, err = env.run("create")
        assert rc == 2 and "HEAD" in err

    def test_missing_generic_image_is_refused(self, env):
        env.fake.images.clear()
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 2 and "csb image build --generic" in err
        self.assert_cleaned(env, IDS[0], generic=False)

    def test_image_with_volume_is_refused(self, env):
        env.fake.add_image("vol/img:1", volumes={"/data": {}})
        rc, _, err = env.run("create", "--image", "vol/img:1")
        assert rc == 2 and "VOLUME" in err
        self.assert_cleaned(env, IDS[0])

    def test_explicit_image_is_pulled_and_its_numeric_user_used(self, env):
        env.fake.pull_user = "1234:5678"
        sid = env.create("--image", "ghcr.io/org/img:2")
        meta = env.meta(sid)
        assert env.fake.commands("pull") == [["pull", "ghcr.io/org/img:2"]]
        assert meta["image"]["source"] == "explicit"
        assert (meta["uid"], meta["gid"]) == (1234, 5678)
        assert "1234:5678" in env.fake.commands("create")[1]

    @pytest.mark.parametrize(
        "user, expected",
        [
            ("", (1000, 1000)),
            ("root", (1000, 1000)),
            ("0", (1000, 1000)),
            ("42", (42, 42)),
            ("node", (1000, 1000)),
            ("7:0", (1000, 1000)),
        ],
    )
    def test_image_user(self, user, expected):
        assert csb._image_user({"Config": {"User": user}}) == expected
        assert csb._image_user({}) == (1000, 1000)

    def test_failed_pull_is_an_error(self, env):
        env.fake.fail["pull"] = _fail("denied")
        rc, _, err = env.run("create", "--image", "ghcr.io/org/img:2")
        assert rc == 1 and "docker pull" in err

    def test_devcontainer_image_and_post_create(self, env):
        (env.repo / ".devcontainer").mkdir()
        (env.repo / ".devcontainer" / "devcontainer.json").write_text(
            json.dumps(
                {
                    "image": "mcr.microsoft.com/devcontainers/base:1",
                    "postCreateCommand": "mkdir -p node_modules && touch node_modules/x",
                    "initializeCommand": "touch /tmp/host-pwned",
                }
            )
        )
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "dc")
        rc, out, err = env.run("create")
        assert rc == 0, err
        assert "initializeCommand はホストで実行される定義のため実行しない" in out
        sid = out.strip().splitlines()[-1].split()[-1]
        assert env.meta(sid)["image"]["source"] == "devcontainer"
        assert (env.work_root(sid) / "node_modules" / "x").exists()

    def test_devcontainer_dockerfile_is_built_from_the_snapshot(self, env):
        (env.repo / ".devcontainer").mkdir()
        (env.repo / ".devcontainer" / "Dockerfile").write_text("FROM debian\n")
        (env.repo / ".devcontainer" / "devcontainer.json").write_text(
            json.dumps({"build": {"dockerfile": "Dockerfile", "context": ".."}})
        )
        os.symlink("README.md", env.repo / "link.md")
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "dc")
        (env.repo / "untracked-secret.txt").write_text("host only\n")
        rc, out, err = env.run("create")
        assert rc == 0, err
        sid = out.strip().splitlines()[-1].split()[-1]
        meta = env.meta(sid)
        assert meta["image"]["source"] == "dockerfile"
        assert meta["image"]["ref"] == f"csb-base:{sid}"
        ((args, data),) = env.fake.build_inputs
        assert args[args.index("-f") + 1] == ".devcontainer/Dockerfile"
        assert f"{gate.LABEL_ID}={sid}" in args
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            names = tar.getnames()
            link = tar.getmember("link.md")
        assert "src/app.py" in names and ".devcontainer/Dockerfile" in names
        assert "untracked-secret.txt" not in names
        assert link.issym() and link.linkname == "README.md"
        assert not os.path.exists(
            os.path.join(gate.sandbox_dir(env.state, sid), "context.tar")
        )

    def test_dockerfile_in_a_subdir_context(self, env):
        (env.repo / ".devcontainer").mkdir()
        (env.repo / ".devcontainer" / "Dockerfile").write_text("FROM debian\n")
        (env.repo / ".devcontainer" / "devcontainer.json").write_text(
            json.dumps({"build": {"dockerfile": "Dockerfile"}})
        )
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "dc")
        assert env.run("create")[0] == 0
        ((args, data),) = env.fake.build_inputs
        assert args[args.index("-f") + 1] == "Dockerfile"
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            assert sorted(tar.getnames()) == ["Dockerfile", "devcontainer.json"]

    def test_dockerfile_outside_the_context_is_refused(self, env):
        (env.repo / "docker").mkdir()
        (env.repo / "docker" / "Dockerfile").write_text("FROM debian\n")
        (env.repo / ".devcontainer").mkdir()
        (env.repo / ".devcontainer" / "devcontainer.json").write_text(
            json.dumps(
                {"build": {"dockerfile": "../docker/Dockerfile", "context": "."}}
            )
        )
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "dc")
        rc, out, err = env.run("create")
        # 計画の段階で拒否する (要約に理由が出る)。汎用イメージには切り替えない。
        assert rc == 2 and "非対応の要素" in err
        assert "build context (.devcontainer) の外にある" in out
        assert not [a for _, a in env.fake.calls if a[:1] == ["build"]]

    def test_work_container_failing_the_check_is_cleaned_up(self, env):
        env.fake.inspect_hook = lambda info: info["HostConfig"].update(Privileged=True)
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 1 and "照合に通らない" in err and "Privileged" in err
        TestCreate.assert_cleaned(self, env, IDS[0])

    def test_cleanup_problems_are_reported(self, env):
        env.fake.fail["start"] = _fail("cannot start")
        env.fake.fail["network rm"] = _fail("in use")
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 1
        assert "片付けに失敗" in err

    def test_bad_container_id_from_create(self, env):
        env.fake.fail["create"] = _ok("not-an-id\n")
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 1 and "コンテナ ID" in err

    def test_bad_image_id_from_commit(self, env):
        env.fake.fail["commit"] = _ok("garbage\n")
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 1 and "イメージ ID" in err


# ---------------------------------------------------------------------------
# exec
# ---------------------------------------------------------------------------
class TestExec:
    def test_runs_with_numeric_user_and_full_id(self, env, sandbox):
        cid = env.meta(sandbox)["work_container_id"]
        rc, out, _ = env.run(
            "exec",
            sandbox,
            "-w",
            "/work/src",
            "-e",
            "A=1",
            "--",
            "sh",
            "-c",
            "echo hi; pwd; exit 3",
        )
        assert rc == 3
        assert out.splitlines()[0] == "hi"
        assert out.splitlines()[1].endswith("/work/src")
        assert env.fake.commands("exec")[-1] == [
            "exec",
            "-i",
            "-u",
            "1000:1000",
            "-w",
            "/work/src",
            "-e",
            "A=1",
            cid,
            "sh",
            "-c",
            "echo hi; pwd; exit 3",
        ]
        inspects = env.fake.commands("inspect")
        assert inspects[-1] == ["inspect", "--type", "container", cid]

    def test_tty_adds_t(self, env, sandbox):
        env.tty = True
        env.run("exec", sandbox, "--", "true")
        assert env.fake.commands("exec")[-1][:3] == ["exec", "-i", "-t"]

    def test_lock_is_released_once_the_process_started(self, env, sandbox):
        seen = []

        def hook(_c, argv):
            lock = csb._Lock(os.path.join(gate.sandbox_dir(env.state, sandbox), "lock"))
            seen.append(lock.acquire(blocking=False))
            lock.release()
            return _ok()

        env.fake.exec_hook = hook
        assert env.run("exec", sandbox, "--", "true")[0] == 0
        assert seen == [True]

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda i: i["HostConfig"].update(Privileged=True),
            lambda i: i["Config"]["Labels"].update({gate.LABEL_ROLE: "setup"}),
            lambda i: i["HostConfig"].update(Binds=["/:/host"]),
            lambda i: i["NetworkSettings"].update(Networks={"bridge": {}}),
            lambda i: i.update(Image="sha256:" + "e" * 64),
            lambda i: i["HostConfig"].update(PidsLimit=256),
        ],
    )
    def test_mismatch_is_refused_without_exec(self, env, sandbox, mutate):
        before = len(env.fake.commands("exec"))
        env.fake.inspect_hook = mutate
        rc, _, err = env.run("exec", sandbox, "--", "true")
        assert rc == 2
        assert "照合に通らない" in err
        assert len(env.fake.commands("exec")) == before

    def test_connected_network_without_window_is_refused(self, env, sandbox):
        env.fake.work(sandbox)["networks"].add(gate.network_name(sandbox))
        rc, _, err = env.run("exec", sandbox, "--", "true")
        assert rc == 2 and "Networks" in err

    def test_stopped_container_is_refused(self, env, sandbox):
        env.fake.work(sandbox)["running"] = False
        assert env.run("exec", sandbox, "--", "true")[0] == 2

    def test_missing_container_is_refused(self, env, sandbox):
        del env.fake.containers[env.meta(sandbox)["work_container_id"]]
        rc, _, err = env.run("exec", sandbox, "--", "true")
        assert rc == 2 and "inspect できない" in err

    def test_unknown_sandbox_is_refused(self, env):
        rc, _, err = env.run("exec", IDS[3], "--", "true")
        assert rc == 2 and "無い" in err

    def test_meta_with_loose_permissions_is_refused(self, env, sandbox):
        os.chmod(gate.meta_path(env.state, sandbox), 0o666)  # nosec B103
        rc, _, err = env.run("exec", sandbox, "--", "true")
        assert rc == 2 and "meta.json" in err

    def test_meta_of_another_sandbox_is_refused(self, env, sandbox):
        other = os.path.join(env.state, IDS[3])
        os.mkdir(other, 0o700)
        os.link(gate.meta_path(env.state, sandbox), os.path.join(other, "meta.json"))
        rc, _, err = env.run("exec", IDS[3], "--", "true")
        assert rc == 2 and "一致しない" in err

    def test_symlinked_lock_is_refused(self, env, sandbox):
        lock = os.path.join(gate.sandbox_dir(env.state, sandbox), "lock")
        os.unlink(lock)
        os.symlink(env.tmp / "elsewhere", lock)
        rc, _, err = env.run("exec", sandbox, "--", "true")
        assert rc == 2 and "ロック" in err

    def test_inspect_garbage_is_refused(self, env, sandbox):
        env.fake.fail["inspect"] = _ok("not json")
        assert env.run("exec", sandbox, "--", "true")[0] == 2
        env.fake.fail["inspect"] = _ok("{}")
        assert env.run("exec", sandbox, "--", "true")[0] == 2
        env.fake.fail["inspect"] = _ok("[]")
        assert env.run("exec", sandbox, "--", "true")[0] == 2


# ---------------------------------------------------------------------------
# net on / off and expiry
# ---------------------------------------------------------------------------
class TestNet:
    def test_window_connects_then_expires(self, env, sandbox):
        net = gate.network_name(sandbox)
        rc, out, err = env.run("net", "on", sandbox, "--minutes", "5")
        assert rc == 0, err
        assert "5 分間" in out
        assert env.fake.work(sandbox)["networks"] == {net}
        assert env.meta(sandbox)["net_on_until"] == T0 + 300
        assert env.run("exec", sandbox, "--", "true")[0] == 0
        env.clock.advance(301)
        rc, _, err = env.run("ls")
        assert rc == 0
        assert "期限が切れたため切断した" in err
        assert env.fake.work(sandbox)["networks"] == set()
        assert "net_on_until" not in env.meta(sandbox)

    def test_default_minutes_and_reconnect_extends(self, env, sandbox):
        assert env.run("net", "on", sandbox)[0] == 0
        assert env.meta(sandbox)["net_on_until"] == T0 + gate.NET_MINUTES_DEFAULT * 60
        env.clock.advance(60)
        assert env.run("net", "on", sandbox, "--minutes", "2")[0] == 0
        assert env.meta(sandbox)["net_on_until"] == T0 + 60 + 120
        assert len(env.fake.commands("network", "connect")) == 1

    def test_expired_window_with_connection_refuses_exec_if_disconnect_fails(
        self, env, sandbox
    ):
        env.run("net", "on", sandbox, "--minutes", "1")
        env.clock.advance(61)
        env.fake.fail["network disconnect"] = _fail("daemon busy")
        rc, _, err = env.run("exec", sandbox, "--", "true")
        assert rc == 2
        assert "切断に失敗" in err and "Networks" in err

    def test_net_off(self, env, sandbox):
        env.run("net", "on", sandbox)
        rc, out, _ = env.run("net", "off", sandbox)
        assert rc == 0 and "切断した" in out
        assert env.fake.work(sandbox)["networks"] == set()
        assert "net_on_until" not in env.meta(sandbox)
        assert env.run("net", "off", sandbox)[0] == 0

    def test_net_on_refused_when_container_mismatches(self, env, sandbox):
        env.fake.inspect_hook = lambda i: i["HostConfig"].update(CapAdd=["NET_ADMIN"])
        assert env.run("net", "on", sandbox)[0] == 2
        assert env.fake.commands("network", "connect") == []

    def test_connect_is_undone_when_meta_cannot_be_written(
        self, env, sandbox, monkeypatch
    ):
        def broken(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(csb, "_write_meta", broken)
        rc, _, err = env.run("net", "on", sandbox)
        # トレースバックではなく、終了コードの表どおりの exit 1 と `csb: エラー:`。
        assert rc == 1 and err.startswith("csb: エラー: OSError: disk full")
        assert env.fake.work(sandbox)["networks"] == set()

    def test_expiry_skips_busy_and_broken_sandboxes(self, env, sandbox):
        env.run("net", "on", sandbox, "--minutes", "1")
        env.clock.advance(61)
        lock = csb._Lock(os.path.join(gate.sandbox_dir(env.state, sandbox), "lock"))
        assert lock.acquire()
        try:
            csb.expire_windows()
        finally:
            lock.release()
        assert "net_on_until" in env.meta(sandbox)
        os.mkdir(os.path.join(env.state, IDS[3]), 0o700)
        csb.expire_windows()
        assert "net_on_until" not in env.meta(sandbox)

    def test_expiry_skips_symlinked_lock(self, env, sandbox):
        env.run("net", "on", sandbox, "--minutes", "1")
        env.clock.advance(61)
        lock = os.path.join(gate.sandbox_dir(env.state, sandbox), "lock")
        os.unlink(lock)
        os.symlink(env.tmp / "elsewhere", lock)
        csb.expire_windows()
        assert "net_on_until" in env.meta(sandbox)


class TestNetDisplay:
    """ls / status show the work container's actual networks (inspect), not the
    recorded window. Expiry runs at the start of the next csb call, so a window
    can be past its end and still connected (the lock was busy, the disconnect
    failed); that is shown as such."""

    def _rows(self, env):
        return {r["id"]: r for r in json.loads(env.run("ls", "--json")[1])}

    def test_connected_within_the_window(self, env, sandbox):
        env.run("net", "on", sandbox, "--minutes", "5")
        row = self._rows(env)[sandbox]
        assert row["net_connected"] is True and row["net_expired"] is False
        assert row["net_on_until"] == T0 + 300
        assert "net on (" in env.run("ls")[1]

    def test_past_the_window_and_still_connected(self, env, sandbox):
        env.run("net", "on", sandbox, "--minutes", "1")
        env.clock.advance(61)
        env.fake.fail["network disconnect"] = _fail("daemon busy")
        out = env.run("ls")[1]
        assert "net on (期限切れ。次の csb の呼び出しで切断)" in out
        row = self._rows(env)[sandbox]
        assert row["net_connected"] is True and row["net_expired"] is True
        assert row["net_on_until"] is None  # the window field keeps its meaning
        assert (
            "ネットワーク: on (期限切れ。次の csb の呼び出しで切断)"
            in env.run("status", sandbox)[1]
        )

    def test_disconnected_outside_csb_is_off(self, env, sandbox):
        env.run("net", "on", sandbox, "--minutes", "5")
        env.fake.work(sandbox)["networks"].clear()
        row = self._rows(env)[sandbox]
        assert row["net_connected"] is False
        assert "net off" in env.run("ls")[1]

    @pytest.mark.parametrize(
        "change",
        [
            # attached to another network as well (docker network connect bridge)
            lambda i: i["NetworkSettings"]["Networks"].update(bridge={}),
            # only another network
            lambda i: i["NetworkSettings"].update(Networks={"bridge": {}}),
            # sharing another container's network, or the host's
            lambda i: i["HostConfig"].update(NetworkMode="container:other"),
            lambda i: i["HostConfig"].update(NetworkMode="host"),
            # any other check failure
            lambda i: i["HostConfig"].update(CapAdd=["NET_ADMIN"]),
        ],
        ids=["sandbox-and-bridge", "bridge-only", "container-mode", "host-mode", "cap"],
    )
    def test_a_container_failing_its_check_is_never_off(self, env, sandbox, change):
        # what we cannot vouch for is "unknown", never "off"
        env.fake.inspect_hook = change
        row = self._rows(env)[sandbox]
        assert row["check"] != "ok"
        assert row["net_connected"] is None
        out = env.run("ls")[1]
        assert "net 不明 (照合に通らない)" in out and "net off" not in out
        assert "ネットワーク: 不明 (照合に通らない)" in env.run("status", sandbox)[1]

    def test_unreachable_docker_is_unknown(self, env, sandbox):
        env.fake.fail["inspect --type"] = _fail("no daemon")
        row = self._rows(env)[sandbox]
        assert row["net_connected"] is None
        assert "net 不明" in env.run("ls")[1]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
class TestExport:
    def test_patch_covers_edits_new_deleted_binary_and_mode(self, env, sandbox):
        change_work(env, sandbox)
        rc, out, err = env.run("export", sandbox)
        assert rc == 0, err
        sha = out.splitlines()[0]
        path = gate.patch_path(env.state, sandbox, sha)
        data = Path(path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        summary = gate.analyze_patch(data)
        assert summary.errors == () and summary.invalid_paths == ()
        status = {f.path: f.status for f in summary.files}
        assert status == {
            "README.md": "modified",
            "new.txt": "added",
            "old.txt": "deleted",
            "bin.dat": "added",
            "src/app.py": "modified",
        }
        assert summary.binary_files == ("bin.dat",)
        assert summary.executable_files == ("src/app.py",)
        assert "apply に必要なレビュー" in out
        assert f"csb verify {sandbox} --patch {sha}" in out
        # the container's real index is untouched (a throwaway index was used)
        work = env.work_root(sandbox)
        assert b"new.txt" not in git(work, "diff", "--cached", "--name-only")

    def test_container_git_config_cannot_change_the_patch_shape(self, env, sandbox):
        work = env.work_root(sandbox)
        with open(work / ".git" / "config", "a") as fh:
            fh.write(
                "[diff]\n\tnoprefix = true\n\texternal = false\n\tmnemonicPrefix = true\n"
            )
            fh.write("[core]\n\tquotePath = false\n")
        (work / "README.md").write_text("changed\n")
        (work / "naïve.txt").write_text("x\n")
        sha = env.export(sandbox)
        data = Path(gate.patch_path(env.state, sandbox, sha)).read_bytes()
        assert b"diff --git a/README.md b/README.md" in data
        assert b'"a/na\\303\\257ve.txt"' in data
        assert gate.analyze_patch(data).errors == ()

    def test_no_changes_writes_nothing(self, env, sandbox):
        rc, out, _ = env.run("export", sandbox)
        assert rc == 0 and "変更なし" in out
        assert not os.path.exists(
            os.path.dirname(gate.patch_path(env.state, sandbox, "0" * 64))
        )

    def test_oversized_patch_is_refused(self, env, sandbox, monkeypatch):
        monkeypatch.setattr(csb, "PATCH_MAX_BYTES", 10)
        (env.work_root(sandbox) / "README.md").write_text("a much longer readme\n")
        rc, _, err = env.run("export", sandbox)
        assert rc == 2 and "MiB" in err

    def test_export_refused_on_mismatch(self, env, sandbox):
        env.fake.inspect_hook = lambda i: i["HostConfig"].update(Privileged=True)
        assert env.run("export", sandbox)[0] == 2

    def test_container_git_failure_is_an_error(self, env, sandbox):
        env.fake.exec_hook = lambda _c, argv: _fail("fatal: not a git repository", 128)
        rc, _, err = env.run("export", sandbox)
        assert rc == 1 and "exit 128" in err


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
class TestVerify:
    def test_pass_and_fail_are_recorded_against_the_sha(self, env):
        sid = env.create(
            "--image", "generic", "--setup", "mkdir -p build && touch build/dep"
        )
        change_work(env, sid)
        sha = env.export(sid)
        rc, out, err = env.run(
            "verify",
            sid,
            "--patch",
            sha,
            "--",
            "sh",
            "-c",
            "test -f new.txt && test ! -e old.txt && test -f build/dep && grep -q world README.md && echo tested",
        )
        assert rc == 0, err
        assert "tested" in out and "verify PASS" in err
        record = json.loads(
            Path(gate.verify_record_path(env.state, sid, sha)).read_text()
        )
        assert gate.verify_status(record, sha, env.meta(sid)) == "PASS"
        assert record["argv"][:2] == ["sh", "-c"]
        assert (
            stat.S_IMODE(os.stat(gate.verify_record_path(env.state, sid, sha)).st_mode)
            == 0o600
        )
        # fresh, network-less container from the setup image, removed afterwards
        create = env.fake.commands("create")[-1]
        meta = env.meta(sid)
        assert create == gate.verify_create_args(
            sid, meta["setup_image_id"], 1000, 1000
        )
        assert env.fake.find_container(gate.container_name(sid, "verify")) is None
        # the work container was not touched
        assert (env.work_root(sid) / "new.txt").exists()

        rc, _, err = env.run("verify", sid, "--patch", sha, "--", "false")
        assert rc == 1 and "verify FAIL" in err
        record = json.loads(
            Path(gate.verify_record_path(env.state, sid, sha)).read_text()
        )
        assert record["exit_code"] == 1

    def test_unknown_patch_is_refused(self, env, sandbox):
        rc, _, err = env.run("verify", sandbox, "--patch", "a" * 64, "--", "true")
        assert rc == 2 and "読めない" in err

    def test_tampered_patch_is_refused(self, env, sandbox):
        (env.work_root(sandbox) / "README.md").write_text("x\n")
        sha = env.export(sandbox)
        path = gate.patch_path(env.state, sandbox, sha)
        Path(path).write_bytes(Path(path).read_bytes() + b" ")
        rc, _, err = env.run("verify", sandbox, "--patch", sha, "--", "true")
        assert rc == 2 and "一致しない" in err

    def test_patch_that_does_not_apply_writes_no_record(self, env, sandbox):
        bogus = (
            b"diff --git a/README.md b/README.md\n"
            b"index " + b"1" * 40 + b".." + b"2" * 40 + b" 100644\n"
            b"--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-nope\n+yes\n"
        )
        sha = hashlib.sha256(bogus).hexdigest()
        csb._write_private(gate.patch_path(env.state, sandbox, sha), bogus)
        rc, _, err = env.run("verify", sandbox, "--patch", sha, "--", "true")
        assert rc == 2 and "適用できない" in err
        assert not os.path.exists(gate.verify_record_path(env.state, sandbox, sha))
        assert env.fake.find_container(gate.container_name(sandbox, "verify")) is None

    def test_stale_verify_container_is_replaced(self, env, sandbox):
        (env.work_root(sandbox) / "README.md").write_text("x\n")
        sha = env.export(sandbox)
        env.fake.add_container(
            gate.container_name(sandbox, "verify"),
            {gate.LABEL_ID: sandbox},
            running=False,
        )
        assert env.run("verify", sandbox, "--patch", sha, "--", "true")[0] == 0

    def test_concurrent_verify_is_refused(self, env, sandbox):
        (env.work_root(sandbox) / "README.md").write_text("x\n")
        sha = env.export(sandbox)
        lock = csb._Lock(
            os.path.join(gate.sandbox_dir(env.state, sandbox), "verify.lock")
        )
        assert lock.acquire()
        try:
            rc, _, err = env.run("verify", sandbox, "--patch", sha, "--", "true")
        finally:
            lock.release()
        assert rc == 2 and "実行中" in err


# ---------------------------------------------------------------------------
# attest, preflight and apply
# ---------------------------------------------------------------------------
@pytest.fixture
def verified(env, sandbox):
    change_work(env, sandbox)
    sha = env.export(sandbox)
    rc, _, err = env.run("verify", sandbox, "--patch", sha, "--", "true")
    assert rc == 0, err
    return sandbox, sha


class TestAttest:
    def test_record_with_ref(self, env, verified):
        sid, sha = verified
        review = env.tmp / "review.md"
        review.write_text("LGTM\n")
        rc, out, err = env.run(
            "attest",
            sid,
            "--patch",
            sha,
            "--review",
            "codex=fail",
            "--ref",
            str(review),
            "--unresolved",
            "2",
        )
        assert rc == 0, err
        assert "codex=fail" in out
        path = gate.attest_record_path(env.state, sid, sha, "codex")
        record = json.loads(Path(path).read_text())
        assert record["ref"] == {
            "path": str(review),
            "sha256": hashlib.sha256(b"LGTM\n").hexdigest(),
        }
        assert record["unresolved"] == 2 and record["result"] == "fail"
        assert gate.attest_record_valid(record, sha)

    def test_unknown_patch_or_unreadable_ref_is_refused(self, env, verified):
        sid, sha = verified
        assert (
            env.run("attest", sid, "--patch", "b" * 64, "--review", "codex=pass")[0]
            == 2
        )
        rc, _, err = env.run(
            "attest",
            sid,
            "--patch",
            sha,
            "--review",
            "codex=pass",
            "--ref",
            str(env.tmp / "absent"),
        )
        assert rc == 2 and "--ref" in err


class TestApply:
    def test_preflight_lists_blockers_then_apply_succeeds(self, env, verified):
        sid, sha = verified
        rc, out, _ = env.run("preflight", sid, "--patch", sha)
        assert rc == 1
        assert "verify: PASS" in out
        assert "code-reviewer のレビューが 未実施" in out
        env.attest_all(sid, sha)
        rc, out, _ = env.run("preflight", sid, "--patch", sha)
        assert rc == 0 and "apply を拒否する理由: なし" in out
        before = (env.repo / "README.md").read_text()
        rc, out, err = env.run("apply", sid, "--patch", sha)
        assert rc == 0, err
        assert "適用した: 5 ファイル" in out
        assert before != (env.repo / "README.md").read_text() == "hello\nworld\n"
        assert (env.repo / "new.txt").read_text() == "brand new\n"
        assert not (env.repo / "old.txt").exists()
        assert (env.repo / "bin.dat").read_bytes() == b"\x00\x01\x02binary"
        assert os.stat(env.repo / "src" / "app.py").st_mode & 0o100
        assert not (env.repo / "build").exists()
        # nothing was staged and a second apply is refused (the host base moved)
        assert git(env.repo, "diff", "--cached", "--name-only") == b""
        rc, out, _ = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "base tree が作成時と一致しない" in out

    def test_missing_reviews_block_and_leave_the_repo_alone(self, env, verified):
        sid, sha = verified
        rc, out, err = env.run("apply", sid, "--patch", sha)
        assert rc == 2
        assert "apply の条件を満たしていない" in err
        assert (env.repo / "README.md").read_text() == "hello\n"

    def test_risky_patch_needs_security_review(self, env, sandbox):
        work = env.work_root(sandbox)
        (work / ".github" / "workflows").mkdir(parents=True)
        (work / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
        sha = env.export(sandbox)
        env.run("verify", sandbox, "--patch", sha, "--", "true")
        env.attest_all(sandbox, sha)
        rc, out, _ = env.run("apply", sandbox, "--patch", sha)
        assert rc == 2 and "security-reviewer のレビューが 未実施" in out
        env.attest_all(sandbox, sha, ["security-reviewer"])
        assert env.run("apply", sandbox, "--patch", sha)[0] == 0

    def test_host_change_after_create_blocks(self, env, verified):
        sid, sha = verified
        env.attest_all(sid, sha)
        (env.repo / "src" / "app.py").write_text("host edit\n")
        rc, out, _ = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "base tree" in out
        assert (env.repo / "src" / "app.py").read_text() == "host edit\n"

    def test_failed_verify_blocks(self, env, verified):
        sid, sha = verified
        env.attest_all(sid, sha)
        env.run("verify", sid, "--patch", sha, "--", "false")
        rc, out, _ = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "verify が FAIL" in out

    def test_changed_ref_invalidates_the_attest(self, env, verified):
        sid, sha = verified
        env.attest_all(sid, sha, ["codex", "advisor"])
        review = env.tmp / "review.md"
        review.write_text("ok\n")
        env.run(
            "attest",
            sid,
            "--patch",
            sha,
            "--review",
            "code-reviewer=pass",
            "--ref",
            str(review),
        )
        assert env.run("preflight", sid, "--patch", sha)[0] == 0
        review.write_text("rewritten after the fact\n")
        rc, out, _ = env.run("apply", sid, "--patch", sha)
        assert rc == 2
        assert "中身が記録時と変わったため失効" in out
        assert "code-reviewer のレビューが 未実施" in out

    def test_malformed_attest_is_ignored(self, env, verified):
        sid, sha = verified
        env.attest_all(sid, sha)
        path = gate.attest_record_path(env.state, sid, sha, "advisor")
        record = json.loads(Path(path).read_text())
        record["reviewer"] = "codex"
        csb._write_json(path, record)
        Path(os.path.dirname(path), "NOTES.txt").write_text("x")
        rc, out, _ = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "attest advisor: 記録が不正" in out

    def test_tty_prompt(self, env, verified, monkeypatch):
        sid, sha = verified
        env.attest_all(sid, sha)
        env.tty = True
        monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
        rc, _, err = env.run("apply", sid, "--patch", sha)
        # one line on stderr, as the exit-code table promises for exit 1 (a real
        # terminal echoes the answer and its newline after the prompt)
        assert rc == 1
        assert err.endswith("[y/N]: csb: 中止: 確認で断ったため適用していない\n")
        assert (env.repo / "README.md").read_text() == "hello\n"
        monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
        assert env.run("apply", sid, "--patch", sha)[0] == 0
        assert (env.repo / "README.md").read_text() == "hello\nworld\n"

    def test_git_apply_check_failure_is_refused(self, env, verified, monkeypatch):
        sid, sha = verified
        env.attest_all(sid, sha)
        real = csb._git

        def fake_git(repo, args, **kw):
            if "--check" in args:
                return subprocess.CompletedProcess(args, 1, b"", b"error: patch failed")
            return real(repo, args, **kw)

        monkeypatch.setattr(csb, "_git", fake_git)
        rc, _, err = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "git apply --check" in err

    def test_missing_repo_root_blocks(self, env, verified):
        sid, sha = verified
        env.attest_all(sid, sha)
        os.rename(env.repo, env.tmp / "moved")
        rc, out, _ = env.run("preflight", sid, "--patch", sha)
        assert rc == 1 and "ホストのリポジトリを確かめられない" in out

    def test_repo_root_that_became_a_subdir_blocks(self, env, verified, monkeypatch):
        sid, sha = verified
        meta = env.meta(sid)
        sub = env.repo / "src"
        meta["repo_root"] = str(sub)
        csb._write_meta(env.state, meta)
        rc, out, _ = env.run("preflight", sid, "--patch", sha)
        assert rc == 1 and "最上位ではない" in out


# ---------------------------------------------------------------------------
# The bash-review hook's preview (child -> JSON data; nothing is written)
# ---------------------------------------------------------------------------
def _cmd(*argv):
    cmd = gate.parse_csb_argv(list(argv))
    assert cmd is not None
    return cmd


def _preview(request):
    out = io.StringIO()
    rc = csb.preview_main(io.StringIO(json.dumps(request)), out)
    return rc, out.getvalue()


def _index_bytes(repo):
    return (Path(repo) / ".git" / "index").read_bytes()


def _plant_markers(env):
    """Repository settings that would run host commands, each leaving a marker."""
    marks = env.tmp / "marks"
    marks.mkdir()
    hooks = env.repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("post-index-change", "reference-transaction", "post-checkout"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch {marks}/{name}\n")
        hook.chmod(0o755)
    monitor = env.tmp / "fsmonitor.sh"
    monitor.write_text(f"#!/bin/sh\ntouch {marks}/fsmonitor\nexit 1\n")
    monitor.chmod(0o755)
    git(env.repo, "config", "core.fsmonitor", str(monitor))
    git(env.repo, "config", "filter.evil.clean", f"sh -c 'touch {marks}/filter; cat'")
    git(env.repo, "config", "filter.evil.smudge", f"sh -c 'touch {marks}/filter; cat'")
    return marks


class TestPreviewData:
    def test_create_data_is_the_plan_create_uses(self, env):
        data = csb.preview_data(
            _cmd("create", "--setup", "make"), str(env.repo), env.state
        )
        assert data["kind"] == "create" and data["repo_root"] == str(env.repo)
        assert data["head"] == git(env.repo, "rev-parse", "HEAD").decode().strip()
        assert data["setup"]["steps"] == [["--setup", ["/bin/sh", "-c", "make"]]]
        assert data["image"]["source"] == "generic" and data["blockers"] == []
        text, refusal = gate.render_preview(data, "csb create")
        assert refusal == "" and "make" in text

    def test_create_preview_reads_the_working_tree_and_writes_nothing(self, env):
        dc = env.repo / ".devcontainer" / "devcontainer.json"
        dc.parent.mkdir()
        dc.write_text('{"image": "node:20"}')
        git(env.repo, "add", ".devcontainer")
        git(env.repo, "commit", "-q", "-m", "dc")
        dc.write_text('{"image": "node:22", "postCreateCommand": "npm ci"}')  # dirty
        marks = _plant_markers(env)
        before = (_index_bytes(env.repo), git(env.repo, "for-each-ref"))
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert data["image"]["ref"] == "node:22"
        assert data["setup"]["steps"] == [
            ["postCreateCommand", ["/bin/sh", "-c", "npm ci"]]
        ]
        assert (_index_bytes(env.repo), git(env.repo, "for-each-ref")) == before
        assert list(marks.iterdir()) == []
        assert env.fake.calls == [] and not os.path.exists(env.state)

    def test_an_untracked_plan_file_is_a_blocker(self, env):
        dc = env.repo / ".devcontainer" / "devcontainer.json"
        dc.parent.mkdir()
        dc.write_text('{"image": "node:20", "postCreateCommand": "curl x | sh"}')
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert any("追跡されていない" in b for b in data["blockers"])
        assert data["image"]["source"] == "generic"  # what the snapshot would use

    def test_submodules_partial_clones_and_empty_repositories_are_blockers(
        self, env, tmp_path
    ):
        git(
            env.repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'1' * 40},vendor/lib",
        )
        git(env.repo, "config", "extensions.partialClone", "origin")
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        joined = "\n".join(data["blockers"])
        assert "submodule" in joined and "partial clone" in joined
        empty = make_repo(tmp_path / "empty", {"a": "x"}, commit=False)
        data = csb.preview_data(_cmd("create"), str(empty), env.state)
        assert data["head"] == "" and any("HEAD" in b for b in data["blockers"])

    def test_a_symlinked_directory_is_not_followed(self, env, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "devcontainer.json").write_text('{"image": "evil:1"}')
        dc = env.repo / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text('{"image": "node:20"}')
        git(env.repo, "add", ".devcontainer")
        git(env.repo, "commit", "-q", "-m", "dc")
        (dc / "devcontainer.json").unlink()
        dc.rmdir()
        dc.symlink_to(outside)
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert data["image"]["ref"] != "evil:1"

    def test_apply_data_takes_no_lock_and_does_not_check_the_host(self, env, verified):
        sid, sha = verified
        marks = _plant_markers(env)
        before = _index_bytes(env.repo)
        lock = csb._lock(env.state, sid)
        assert lock.acquire(blocking=False)
        try:
            data = csb.preview_data(_cmd("apply", sid, "--patch", sha), "/", env.state)
        finally:
            lock.release()
        assert data["kind"] == "apply" and data["sandbox"] == sid
        assert data["head"] == "same" and data["unchecked"]
        assert data["verify"]["status"] == "PASS"
        assert _index_bytes(env.repo) == before and list(marks.iterdir()) == []

    def test_apply_data_for_a_harness_patch_carries_the_manual_steps(self, env):
        sid = env.create("--image", "generic")
        (env.work_root(sid) / ".claude").mkdir()
        (env.work_root(sid) / ".claude" / "settings.json").write_text("{}\n")
        sha = env.export(sid)
        data = csb.preview_data(_cmd("apply", sid, "--patch", sha), "/", env.state)
        assert any("ハーネス" in b for b in data["blockers"])
        patch_file = gate.patch_path(env.state, sid, sha)
        assert data["manual_steps"] == gate.harness_manual_steps(
            str(env.repo), patch_file, sha
        )

    def test_head_moved(self, env, verified):
        sid, sha = verified
        (env.repo / "new.md").write_text("x\n")
        git(env.repo, "add", "new.md")
        git(env.repo, "commit", "-q", "-m", "moved")
        data = csb.preview_data(_cmd("apply", sid, "--patch", sha), "/", env.state)
        assert data["head"] == "moved"

    def test_preview_main_writes_json(self, env, verified):
        sid, sha = verified
        for argv, cwd in (
            (["create"], str(env.repo)),
            (["apply", sid, "--patch", sha], "/"),
        ):
            rc, out = _preview({"argv": argv, "cwd": cwd, "state": env.state})
            assert rc == 0
            text, _ = gate.render_preview(json.loads(out), "x")
            assert text

    @pytest.mark.parametrize(
        "request_",
        [
            {"argv": ["apply", IDS[3], "--patch", "c" * 64], "cwd": "/"},
            {"argv": ["create"], "cwd": "PLAIN"},
        ],
    )
    def test_preview_main_refusals_exit_2(self, env, tmp_path, request_):
        plain = tmp_path / "plain"
        plain.mkdir()
        request_ = {**request_, "state": env.state}
        if request_["cwd"] == "PLAIN":
            request_["cwd"] = str(plain)
        assert _preview(request_) == (2, "")

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "[]",
            json.dumps({"argv": ["ls"], "cwd": "/", "state": "/s"}),
            json.dumps({"argv": ["create", "--bogus"], "cwd": "/", "state": "/s"}),
            json.dumps({"argv": "create", "cwd": "/", "state": "/s"}),
            json.dumps({"argv": ["create"], "cwd": "relative", "state": "/s"}),
            json.dumps({"argv": ["create"], "cwd": "/"}),
            json.dumps({"argv": ["create", 1], "cwd": "/", "state": "/s"}),
        ],
    )
    def test_preview_main_bad_requests_exit_1(self, raw):
        out = io.StringIO()
        assert csb.preview_main(io.StringIO(raw), out) == 1
        assert out.getvalue() == ""

    @pytest.mark.parametrize(
        "error",
        [
            csb.SandboxError("git broke"),
            OSError("gone"),
            subprocess.TimeoutExpired(["git"], 1),
        ],
    )
    def test_preview_main_runtime_errors_exit_1(self, env, monkeypatch, error):
        def boom(*args, **kwargs):
            raise error

        monkeypatch.setattr(csb, "_index_listing", boom)
        rc, out = _preview(
            {"argv": ["create"], "cwd": str(env.repo), "state": env.state}
        )
        assert (rc, out) == (1, "")

    def test_preview_main_reads_the_process_streams(self, env, monkeypatch, capsys):
        request = {"argv": ["create"], "cwd": str(env.repo), "state": env.state}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
        assert csb.preview_main() == 0
        assert json.loads(capsys.readouterr().out)["kind"] == "create"


# ---------------------------------------------------------------------------
# Host git: repository settings never run a host command; no filters, no stash
# ---------------------------------------------------------------------------
class TestHostGit:
    def test_every_host_git_call_pins_the_settings(self, env, monkeypatch):
        seen = []
        real = subprocess.run

        def spy(argv, **kwargs):
            # host calls only: FakeDocker runs the container's git through the
            # same subprocess module, on the fake container's paths
            if argv[:1] == [csb.HOST_GIT_BIN] and str(env.repo) in argv:
                seen.append((list(argv), dict(kwargs.get("env") or {})))
            return real(argv, **kwargs)

        monkeypatch.setattr(csb.subprocess, "run", spy)
        env.create("--image", "generic")
        assert seen
        for argv, environ in seen:
            assert argv[1:7] == [
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "protocol.allow=never",
            ], argv
            assert environ.get("GIT_OPTIONAL_LOCKS") == "0"
            assert "stash" not in argv

    def test_the_base_tree_costs_a_fixed_number_of_git_calls(self, env, monkeypatch):
        # One git process per tracked file would make create and apply take minutes
        # on a large repository (GIT_TIMEOUT caps each call, not the total).
        for i in range(30):
            (env.repo / f"many{i}.txt").write_text(f"{i}\n")
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "many")
        (env.repo / "many0.txt").write_text("dirty\n")
        calls = []
        real = subprocess.run

        def spy(argv, **kwargs):
            if argv[:1] == [csb.HOST_GIT_BIN] and str(env.repo) in argv:
                calls.append(argv[argv.index(str(env.repo)) + 1 :])
            return real(argv, **kwargs)

        monkeypatch.setattr(csb.subprocess, "run", spy)
        csb.host_base_tree(str(env.repo))
        assert sum("--show-object-format" in c for c in calls) == 1
        # the working tree and the index (--cached) each give attributes
        assert sum("check-attr" in c for c in calls) == 2
        # ls-files, check-attr x2, config, object format, one hash-object (the
        # dirty file), update-index, write-tree
        assert len(calls) <= 8, calls

    def test_create_runs_no_repository_command(self, env):
        marks = _plant_markers(env)
        (env.repo / "old.txt").write_text("dirty raw bytes\n")
        sid = env.create("--image", "generic")
        assert list(marks.iterdir()) == []
        assert (env.work_root(sid) / "old.txt").read_text() == "dirty raw bytes\n"

    def test_a_filtered_repository_is_refused_and_no_filter_runs(self, env):
        # Any filter attribute on a tracked path refuses create: the snapshot takes
        # working-tree bytes with no filter, so git-crypt would put plaintext in.
        (env.repo / ".gitattributes").write_text("*.txt filter=evil\n")
        git(env.repo, "add", ".gitattributes")
        git(env.repo, "commit", "-q", "-m", "attrs")
        marks = _plant_markers(env)
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 2 and "filter 属性のあるリポジトリは非対応" in err
        assert list(marks.iterdir()) == []

    def test_the_base_tree_of_a_clean_repository_is_head(self, env):
        head = git(env.repo, "rev-parse", "HEAD^{tree}").decode().strip()
        before = _index_bytes(env.repo)
        assert csb.host_base_tree(str(env.repo)) == head
        assert _index_bytes(env.repo) == before

    def test_the_base_tree_takes_the_working_tree_contents(self, env, tmp_path):
        (env.repo / "README.md").write_text("dirty\n")
        (env.repo / "old.txt").unlink()
        os.chmod(env.repo / "src" / "app.py", 0o755)  # nosec B103
        (env.repo / "staged.txt").write_text("staged\n")
        git(env.repo, "add", "staged.txt")
        tree = csb.host_base_tree(str(env.repo))
        listing = git(env.repo, "ls-tree", "-r", tree).decode()
        assert "old.txt" not in listing and "staged.txt" in listing
        assert "100755 blob" in [ln for ln in listing.splitlines() if "app.py" in ln][0]
        blob = [ln for ln in listing.splitlines() if "README.md" in ln][0].split()[2]
        assert git(env.repo, "cat-file", "-p", blob) == b"dirty\n"

    def test_skip_worktree_entries_are_kept(self, env):
        git(env.repo, "update-index", "--skip-worktree", "README.md")
        (env.repo / "README.md").unlink()
        tree = csb.host_base_tree(str(env.repo))
        assert (
            "README.md" in git(env.repo, "ls-tree", "-r", "--name-only", tree).decode()
        )

    def test_a_symlinked_leading_directory_is_not_read(self, env, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "app.py").write_text("outside\n")
        shutil.rmtree(env.repo / "src")
        (env.repo / "src").symlink_to(outside)
        tree = csb.host_base_tree(str(env.repo))
        assert (
            "src/app.py"
            not in git(env.repo, "ls-tree", "-r", "--name-only", tree).decode()
        )

    def test_unmerged_entries_are_refused(self, env):
        blob = (
            git(env.repo, "hash-object", "-w", "--stdin", input=b"x\n").decode().strip()
        )
        git(
            env.repo,
            "update-index",
            "--index-info",
            input=f"100644 {blob} 1\tconflict.txt\n".encode(),
        )
        with pytest.raises(csb.SandboxRefused, match="マージ"):
            csb.host_base_tree(str(env.repo))

    def test_file_mode_false_keeps_the_index_mode(self, env):
        git(env.repo, "config", "core.fileMode", "false")
        os.chmod(env.repo / "src" / "app.py", 0o755)  # nosec B103
        tree = csb.host_base_tree(str(env.repo))
        assert tree == git(env.repo, "rev-parse", "HEAD^{tree}").decode().strip()

    def test_partial_clones_are_refused(self, env):
        git(env.repo, "config", "remote.origin.promisor", "true")
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 2 and "partial clone" in err

    def test_an_untracked_or_ignored_plan_file_refuses_create(self, env):
        (env.repo / ".gitignore").write_text(
            "build/\nnode_modules/\npackage-lock.json\n"
        )
        (env.repo / "package-lock.json").write_text("{}")
        rc, out, _ = env.run("create")
        assert rc == 2 and "package-lock.json" in out and "追跡されていない" in out
        assert env.fake.calls == []

    def test_preflight_writes_nothing(self, env, verified):
        sid, sha = verified
        marks = _plant_markers(env)
        before = _index_bytes(env.repo)
        rc, out, _ = env.run("preflight", sid, "--patch", sha)
        assert rc == 1 and "未確認" in out
        assert _index_bytes(env.repo) == before and list(marks.iterdir()) == []


class TestWorkingTreeReads:
    def _commit_devcontainer(self, env):
        dc = env.repo / ".devcontainer" / "devcontainer.json"
        dc.parent.mkdir()
        dc.write_text('{"image": "node:20"}')
        git(env.repo, "add", ".devcontainer")
        git(env.repo, "commit", "-q", "-m", "dc")
        return dc

    def test_a_fifo_in_place_of_a_tracked_file_does_not_block(self, env):
        dc = self._commit_devcontainer(env)
        dc.unlink()
        os.mkfifo(dc)
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert data["image"]["source"] == "generic"
        # git cannot index a FIFO either: the snapshot refuses rather than dropping it
        with pytest.raises(csb.SandboxRefused, match="通常ファイル"):
            csb.host_base_tree(str(env.repo))

    def test_an_oversized_plan_file_reads_as_empty(self, env, monkeypatch):
        self._commit_devcontainer(env)
        monkeypatch.setattr(csb, "PLAN_READ_MAX", 4)
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert data["image"]["ref"] != "node:20"

    def test_a_file_replaced_by_a_directory_is_dropped(self, env):
        (env.repo / "old.txt").unlink()
        (env.repo / "old.txt").mkdir()
        (env.repo / "old.txt" / "inner").write_text("untracked\n")
        tree = csb.host_base_tree(str(env.repo))
        assert (
            "old.txt"
            not in git(env.repo, "ls-tree", "-r", "--name-only", tree).decode()
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
    def test_an_unreadable_tracked_file_is_refused(self, env):
        target = env.repo / "src" / "app.py"
        target.chmod(0)
        try:
            with pytest.raises(csb.SandboxRefused, match="読めない"):
                csb.host_base_tree(str(env.repo))
        finally:
            target.chmod(0o644)

    def test_an_unmerged_index_is_a_preview_blocker(self, env):
        blob = (
            git(env.repo, "hash-object", "-w", "--stdin", input=b"x\n").decode().strip()
        )
        git(
            env.repo,
            "update-index",
            "--index-info",
            input=f"100644 {blob} 1\tconflict.txt\n".encode(),
        )
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert "未解決のマージがある" in data["blockers"]

    def test_the_real_apply_notes_a_vanished_repository(self, env, verified):
        sid, sha = verified
        env.attest_all(sid, sha)
        os.rename(env.repo, env.tmp / "moved")
        rc, out, _ = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "base tree を求められない" in out

    def test_a_patch_with_no_paths_has_no_filtered_targets(self, env):
        summary = gate.analyze_patch(b"")
        assert csb._filtered_paths(str(env.repo), summary) == []

    def test_preview_data_refuses_other_subcommands(self, env):
        with pytest.raises(ValueError):
            csb.preview_data(_cmd("ls"), str(env.repo), env.state)


class TestHarnessPatches:
    def _harness_patch(self, env):
        sid = env.create("--image", "generic")
        work = env.work_root(sid)
        (work / ".claude").mkdir()
        (work / ".claude" / "settings.json").write_text("{}\n")
        sha = env.export(sid)
        rc, _, err = env.run("verify", sid, "--patch", sha, "--", "true")
        assert rc == 0, err
        env.attest_all(sid, sha, [*gate.REQUIRED_REVIEWERS, "security-reviewer"])
        return sid, sha

    def test_apply_refuses_and_prints_the_manual_steps(self, env):
        sid, sha = self._harness_patch(env)
        rc, out, err = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "csb: 拒否" in err
        patch_file = gate.patch_path(env.state, sid, sha)
        for step in gate.harness_manual_steps(str(env.repo), patch_file, sha):
            assert step in out
        assert not (env.repo / ".claude").exists()

    def test_preflight_shows_the_same_blocker(self, env):
        sid, sha = self._harness_patch(env)
        rc, out, _ = env.run("preflight", sid, "--patch", sha)
        assert rc == 1 and "ハーネス" in out and "git -C" in out


class TestApplyFilters:
    def test_a_filtered_target_is_refused_and_the_filter_never_runs(
        self, env, verified
    ):
        # Only the file the patch adds matches: the tracked tree is unfiltered, so
        # it is git apply's own filter run that has to be refused.
        sid, sha = verified
        env.attest_all(sid, sha)
        (env.repo / ".git" / "info").mkdir(exist_ok=True)
        (env.repo / ".git" / "info" / "attributes").write_text("new.txt filter=evil\n")
        marks = _plant_markers(env)
        rc, out, err = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "filter" in err
        assert list(marks.iterdir()) == []
        assert not (env.repo / "new.txt").exists()

    def test_a_filter_on_a_tracked_file_stops_the_base_tree(self, env, verified):
        sid, sha = verified
        env.attest_all(sid, sha)
        (env.repo / ".git" / "info").mkdir(exist_ok=True)
        (env.repo / ".git" / "info" / "attributes").write_text(
            "README.md filter=evil\n"
        )
        rc, out, _ = env.run("apply", sid, "--patch", sha)
        assert rc == 2 and "filter 属性のあるリポジトリは非対応" in out
        assert (env.repo / "README.md").read_text() == "hello\n"


class TestFilteredRepositories:
    """The snapshot takes working-tree bytes with no filter, so a filter
    on any tracked path (git-crypt, transcrypt, lfs, anything) refuses create."""

    def _encrypted(self, env, attrs):
        (env.repo / "secret.env").write_text("CIPHERTEXT\n")
        git(env.repo, "add", "secret.env")
        git(env.repo, "commit", "-q", "-m", "secret")
        attrs()
        plaintext = b"PLAINTEXT-never-for-the-sandbox\n"
        (env.repo / "secret.env").write_bytes(plaintext)  # unlocked working tree
        return git(env.repo, "hash-object", "--stdin", input=plaintext).decode().strip()

    @pytest.mark.parametrize(
        "where",
        [
            "gitattributes",
            "subdir",
            "info",
            "attributes-file",
            "macro",
            "index-only",
            "xdg",
        ],
    )
    def test_create_is_refused_before_the_plaintext_is_hashed(
        self, env, tmp_path, where
    ):
        def attrs():
            if where == "gitattributes":
                (env.repo / ".gitattributes").write_text(
                    "secret.env filter=git-crypt diff=git-crypt\n"
                )
            elif where == "subdir":
                # an untracked .gitattributes still applies to the tracked src/app.py
                (env.repo / "src" / ".gitattributes").write_text(
                    "app.py filter=transcrypt\n"
                )
            elif where == "info":
                (env.repo / ".git" / "info").mkdir(exist_ok=True)
                (env.repo / ".git" / "info" / "attributes").write_text(
                    "secret.env filter=lfs\n"
                )
            elif where == "attributes-file":
                path = tmp_path / "attrs"
                path.write_text("secret.env filter=crypt\n")
                git(env.repo, "config", "core.attributesFile", str(path))
            elif where == "index-only":
                # the committed .gitattributes filters; the working-tree copy no
                # longer says so (check-attr reads the working tree first)
                (env.repo / ".gitattributes").write_text("secret.env filter=crypt\n")
                git(env.repo, "add", ".gitattributes")
                git(env.repo, "commit", "-q", "-m", "attrs")
                (env.repo / ".gitattributes").write_text("# nothing here\n")
            elif where == "xdg":
                xdg = tmp_path / "xdg"
                (xdg / "git").mkdir(parents=True)
                (xdg / "git" / "attributes").write_text("secret.env filter=crypt\n")
                env.monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
            else:
                (env.repo / ".gitattributes").write_text(
                    "[attr]vault filter=crypt\nsecret.env vault\n"
                )

        oid = self._encrypted(env, attrs)
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 2 and "filter 属性のあるリポジトリは非対応" in err, err
        assert (
            subprocess.run(  # noqa: S603
                ["git", "-C", str(env.repo), "cat-file", "-e", oid], capture_output=True
            ).returncode
            != 0
        ), "the plaintext was written into the object store"
        assert env.fake.calls == []

    def test_an_unset_filter_is_fine(self, env):
        (env.repo / ".gitattributes").write_text("*.bin -filter\n*.txt !filter\n")
        git(env.repo, "add", ".gitattributes")
        git(env.repo, "commit", "-q", "-m", "attrs")
        assert env.create("--image", "generic")

    def test_the_preview_shows_the_blocker(self, env):
        (env.repo / ".gitattributes").write_text("secret.env filter=git-crypt\n")
        (env.repo / "secret.env").write_text("x\n")
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "c")
        before = _index_bytes(env.repo)
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert any("filter 属性のあるリポジトリは非対応" in b for b in data["blockers"])
        assert "secret.env" in "".join(data["blockers"])
        assert _index_bytes(env.repo) == before


# ---------------------------------------------------------------------------
# ls / status / rm / gc / image build
# ---------------------------------------------------------------------------
class TestListing:
    def test_empty(self, env):
        assert env.run("ls")[1].strip() == "サンドボックスは無い"
        assert env.run("status")[1].strip() == "サンドボックスは無い"
        assert json.loads(env.run("ls", "--json")[1]) == []

    def test_ls_and_status(self, env, verified):
        sid, sha = verified
        rc, out, _ = env.run("ls")
        assert rc == 0 and out.startswith(sid) and "running" in out and "net off" in out
        rows = json.loads(env.run("ls", "--json")[1])
        assert rows[0]["id"] == sid and rows[0]["check"] == "ok"
        env.run("net", "on", sid)
        assert "net on" in env.run("ls")[1]
        rc, out, _ = env.run("status", sid)
        assert rc == 0
        assert f"パッチ {sha}: 5 ファイル" in out and "verify PASS" in out
        assert "code-reviewer=missing" in out
        data = json.loads(env.run("status", sid, "--json")[1])
        assert data["patches"][0]["verify"] == "PASS"
        assert json.loads(env.run("status", "--json")[1])[0]["id"] == sid
        assert "ネットワーク: on" in env.run("status")[1]

    def test_docker_down_and_broken_entries(self, env, sandbox):
        os.mkdir(os.path.join(env.state, IDS[3]), 0o700)
        Path(env.state, "not-a-sandbox").write_text("x")
        env.fake.fail["inspect"] = _fail("Cannot connect to the Docker daemon")
        rc, out, _ = env.run("ls")
        assert rc == 0
        assert "?" in out and "照合" in out
        assert f"{IDS[3]}  (壊れている" in out
        rc, out, _ = env.run("status")
        assert rc == 0 and "壊れている" in out
        bad_patch = gate.patch_path(env.state, sandbox, "d" * 64)
        csb._write_private(bad_patch, b"mismatch")
        Path(os.path.dirname(bad_patch), "junk").write_text("x")
        rc, out, _ = env.run("status", sandbox)
        assert "一致しない" in out

    def test_status_of_unknown_sandbox(self, env):
        assert env.run("status", IDS[3])[0] == 2


class TestRemove:
    def test_rm_removes_only_this_sandbox(self, env):
        first = env.create("--image", "generic")
        second = env.create("--image", "generic")
        rc, out, err = env.run("rm", first)
        assert rc == 0, err
        assert not os.path.exists(gate.sandbox_dir(env.state, first))
        labels = [c["labels"].get(gate.LABEL_ID) for c in env.fake.containers.values()]
        assert first not in labels and second in labels
        assert gate.network_name(first) not in env.fake.networks
        assert gate.network_name(second) in env.fake.networks
        assert env.fake.find_image(f"csb-setup:{first}") is None
        assert env.fake.find_image(f"csb-setup:{second}") is not None
        assert env.fake.find_image(gate.GENERIC_IMAGE_REF) is not None

    def test_rm_without_meta_uses_a_fresh_endpoint(self, env, sandbox):
        os.unlink(gate.meta_path(env.state, sandbox))
        assert env.run("rm", sandbox)[0] == 0
        assert env.fake.commands("context", "inspect")

    def test_rm_busy_or_missing(self, env, sandbox):
        assert env.run("rm", IDS[3])[0] == 2
        lock = csb._Lock(os.path.join(gate.sandbox_dir(env.state, sandbox), "lock"))
        lock.acquire()
        try:
            rc, _, err = env.run("rm", sandbox)
        finally:
            lock.release()
        assert rc == 2 and "使用中" in err

    def test_rm_reports_docker_failures(self, env, sandbox):
        env.fake.fail["rm"] = _fail("daemon gone")
        env.fake.fail["rmi"] = _fail("in use")
        rc, _, err = env.run("rm", sandbox)
        assert rc == 1 and "消せない" in err
        assert os.path.exists(gate.sandbox_dir(env.state, sandbox))


class TestGc:
    def test_gc_removes_old_and_orphans_but_keeps_young_and_busy(self, env):
        old = env.create("--image", "generic")
        env.clock.advance(8 * 86400)
        young = env.create("--image", "generic")
        orphan = IDS[3]
        env.fake.add_container(
            gate.container_name(orphan),
            {gate.SANDBOX_LABEL: "1", gate.LABEL_ID: orphan},
        )
        env.fake.networks["csb-net-x"] = {
            "Id": "n" * 12,
            "Name": "csb-net-x",
            "Labels": {gate.SANDBOX_LABEL: "1", gate.LABEL_ID: "not-an-id"},
        }
        rc, out, err = env.run("gc")
        assert rc == 0, err
        assert old in out and orphan in out
        assert not os.path.exists(gate.sandbox_dir(env.state, old))
        assert os.path.exists(gate.sandbox_dir(env.state, young))
        assert env.fake.find_container(gate.container_name(orphan)) is None
        assert env.fake.work(young) is not None

    def test_gc_older_than_and_busy(self, env, sandbox):
        env.clock.advance(2 * 86400)
        lock = csb._Lock(os.path.join(gate.sandbox_dir(env.state, sandbox), "lock"))
        lock.acquire()
        try:
            env.run("gc", "--older-than", "1d")
        finally:
            lock.release()
        assert os.path.exists(gate.sandbox_dir(env.state, sandbox))
        rc, out, _ = env.run("gc", "--older-than", "1d")
        assert rc == 0 and sandbox in out

    def test_gc_handles_dirs_without_meta_and_docker_errors(self, env):
        # 時刻は実時刻から作る: meta の無いディレクトリの年齢は、ファイルシステムの
        # mtime (実時刻) で見るため。gc は lock を開く前に mtime を読む (開くと
        # ディレクトリの mtime が今になる)。
        now = time.time()
        env.clock.t = now - 30 * 86400
        sandbox = env.create("--image", "generic")
        env.clock.t = now
        broken = os.path.join(env.state, IDS[3])
        os.mkdir(broken, 0o700)
        os.utime(broken, (now - 30 * 86400, now - 30 * 86400))
        lock_link = os.path.join(env.state, IDS[2])
        os.mkdir(lock_link, 0o700)
        os.symlink(env.tmp / "elsewhere", os.path.join(lock_link, "lock"))
        env.fake.fail["rm"] = _fail("daemon gone")
        env.fake.fail["ps"] = lambda args: (
            _fail("daemon gone")
            if "--no-trunc" in args and f"{gate.SANDBOX_LABEL}=1" in args[-1]
            else env.fake.do_ps(args, None, None)
        )
        rc, _, err = env.run("gc")
        assert rc == 0
        assert f"{sandbox} を消せない" in err and "調べられない" in err
        assert os.path.exists(gate.sandbox_dir(env.state, sandbox))
        assert not os.path.exists(broken)
        assert os.path.exists(lock_link)


class TestCleanupWhenDockerIsBroken:
    """固定した docker が検査に落ちたとき・docker に届かないときの ls / rm / gc。"""

    @staticmethod
    def refuse_the_pin(env):
        def refuse(path):
            raise csb.SandboxRefused(f"固定した docker が検査に落ちた: {path}")

        env.monkeypatch.setattr(csb, "_check_pinned_docker", refuse)

    @staticmethod
    def fail_rm_for(env, sid):
        stuck = env.fake.work(sid)["Id"]
        env.fake.fail["rm"] = lambda args: (
            _fail("container is busy")
            if stuck in args
            else env.fake.do_rm(args, None, None)
        )

    def test_ls_and_status_show_the_refusal(self, env, sandbox):
        self.refuse_the_pin(env)
        rc, out, _ = env.run("ls")
        assert rc == 0
        assert "[照合: 固定した docker が検査に落ちた" in out
        # the net column names the cause: docker was refused, not unreachable
        assert "net 不明 (固定した docker が検査に落ちた)" in out
        assert "docker に届かない" not in out
        rc, out, _ = env.run("status", sandbox)
        assert "ネットワーク: 不明 (固定した docker が検査に落ちた)" in out
        # --json keeps its shape: no display-only keys
        rc, out, _ = env.run("status", sandbox, "--json")
        row = json.loads(out)
        assert rc == 0
        assert row["container"] == "?" and row["net_connected"] is None
        assert row["check"].startswith("固定した docker が検査に落ちた")
        assert not [k for k in row if k.startswith("_")]
        rc, out, _ = env.run("ls", "--json")
        assert not [k for r in json.loads(out) for k in r if k.startswith("_")]

    def test_unreachable_docker_keeps_its_wording(self, env, sandbox):
        env.fake.fail["inspect"] = _fail("Cannot connect to the Docker daemon")
        rc, out, _ = env.run("ls")
        assert rc == 0 and "net 不明 (docker に届かない)" in out

    def test_net_off_still_refuses(self, env, sandbox):
        self.refuse_the_pin(env)
        assert env.run("net", "off", sandbox)[0] == 2

    def test_an_expired_window_only_warns(self, env, sandbox):
        env.run("net", "on", sandbox, "--minutes", "1")
        env.clock.advance(61)
        self.refuse_the_pin(env)
        rc, _, err = env.run("ls")
        assert rc == 0 and "期限切れの切断に失敗した" in err

    def test_rm_switches_to_a_fresh_pin(self, env, sandbox):
        other = env.create("--image", "generic")
        self.refuse_the_pin(env)
        rc, _, err = env.run("rm", sandbox)
        assert rc == 0, err
        assert "固定の場所の docker に切り替える" in err
        assert not os.path.exists(gate.sandbox_dir(env.state, sandbox))
        assert env.fake.work(sandbox) is None
        assert gate.network_name(sandbox) not in env.fake.networks
        # ラベルの ID で絞るので、ほかのサンドボックスには触れない
        assert env.fake.work(other) is not None

    def test_rm_keeps_the_recorded_endpoint(self, env, sandbox):
        # 空の設定の既定の context (ここでは別のソケット) を読み直さず、作成時に
        # 記録した endpoint の daemon で消す
        env.fake.context["Host"] = "unix:///var/run/other-docker.sock"
        self.refuse_the_pin(env)
        start = len(env.fake.calls)
        rc, _, err = env.run("rm", sandbox)
        assert rc == 0, err
        calls = env.fake.calls[start:]
        assert not [a for _, a in calls if a[:2] == ["context", "inspect"]]
        assert {ep.host for ep, _ in calls} == {DOCKER_HOST}
        assert {ep.bin for ep, _ in calls} == {DOCKER_BIN}
        assert env.fake.work(sandbox) is None

    def test_gc_switches_and_goes_on_after_a_failure(self, env):
        stuck = env.create("--image", "generic")
        plain = env.create("--image", "generic")
        self.fail_rm_for(env, stuck)
        env.clock.advance(8 * 86400)
        self.refuse_the_pin(env)
        rc, out, err = env.run("gc")
        assert rc == 0, err
        assert f"{stuck} を消せない" in err
        assert os.path.exists(gate.sandbox_dir(env.state, stuck))
        assert plain in out and not os.path.exists(gate.sandbox_dir(env.state, plain))

    def test_gc_does_not_stop_when_no_docker_can_be_pinned(self, env, sandbox):
        env.clock.advance(8 * 86400)
        self.refuse_the_pin(env)

        def no_docker():
            raise csb.SandboxRefused("docker が見つからない")

        env.monkeypatch.setattr(csb, "_find_docker_binary", no_docker)
        rc, _, err = env.run("gc")
        assert rc == 0
        assert f"{sandbox} を消せない" in err and "調べられない" in err
        assert os.path.exists(gate.sandbox_dir(env.state, sandbox))

    @pytest.mark.parametrize("listing", ["ps", "network ls", "images"])
    def test_rm_with_a_failing_listing_keeps_the_state(self, env, sandbox, listing):
        env.fake.fail[listing] = _fail("Cannot connect to the Docker daemon")
        rc, _, err = env.run("rm", sandbox)
        assert rc == 1 and "の一覧: Cannot connect" in err
        assert os.path.exists(gate.sandbox_dir(env.state, sandbox))

    def test_rm_with_docker_down_keeps_everything(self, env, sandbox):
        for listing in ("ps", "network ls", "images"):
            env.fake.fail[listing] = _fail("Cannot connect to the Docker daemon")
        rc, out, err = env.run("rm", sandbox)
        assert rc == 1 and "削除した" not in out
        assert os.path.exists(gate.sandbox_dir(env.state, sandbox))
        assert env.fake.work(sandbox) is not None
        assert gate.network_name(sandbox) in env.fake.networks

    def test_a_failed_create_reports_a_failing_listing(self, env):
        env.fake.fail["start"] = _fail("cannot start")
        env.fake.fail["ps"] = _fail("Cannot connect to the Docker daemon")
        rc, _, err = env.run("create", "--image", "generic")
        assert rc == 1 and "片付けに失敗: コンテナの一覧" in err

    def test_gc_keeps_what_a_concurrent_create_made(self, env, monkeypatch):
        # gc がラベルの一覧を取る直前に create が始まった形: 状態のディレクトリと
        # ラベル付きのネットワークが、gc が最初に読んだ一覧の後にできる。
        late = IDS[3]
        real = csb._labelled_ids

        def labelled(ep):
            os.mkdir(os.path.join(env.state, late), 0o700)
            env.fake.networks[gate.network_name(late)] = {
                "Id": "f" * 12,
                "Name": gate.network_name(late),
                "Labels": {gate.SANDBOX_LABEL: "1", gate.LABEL_ID: late},
            }
            return real(ep)

        os.makedirs(env.state, mode=0o700, exist_ok=True)
        monkeypatch.setattr(csb, "_labelled_ids", labelled)
        rc, out, err = env.run("gc")
        assert rc == 0, err
        assert late not in out
        assert gate.network_name(late) in env.fake.networks


class TestUnexpectedErrors:
    @pytest.mark.parametrize(
        "error",
        [OSError(28, "No space left on device"), subprocess.TimeoutExpired(["git"], 5)],
        ids=["oserror", "timeout"],
    )
    def test_main_turns_them_into_exit_1(self, env, monkeypatch, error):
        def broken():
            raise error

        monkeypatch.setattr(csb, "expire_windows", broken)
        rc, _, err = env.run("ls")
        assert rc == 1 and err.startswith("csb: エラー: ")
        assert "Traceback" not in err


class TestImageBuild:
    def test_builds_the_generic_image(self, env, monkeypatch, tmp_path):
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        (ctx / "Dockerfile").write_text("FROM scratch\n")
        monkeypatch.setattr(csb, "_generic_context", lambda: str(ctx))
        rc, out, _ = env.run("image", "build", "--generic")
        assert rc == 0 and gate.GENERIC_IMAGE_REF in out
        assert env.fake.commands("build")[-1] == [
            "build",
            "--pull",
            "-t",
            gate.GENERIC_IMAGE_REF,
            str(ctx),
        ]

    def test_missing_dockerfile(self, env, monkeypatch, tmp_path):
        monkeypatch.setattr(csb, "_generic_context", lambda: str(tmp_path / "nope"))
        assert env.run("image", "build", "--generic")[0] == 1

    def test_default_context_is_the_repository_one(self):
        assert csb._generic_context() == str(REPO_ROOT / "containers" / "sandbox")
        assert (REPO_ROOT / "containers" / "sandbox" / "Dockerfile").is_file()


# ---------------------------------------------------------------------------
# The real _docker, binary discovery and endpoint pinning
# ---------------------------------------------------------------------------
STUB = """#!/bin/sh
printf 'ARG:%s\\n' "$@"
echo "DOCKER_HOST=${DOCKER_HOST-unset} DOCKER_CONTEXT=${DOCKER_CONTEXT-unset}"
echo "DOCKER_TLS_VERIFY=${DOCKER_TLS_VERIFY-unset} DOCKER_CONFIG=${DOCKER_CONFIG-unset}"
echo "KEEP=${KEEP-unset}"
echo "PATH=$PATH"
echo "DEVELOPER_DIR=${DEVELOPER_DIR-unset} DYLD_INSERT_LIBRARIES=${DYLD_INSERT_LIBRARIES-unset}"
echo "HOME=${HOME-unset}"
for last; do :; done  # the subcommand is the last word (after --config / -H)
case "$last" in
  stdin) cat ;;
  sleep) exec sleep 5 ;;
  write) [ "$1" = --config ] && mkdir -p "$2/buildx" && echo state > "$2/buildx/x" ;;
  fail) echo oops >&2; exit 3 ;;
esac
"""


@pytest.fixture
def stub(tmp_path, monkeypatch):
    monkeypatch.setattr(csb, "_state_root", lambda: str(tmp_path / "state"))
    path = tmp_path / "bin" / "docker"
    path.parent.mkdir()
    path.write_text(STUB)
    path.chmod(0o755)
    for key, value in {
        "DOCKER_HOST": "tcp://evil:2375",
        "DOCKER_CONTEXT": "evil",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CONFIG": "/evil",
        "KEEP": "kept",
        "DEVELOPER_DIR": "/evil",
        "DYLD_INSERT_LIBRARIES": "/evil/lib.dylib",
    }.items():
        monkeypatch.setenv(key, value)
    return csb.Endpoint(str(path), "unix:///run/fake.sock")


class TestDockerConfigUse:
    def test_only_the_approved_create_reads_the_user_config(self, env):
        env.fake.add_image("ubuntu:24.04")
        (env.repo / ".devcontainer").mkdir()
        (env.repo / ".devcontainer" / "devcontainer.json").write_text(
            '{"image": "ubuntu:24.04"}'
        )
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "dc")
        sid = env.create()
        env.run("exec", sid, "--", "true")
        env.run("ls")
        users = {tuple(a) for a, user in env.fake.configs if user}
        assert users, env.fake.configs
        assert users <= {
            ("context", "inspect"),
            ("pull", "ubuntu:24.04"),
            ("build", "-f"),
        }
        others = [a for a, user in env.fake.configs if not user]
        assert any(a[:1] == ["exec"] for a in others)
        assert any(a[:1] == ["inspect"] for a in others)


class TestImageBuildBoundary:
    def test_the_preview_data(self, env, monkeypatch):
        monkeypatch.setattr(csb, "_find_docker_binary", lambda: "/x/docker")
        monkeypatch.setattr(csb, "_owner_name", lambda path: "root")
        monkeypatch.setattr(csb, "_docker_signed_roots", lambda: ())
        data = csb.preview_data(
            _cmd("image", "build", "--generic"), str(env.repo), env.state
        )
        dockerfile = os.path.join(csb._generic_context(), "Dockerfile")
        assert data["kind"] == "image"
        assert data["dockerfile"] == dockerfile
        with open(dockerfile, "rb") as fh:
            assert data["sha256"] == hashlib.sha256(fh.read()).hexdigest()
        assert data["bases"] and all(len(b) == 2 for b in data["bases"])
        assert data["docker"]["path"] == "/x/docker"
        assert data["blockers"] == []

    def test_the_instructions_and_the_context_against_head(self, env, monkeypatch):
        context = env.repo / "containers" / "sandbox"
        context.mkdir(parents=True)
        (context / "Dockerfile").write_text(
            "FROM debian:trixie-slim\nRUN apt-get update \\\n  && make\nUSER 1000\n"
        )
        (context / "README.md").write_text("x\n")
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-q", "-m", "ctx")
        monkeypatch.setattr(csb, "_generic_context", lambda: str(context))
        monkeypatch.setattr(csb, "_find_docker_binary", lambda: "/x/docker")

        def data():
            return csb.preview_data(
                _cmd("image", "build", "--generic"), str(env.repo), env.state
            )

        first = data()
        assert first["instructions"] == ["RUN apt-get update && make", "USER 1000"]
        assert first["context_diff"] == {"state": "none", "count": 0}
        # an edit, a new untracked file and a deletion: three files, no refusal
        (context / "Dockerfile").write_text("FROM debian:trixie-slim\nRUN evil\n")
        (context / "new.sh").write_text("echo\n")
        (context / "README.md").unlink()
        changed = data()
        assert changed["context_diff"] == {"state": "changed", "count": 3}
        assert not [b for b in changed["blockers"] if "HEAD" in b]
        # a change outside the context does not count
        (env.repo / "README.md").write_text("elsewhere\n")
        assert data()["context_diff"]["count"] == 3

    def test_dockerfile_blockers_reach_the_preview(self, env, monkeypatch, tmp_path):
        context = tmp_path / "ctx"
        context.mkdir()
        (context / "Dockerfile").write_text("FROM x\nRUN <<EOF\nevil\nEOF\n")
        monkeypatch.setattr(csb, "_generic_context", lambda: str(context))
        monkeypatch.setattr(csb, "_find_docker_binary", lambda: "/x/docker")
        data = csb.preview_data(
            _cmd("image", "build", "--generic"), str(env.repo), env.state
        )
        assert any("heredoc" in b for b in data["blockers"])

    def test_a_fifo_dockerfile_does_not_hang(self, env, monkeypatch, tmp_path):
        # opened with O_NONBLOCK, checked with fstat, so a FIFO is refused
        import signal

        context = tmp_path / "ctx"
        context.mkdir()
        os.mkfifo(context / "Dockerfile")
        monkeypatch.setattr(csb, "_generic_context", lambda: str(context))
        monkeypatch.setattr(csb, "_find_docker_binary", lambda: "/x/docker")

        def hung(*_):
            raise TimeoutError("open() blocked on the FIFO")

        old = signal.signal(signal.SIGALRM, hung)
        signal.alarm(5)
        try:
            data = csb.preview_data(
                _cmd("image", "build", "--generic"), str(env.repo), env.state
            )
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        assert any("Dockerfile を読めない" in b for b in data["blockers"])

    def test_a_context_outside_a_repository_is_unknown(
        self, env, monkeypatch, tmp_path
    ):
        context = tmp_path / "loose"
        context.mkdir()
        (context / "Dockerfile").write_text("FROM x\n")
        monkeypatch.setattr(csb, "_generic_context", lambda: str(context))
        monkeypatch.setattr(csb, "_find_docker_binary", lambda: "/x/docker")
        data = csb.preview_data(
            _cmd("image", "build", "--generic"), str(env.repo), env.state
        )
        assert data["context_diff"]["state"] == "unknown"

    def test_a_missing_dockerfile_is_a_blocker(self, env, monkeypatch, tmp_path):
        monkeypatch.setattr(csb, "_generic_context", lambda: str(tmp_path / "none"))
        data = csb.preview_data(
            _cmd("image", "build", "--generic"), str(env.repo), env.state
        )
        assert any("Dockerfile" in b for b in data["blockers"])

    def test_the_preview_request_takes_image_build(self):
        req = json.dumps(
            {"argv": ["image", "build", "--generic"], "cwd": "/", "state": "/s"}
        )
        parsed = csb._preview_request(req)
        assert parsed is not None and parsed[0].sub == "image build"

    def test_the_build_uses_the_user_docker_config(self, env):
        rc, _, err = env.run("image", "build", "--generic")
        assert rc == 0, err
        users = [tuple(a) for a, user in env.fake.configs if user]
        assert ("context", "inspect") in users and ("build", "--pull") in users


class TestHostGitBinaryAndEnv:
    """E: host-side git is the root-owned /usr/bin/git, on a fixed PATH, with an
    allowlisted environment (xcrun's shim follows DEVELOPER_DIR / TOOLCHAINS /
    SDKROOT; GIT_EXEC_PATH and friends would run other helpers)."""

    BAD = {
        "DEVELOPER_DIR": "/evil",
        "TOOLCHAINS": "evil",
        "SDKROOT": "/evil",
        "GIT_EXEC_PATH": "/evil",
        "GIT_SSH_COMMAND": "evil",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "/evil",
        "DYLD_INSERT_LIBRARIES": "/evil/lib.dylib",
        "LD_PRELOAD": "/evil/lib.so",
        "GIT_DIR": "/evil",
    }

    def _spy(self, monkeypatch):
        seen = []
        real_run, real_popen = subprocess.run, subprocess.Popen

        def run(argv, **kw):
            seen.append((list(argv), kw.get("env")))
            return real_run(argv, **kw)

        def popen(argv, **kw):
            seen.append((list(argv), kw.get("env")))
            return real_popen(argv, **kw)

        monkeypatch.setattr(csb.subprocess, "run", run)
        monkeypatch.setattr(csb.subprocess, "Popen", popen)
        return seen

    def test_git_runs_as_the_system_git_with_an_allowlisted_env(self, env, monkeypatch):
        for key, value in self.BAD.items():
            monkeypatch.setenv(key, value)
        seen = self._spy(monkeypatch)
        assert csb._git_ok(str(env.repo), ["rev-parse", "HEAD"]).strip()
        csb._BlobReader(str(env.repo)).close()
        # rev-parse, and the cat-file --batch process (plus whatever it starts)
        assert len(seen) >= 2 and any("cat-file" in a for a, _ in seen)
        for argv, child_env in seen:
            assert argv[0] == "/usr/bin/git" == csb.HOST_GIT_BIN
            assert child_env["PATH"] == csb.HOST_PATH
            assert not set(self.BAD) & set(child_env), sorted(child_env)
            assert child_env["HOME"] == os.environ["HOME"]
            assert child_env["GIT_OPTIONAL_LOCKS"] == "0"

    def test_the_fixed_path_holds_only_root_owned_dirs(self):
        for d in csb.HOST_PATH.split(":"):
            st = os.lstat(d)
            assert stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode), d
            if stat.S_ISDIR(st.st_mode):
                assert st.st_uid == 0 and not st.st_mode & 0o022, d

    def test_an_untrusted_git_is_refused(self, env, monkeypatch):
        monkeypatch.setattr(csb.gate, "trusted_path", lambda path, **kw: "所有者が不正")
        with pytest.raises(csb.SandboxRefused, match="信頼できる git が無い"):
            csb._git(str(env.repo), ["rev-parse", "HEAD"])


class TestRealDocker:
    def test_pins_host_and_scrubs_env(self, stub):
        res = csb._docker(stub, ["ps"])
        lines = res.stdout.decode().splitlines()
        assert lines[0] == "ARG:--config"
        assert lines[2:5] == ["ARG:-H", "ARG:unix:///run/fake.sock", "ARG:ps"]
        assert "DOCKER_HOST=unset DOCKER_CONTEXT=unset" in lines
        assert "DOCKER_TLS_VERIFY=unset DOCKER_CONFIG=unset" in lines
        # an allowlist, not "everything but DOCKER_*" (E)
        assert "KEEP=unset" in lines
        assert "DEVELOPER_DIR=unset DYLD_INSERT_LIBRARIES=unset" in lines
        assert f"HOME={os.environ['HOME']}" in lines
        # a fixed PATH of root-owned dirs, plus the pinned docker's own directory
        # (its credential helpers live next to it in Docker.app)
        assert f"PATH={csb.HOST_PATH}:{os.path.dirname(stub.bin)}" in lines

    @staticmethod
    def _config_of(res):
        lines = res.stdout.decode().splitlines()
        assert lines[0] == "ARG:--config", lines
        return lines[1][len("ARG:") :]

    def test_each_call_gets_a_fresh_private_config_that_is_removed(
        self, stub, tmp_path
    ):
        # ~/.docker/config.json and cli-plugins are user-writable without an
        # ask, so every call gets a new empty private config unless it asks for the
        # user's (the approved create's context, pull and build)
        parent = tmp_path / "state" / "docker-config.d"
        first = self._config_of(csb._docker(stub, ["ps"]))
        second = self._config_of(csb._docker(stub, ["ps"]))
        assert first != second
        for path in (first, second):
            assert os.path.dirname(path) == str(parent)
            assert not os.path.lexists(path), "removed after the call"
        st = os.lstat(parent)
        assert stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700
        lines = csb._docker(stub, ["ps"], user_config=True).stdout.decode().splitlines()
        assert lines[:3] == ["ARG:-H", "ARG:unix:///run/fake.sock", "ARG:ps"]

    def test_the_config_is_empty_and_private_while_docker_runs(self, stub, monkeypatch):
        seen = []
        real = csb.subprocess.Popen

        def popen(argv, **kw):
            path = argv[argv.index("--config") + 1]
            st = os.lstat(path)
            seen.append((os.listdir(path), stat.S_IMODE(st.st_mode)))
            return real(argv, **kw)

        monkeypatch.setattr(csb.subprocess, "Popen", popen)
        csb._docker(stub, ["ps"])
        assert seen == [([], 0o700)]

    def test_what_docker_writes_does_not_reach_the_next_call(self, stub, tmp_path):
        written = self._config_of(csb._docker(stub, ["write"]))
        assert not os.path.lexists(written)
        res = csb._docker(stub, ["ps"])
        assert res.returncode == 0 and self._config_of(res) != written
        assert os.listdir(tmp_path / "state" / "docker-config.d") == []

    def test_the_config_is_removed_when_the_call_fails(self, stub, monkeypatch):
        made = []
        real = csb.tempfile.mkdtemp

        def mkdtemp(**kw):
            made.append(real(**kw))
            return made[-1]

        def boom():
            raise RuntimeError("on_spawn failed")

        monkeypatch.setattr(csb.tempfile, "mkdtemp", mkdtemp)
        with pytest.raises(RuntimeError):
            csb._docker(stub, ["ps"], on_spawn=boom)
        assert csb._docker(stub, ["sleep"], timeout=0.1).returncode == 124
        assert len(made) == 2 and not any(os.path.lexists(p) for p in made)

    def test_a_parent_that_is_not_a_private_dir_is_refused(self, stub, tmp_path):
        state = tmp_path / "state"
        state.mkdir(mode=0o700, exist_ok=True)
        (tmp_path / "elsewhere").mkdir()
        (state / "docker-config.d").symlink_to(tmp_path / "elsewhere")
        with pytest.raises(csb.SandboxRefused, match="状態ディレクトリが不正"):
            csb._docker(stub, ["ps"])
        assert os.listdir(tmp_path / "elsewhere") == []

    def test_input_bytes_and_file(self, stub, tmp_path):
        assert csb._docker(stub, ["stdin"], input=b"payload").stdout.endswith(
            b"payload"
        )
        source = tmp_path / "in.tar"
        source.write_bytes(b"from-file")
        assert csb._docker(stub, ["stdin"], input_path=str(source)).stdout.endswith(
            b"from-file"
        )

    def test_failure_timeout_and_missing_binary(self, stub, tmp_path):
        res = csb._docker(stub, ["fail"])
        assert (res.returncode, res.stderr) == (3, b"oops\n")
        res = csb._docker(stub, ["sleep"], timeout=0.2)
        assert res.returncode == 124 and b"timeout" in res.stderr
        res = csb._docker(csb.Endpoint(str(tmp_path / "absent"), ""), ["ps"])
        assert res.returncode == 127

    def test_no_host_flag_for_context_discovery(self, stub):
        res = csb._docker(csb.Endpoint(stub.bin, ""), ["context"])
        lines = res.stdout.decode().splitlines()
        assert lines[2] == "ARG:context" and "ARG:-H" not in lines

    def test_uncaptured_output_and_on_spawn(self, stub, capfd):
        spawned = []
        res = csb._docker(
            stub,
            ["ps"],
            capture=False,
            output_to_stderr=True,
            on_spawn=lambda: spawned.append(1),
        )
        assert res.returncode == 0 and res.stdout == b""
        captured = capfd.readouterr()
        assert "ARG:ps" in captured.err and captured.out == ""
        assert spawned == [1]
        csb._docker(stub, ["ps"], capture=False)
        assert "ARG:ps" in capfd.readouterr().out

    def test_docker_ok_raises_with_shown_stderr(self, stub):
        with pytest.raises(csb.SandboxError, match="oops"):
            csb._docker_ok(stub, ["fail"])

    def test_endpoint_from_meta(self, monkeypatch):
        checked = []
        monkeypatch.setattr(csb, "_check_pinned_docker", checked.append)
        assert csb._endpoint(
            {"docker_bin": "/a/docker", "docker_host": "unix:///s"}
        ) == csb.Endpoint("/a/docker", "unix:///s")
        assert checked == ["/a/docker"]


class TestBinaryAndEndpoint:
    """docker comes from fixed places only (never the inherited PATH, which
    may put ~/.cargo/bin first), its realpath must sit under a known root, and the
    chain must pass trusted_path with root or the user as owner."""

    @pytest.fixture
    def places(self, tmp_path, monkeypatch):
        top = tmp_path / "top"
        dirs = [top / "first" / "bin", top / "second" / "bin"]
        for d in dirs:
            d.mkdir(parents=True)
        monkeypatch.setattr(csb, "_docker_candidates", lambda: tuple(map(str, dirs)))
        monkeypatch.setattr(csb, "_docker_roots", lambda: (f"{top}/",))
        monkeypatch.setattr(csb, "_DOCKER_TRUST_TOP", str(tmp_path))
        return top, dirs

    def _binary(self, path, mode=0o755):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(mode)
        return path

    def test_the_first_fixed_place_and_its_realpath(self, places):
        top, (first, second) = places
        real = self._binary(top / "App" / "bin" / "docker")
        (second / "docker").symlink_to(real)
        assert csb._find_docker_binary() == str(real)

    def test_the_inherited_path_is_not_used(self, places, tmp_path, monkeypatch):
        cargo = self._binary(tmp_path / ".cargo" / "bin" / "docker")
        monkeypatch.setenv("PATH", f"{cargo.parent}:{os.environ['PATH']}")
        with pytest.raises(csb.SandboxRefused, match="見つからない"):
            csb._find_docker_binary()

    def test_a_realpath_outside_the_known_roots_is_refused(self, places, tmp_path):
        _, (first, _) = places
        outside = self._binary(tmp_path / ".cargo" / "bin" / "docker")
        (first / "docker").symlink_to(outside)
        with pytest.raises(csb.SandboxRefused, match="既知の場所の外"):
            csb._find_docker_binary()

    def test_the_first_existing_place_decides(self, places):
        # an unsafe docker in the first place is refused, not skipped for the next
        top, (first, second) = places
        self._binary(first / "docker", mode=0o777)
        self._binary(second / "docker")
        with pytest.raises(csb.SandboxRefused, match="実体が不正"):
            csb._find_docker_binary()

    @pytest.mark.parametrize(
        "what", ["file-mode", "dir-mode", "name", "not-executable"]
    )
    def test_unsafe_binaries_are_refused(self, places, what):
        top, (first, _) = places
        if what == "name":
            real = self._binary(top / "App" / "podman")
            (first / "docker").symlink_to(real)
        elif what == "file-mode":
            self._binary(first / "docker", mode=0o777)
        elif what == "not-executable":
            self._binary(first / "docker", mode=0o644)
        else:
            self._binary(first / "docker")
            first.chmod(0o777)
        try:
            with pytest.raises(csb.SandboxRefused):
                csb._find_docker_binary()
        finally:
            first.chmod(0o755)

    def test_a_group_the_platform_trusts_may_write(self, places, monkeypatch):
        # /Applications and the Homebrew prefix are admin-group writable on macOS
        top, (first, _) = places
        self._binary(first / "docker")
        first.chmod(0o775)
        try:
            with pytest.raises(csb.SandboxRefused):
                csb._find_docker_binary()
            gid = os.stat(first).st_gid
            monkeypatch.setattr(csb, "_docker_write_groups", lambda: (gid,))
            assert csb._find_docker_binary() == str(first / "docker")
        finally:
            first.chmod(0o755)

    def test_missing_binary(self, places):
        with pytest.raises(csb.SandboxRefused, match="見つからない"):
            csb._find_docker_binary()

    def test_the_fixed_places_per_platform(self, monkeypatch):
        monkeypatch.setattr(csb.sys, "platform", "darwin")
        mac = csb._docker_candidates()
        assert mac[: len(csb.HOST_PATH.split(":"))] == tuple(csb.HOST_PATH.split(":"))
        assert "/Applications/Docker.app/Contents/Resources/bin" in mac
        assert "/opt/homebrew/bin" in mac and "/usr/local/bin" in mac
        monkeypatch.setattr(csb.sys, "platform", "linux")
        linux = csb._docker_candidates()
        assert "/usr/bin" in linux and not [d for d in linux if "Applications" in d]
        for d in (*mac, *linux):
            assert d.startswith("/") and "~" not in d and ".cargo" not in d

    def test_the_pinned_docker_is_checked_again_at_every_use(self, places, tmp_path):
        top, (first, _) = places
        real = self._binary(top / "App" / "bin" / "docker")
        meta = {"docker_bin": str(real), "docker_host": "unix:///s"}
        assert csb._endpoint(meta) == csb.Endpoint(str(real), "unix:///s")
        # replaced by a link to somewhere else, or made writable: refused
        real.unlink()
        real.symlink_to(self._binary(tmp_path / ".cargo" / "bin" / "docker"))
        with pytest.raises(csb.SandboxRefused, match="固定した docker"):
            csb._endpoint(meta)
        real.unlink()
        self._binary(real, mode=0o777)
        with pytest.raises(csb.SandboxRefused, match="固定した docker"):
            csb._endpoint(meta)

    def _signed(self, places, monkeypatch, verdict=""):
        top, (first, _) = places
        real = self._binary(top / "Docker.app" / "bin" / "docker")
        (first / "docker").symlink_to(real)
        # The macOS path on any system: the signed root is the fake Docker.app
        # (the platform branch of _docker_signed_roots), and codesign itself, which
        # only macOS has, is any root-owned file that passes trusted_path (its own
        # trust check is test_an_untrusted_codesign_is_refused); _run_codesign is
        # faked, so it never runs.
        monkeypatch.setattr(
            csb, "_docker_signed_roots", lambda: (f"{top}/Docker.app/",)
        )
        monkeypatch.setattr(csb, "CODESIGN", "/usr/bin/env")
        calls = []

        def run(path):
            calls.append(path)
            return verdict

        monkeypatch.setattr(csb, "_run_codesign", run)
        return real, calls

    def test_a_docker_in_docker_app_must_carry_the_docker_signature(
        self, places, monkeypatch
    ):
        real, calls = self._signed(
            places, monkeypatch, verdict="Docker Inc の署名でない"
        )
        with pytest.raises(csb.SandboxRefused, match="署名でない"):
            csb._find_docker_binary()
        assert calls == [str(real)]

    def test_a_signed_docker_is_pinned_and_checked_again_at_use(
        self, places, monkeypatch
    ):
        real, calls = self._signed(places, monkeypatch)
        assert csb._find_docker_binary() == str(real)
        csb._endpoint({"docker_bin": str(real), "docker_host": "unix:///s"})
        # one codesign per file state in a process; a changed file is checked again
        assert calls == [str(real)]
        real.write_text("#!/bin/sh\nexit 0\n")
        csb._check_pinned_docker(str(real))
        assert calls == [str(real), str(real)]

    def test_outside_docker_app_no_signature_is_asked(self, places, monkeypatch):
        top, (first, _) = places
        self._binary(first / "docker")
        monkeypatch.setattr(
            csb, "_docker_signed_roots", lambda: (f"{top}/Docker.app/",)
        )
        monkeypatch.setattr(csb, "_run_codesign", lambda path: pytest.fail("ran"))
        assert csb._find_docker_binary() == str(first / "docker")

    def test_the_signed_roots_per_platform(self, monkeypatch):
        monkeypatch.setattr(csb.sys, "platform", "darwin")
        assert csb._docker_signed_roots() == ("/Applications/Docker.app/",)
        monkeypatch.setattr(csb.sys, "platform", "linux")
        assert csb._docker_signed_roots() == ()

    def test_an_untrusted_codesign_is_refused(self, tmp_path, monkeypatch):
        fake = self._binary(tmp_path / "bin" / "codesign")
        monkeypatch.setattr(csb, "CODESIGN", str(fake))
        assert "codesign を信頼できない" in csb._codesign_problem("/x/docker")

    def test_the_team_only_check_runs_only_after_a_failure(self, monkeypatch):
        calls = []

        class Result:
            def __init__(self, rc):
                self.returncode = rc

        def run(argv, **kw):
            calls.append(argv[3])
            return Result(0 if len(calls) == 2 else 1)

        monkeypatch.setattr(csb.subprocess, "run", run)
        assert "identifier が docker でない" in csb._run_codesign("/x/docker")
        assert calls == [
            f"-R={csb._DOCKER_REQUIREMENT}",
            f"-R={csb._DOCKER_TEAM_REQUIREMENT}",
        ]
        calls.clear()

        def ok(argv, **kw):
            calls.append(argv[3])
            return Result(0)

        monkeypatch.setattr(csb.subprocess, "run", ok)
        assert csb._run_codesign("/x/docker") == ""
        assert calls == [f"-R={csb._DOCKER_REQUIREMENT}"]

    @pytest.mark.skipif(not os.path.exists("/usr/bin/codesign"), reason="macOS only")
    def test_real_codesign(self, monkeypatch):
        # another team's signature (Apple's ls) fails the requirement; a timeout
        # is a refusal, not a pass
        assert "署名でない" in csb._run_codesign("/bin/ls")
        monkeypatch.setattr(csb, "CODESIGN_TIMEOUT", 1e-6)
        assert "時間切れ" in csb._run_codesign("/bin/ls")

    def test_the_requirement_pins_the_identifier(self):
        # Docker.app/bin holds other Docker-signed binaries (kubectl,
        # docker-credential-desktop, …); a copy of one named docker must not pass
        assert csb._DOCKER_REQUIREMENT.endswith(' and identifier "docker"')

    @pytest.mark.skipif(
        not os.path.exists(
            "/Applications/Docker.app/Contents/Resources/bin/docker-credential-desktop"
        ),
        reason="no Docker Desktop here",
    )
    def test_another_docker_signed_binary_named_docker_fails(self, tmp_path):
        copy = tmp_path / "docker"
        shutil.copy2(
            "/Applications/Docker.app/Contents/Resources/bin/docker-credential-desktop",
            copy,
        )
        # it carries Docker's signature, only the identifier differs
        problem = csb._run_codesign(str(copy))
        assert "Docker の署名だが identifier が docker でない" in problem
        assert "README" in problem

    @pytest.mark.skipif(
        not os.path.exists("/Applications/Docker.app/Contents/Resources/bin/docker"),
        reason="no Docker Desktop here",
    )
    def test_this_machine_s_docker_desktop_passes(self):
        path = "/Applications/Docker.app/Contents/Resources/bin/docker"
        assert csb._codesign_problem(path) == ""

    def test_the_create_preview_shows_the_docker_and_its_owner(self, env, monkeypatch):
        monkeypatch.setattr(
            csb, "_find_docker_binary", lambda: "/x/Docker.app/bin/docker"
        )
        monkeypatch.setattr(csb, "_owner_name", lambda path: "alice")
        monkeypatch.setattr(csb, "_docker_signed_roots", lambda: ("/x/Docker.app/",))
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert data["docker"] == {
            "path": "/x/Docker.app/bin/docker",
            "owner": "alice",
            "signature": "Docker Inc (9BNSXJN65R) 確認済み",
        }
        monkeypatch.setattr(csb, "_docker_signed_roots", lambda: ())
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert data["docker"]["signature"] == "確かめない (Docker.app の外)"

    def test_a_docker_that_cannot_be_pinned_is_a_create_blocker(self, env, monkeypatch):
        def refuse():
            raise csb.SandboxRefused("docker が見つからない (固定の場所: /a)")

        monkeypatch.setattr(csb, "_find_docker_binary", refuse)
        data = csb.preview_data(_cmd("create"), str(env.repo), env.state)
        assert data["docker"] is None
        assert any("docker が見つからない" in b for b in data["blockers"])

    @pytest.mark.parametrize(
        "context",
        [
            {"Host": "tcp://10.0.0.1:2375"},
            {"Host": "ssh://user@host"},
            {"Host": "unix://relative.sock"},
            {"Host": 5},
            {},
        ],
    )
    def test_non_unix_endpoints_are_refused(self, env, context):
        env.fake.context = context
        with pytest.raises(csb.SandboxRefused):
            csb._pin_endpoint()

    def test_unreadable_context(self, env):
        env.fake.fail["context"] = _ok("not json")
        with pytest.raises(csb.SandboxRefused, match="読めない"):
            csb._pin_endpoint()
        env.fake.fail["context"] = _fail("no context")
        with pytest.raises(csb.SandboxRefused, match="調べられない"):
            csb._pin_endpoint()

    def test_pinned(self, env):
        assert csb._pin_endpoint() == EP


class TestHelpers:
    def test_state_root_is_the_gate_one(self):
        assert csb._state_root.__code__ is not None
        assert gate.state_dir().endswith(
            os.path.join(".local", "state", "claude-sandbox")
        )

    def test_default_seams(self):
        assert gate.is_sandbox_id(csb._new_id())
        assert abs(csb._now() - time.time()) < 5
        assert csb._isatty(0) in (True, False)

    def test_write_private_cleans_up_on_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "d" / "f.json"

        def broken(*_a):
            raise OSError("no space")

        monkeypatch.setattr(csb.os, "replace", broken)
        with pytest.raises(OSError):
            csb._write_private(str(target), b"x")
        assert os.listdir(target.parent) == []

    def test_private_dir_must_be_ours(self, tmp_path, monkeypatch):
        d = tmp_path / "s"
        d.mkdir(mode=0o755)
        csb._ensure_private_dir(str(d))
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
        link = tmp_path / "l"
        link.symlink_to(d)
        with pytest.raises(csb.SandboxRefused):
            csb._ensure_private_dir(str(link))

    def test_blob_reader_errors(self, env):
        reader = csb._BlobReader(str(env.repo))
        try:
            with pytest.raises(csb.SandboxError):
                reader.read("0" * 40)
        finally:
            reader.close()

    def test_hash_file_limits(self, tmp_path, monkeypatch):
        f = tmp_path / "f"
        f.write_bytes(b"abc")
        assert csb._hash_file(str(f)) == hashlib.sha256(b"abc").hexdigest()
        monkeypatch.setattr(csb, "REF_MAX_BYTES", 2)
        assert csb._hash_file(str(f)) is None
        assert csb._hash_file(str(tmp_path)) is None
        assert csb._hash_file(str(tmp_path / "missing")) is None

    def test_hash_file_does_not_block_on_a_fifo(self, tmp_path):
        # O_NONBLOCK で開くので、書き手の無い FIFO でも止まらずに None を返す
        import signal

        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        signal.signal(signal.SIGALRM, lambda *_: pytest.fail("blocked on the FIFO"))
        signal.alarm(5)
        try:
            assert csb._hash_file(str(fifo)) is None
        finally:
            signal.alarm(0)

    @pytest.mark.parametrize(
        ("items", "text"),
        [(["a"], "a"), (["a", "b"], "a ほか 1 件"), (["a", "b", "c"], "a ほか 2 件")],
    )
    def test_first_and_more_counts_the_rest(self, items, text):
        assert csb._first_and_more(items) == text

    def test_json_list_errors(self):
        with pytest.raises(csb.SandboxError):
            csb._json_list(b"\xff")
        with pytest.raises(csb.SandboxError):
            csb._json_list(b"{}")
