"""Deterministic-layer hardening of the bash-review gate.

Each class pins one family of shapes in the shared decision module
(.claude/hooks/_bash_review_common.py):

- docker / docker compose shapes that must reach the two-model AND gate
- harness writes through docker bind mounts, `docker cp` and sed `w`
- credential files that must not be safe-skipped
- the permissions.deny Bash rules mirrored into the deterministic deny
- git config injection, git execution hooks and destructive git / gh

Every positive corpus has a negative twin of everyday commands, because a false
positive in the deny layer cannot be overridden and one in the AND gate costs two
model calls on every run.
"""

import sys
import time

import pytest
from conftest import REPO_ROOT, fake_gemini, fake_run, hook_payload

HOOK = ".claude/hooks/bash-review.py"
CODEX_HOOK = ".codex/hooks/bash-review.py"

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402

FAKE_HOME = "/Users/someone"


@pytest.fixture(autouse=True)
def _fixed_home(monkeypatch):
    # Mount classification resolves `~` / `$HOME` against HOME; pin it so the
    # corpora mean the same thing on every machine and in CI (/home/runner).
    monkeypatch.setenv("HOME", FAKE_HOME)


def _risk(command: str) -> str:
    return _common.classify_high_risk(_common._split_commands(command), command)


def _harness(command: str) -> str:
    return _common.harness_write_ask_reason(_common._split_commands(command), command)


def _deny(command: str) -> tuple:
    return _common.find_deny_command(_common._split_commands(command), command)


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------

DOCKER_HIGH_RISK_CASES = [
    # compose files are not parsed: whatever they mount or grant is unseen
    ("docker compose up -d", "compose up"),
    ("docker compose up", "compose up"),
    ("docker compose run --rm web sh", "compose run"),
    ("docker compose create", "compose create"),
    ("docker-compose up -d", "compose up"),
    ("docker compose -f dev.yml -p proj up", "compose up"),
    ("docker compose --env-file .env up", "compose up"),
    ("docker compose --profile dev --project-directory x up", "compose up"),
    ("docker compose down -v", "compose down -v"),
    ("docker compose down --volumes", "compose down -v"),
    ("docker compose rm -fsv", "compose rm -v"),
    ("docker compose exec -u root db sh", "as root"),
    ("docker compose exec --user 0 db sh", "as root"),
    ("docker compose exec db psql -U app", "psql"),
    ("docker-compose exec db mysql -uroot", "mysql"),
    ("docker compose exec -T db env PGHOST=x psql", "psql"),
    ("docker compose cp web:/etc/passwd .", "compose cp"),
    ("docker compose push", "compose push"),
    # mount spellings of the host root
    ("docker run -v/:/h alpine", "host root mount"),
    ("docker run -itv /:/h alpine sh", "host root mount"),
    ("docker run -itv/:/h alpine sh", "host root mount"),
    ("docker run -v=/:/h alpine", "host root mount"),
    ("docker run -v /./:/h alpine", "host root mount"),
    ("docker run -v /etc/../:/h alpine", "host root mount"),
    ("docker run -v //:/h alpine", "host root mount"),
    ("docker run --volume=/:/h:ro alpine", "host root mount"),
    ("docker run --mount type=bind,source=/,target=/h,readonly img", "host root mount"),
    # home directory and its ancestors
    ("docker run -v ~:/h alpine", "home directory mount"),
    ("docker run -v $HOME:/h alpine", "home directory mount"),
    ("docker run -v ${HOME}:/h alpine", "home directory mount"),
    ('docker run -v "$HOME":/h alpine', "home directory mount"),
    (f"docker run -v {FAKE_HOME}:/h alpine", "home directory mount"),
    (f"docker run -v {FAKE_HOME}/:/h alpine", "home directory mount"),
    ("docker run -v ~/..:/h alpine", "home directory mount"),
    ("docker run -v /Users:/u alpine", "home directory mount"),
    ("docker run -v /home:/u alpine", "home directory mount"),
    ("docker run --mount type=bind,src=$HOME,dst=/h alpine", "home directory mount"),
    # system paths
    ("docker run -v /etc:/e alpine", "system path mount"),
    ("docker run -v /etc/sudoers.d:/s alpine", "system path mount"),
    ("docker run -v /root:/r alpine", "system path mount"),
    ("docker run -v /etc/localtime:/etc/localtime alpine", "system path mount"),
    ("docker run -v /dev:/dev alpine", "system path mount"),
    ("docker run --mount type=bind,source=/etc,target=/e alpine", "system path mount"),
    # the daemon socket (and the directories holding it)
    ("docker run -v /var/run/docker.sock:/s alpine", "docker.sock mount"),
    ("docker run -v /run/docker.sock:/s alpine", "docker.sock mount"),
    ("docker run -v /var/run:/r alpine", "docker.sock mount"),
    ("docker run -v ~/.docker/run/docker.sock:/s alpine", "docker.sock mount"),
    # credential stores under HOME, relative spellings included
    ("docker run -v ~/.ssh:/s alpine", "protected path mount"),
    ("docker run -v ~/.aws:/a alpine", "protected path mount"),
    ("docker run -v ~/.docker:/d alpine", "protected path mount"),
    ("docker run -v ~/.kube:/k alpine", "protected path mount"),
    ("docker run -v ~/.gnupg:/g alpine", "protected path mount"),
    ("docker run -v ~/.config:/c alpine", "protected path mount"),
    ("docker run -v ~/.config/gh:/c alpine", "protected path mount"),
    ("docker run -v $HOME/.netrc:/n alpine", "protected path mount"),
    ("docker run -v ~/.claude:/c:ro alpine", "protected path mount"),
    ("docker run -v ./.ssh:/s alpine", "protected path mount"),
    ("docker run -v .aws:/a alpine", "protected path mount"),
    # sources the hook cannot resolve, or that leave the working tree
    ("docker run -v ../..:/up alpine", "outside the working tree"),
    ("docker run -v $SRC:/x alpine", "unresolved mount source"),
    ('docker run -v "$(dirname x)":/x alpine', "unresolved mount source"),
    # isolation flags
    ("docker run --network=host alpine", "--network=host"),
    ("docker run --network host alpine", "--network=host"),
    ("docker run --net=host alpine", "--network=host"),
    ("docker run --network=container:web alpine", "--network=container"),
    ("docker run --userns=host alpine", "--userns=host"),
    ("docker run --ipc=host alpine", "--ipc=host"),
    ("docker run --uts host alpine", "--uts=host"),
    ("docker run --cgroupns=host alpine", "--cgroupns=host"),
    ("docker run --cap-add=SYS_PTRACE alpine", "--cap-add SYS_PTRACE"),
    ("docker run --cap-add NET_ADMIN alpine", "--cap-add NET_ADMIN"),
    ("docker run --cap-add=cap_sys_module alpine", "--cap-add SYS_MODULE"),
    ("docker run --cap-add DAC_READ_SEARCH alpine", "--cap-add DAC_READ_SEARCH"),
    ("docker run --cap-add=DAC_OVERRIDE alpine", "--cap-add DAC_OVERRIDE"),
    ("docker run --cap-add=SYS_RAWIO alpine", "--cap-add SYS_RAWIO"),
    ("docker run --cap-add=BPF alpine", "--cap-add BPF"),
    ("docker run --cap-add=PERFMON alpine", "--cap-add PERFMON"),
    ("docker run --security-opt seccomp=unconfined alpine", "unconfined"),
    ("docker run --security-opt=apparmor=unconfined alpine", "unconfined"),
    ("docker run --security-opt seccomp:unconfined alpine", "unconfined"),
    ("docker run --security-opt label=disable alpine", "unconfined"),
    ("docker run --security-opt systempaths=unconfined alpine", "unconfined"),
    ("docker run --device /dev/sda alpine", "--device"),
    ("docker run --device=/dev/fuse alpine", "--device"),
    ("docker run --device-cgroup-rule='c 1:3 mr' alpine", "--device-cgroup-rule"),
    ("docker run --volumes-from web alpine", "--volumes-from"),
    ("docker run --env-file .env app", "--env-file"),
    # a value-taking flag before the dangerous one must not end the option scan
    ("docker run --name web --privileged img", "--privileged"),
    ("docker run -e A=1 -u 0 --network host img", "--network=host"),
    ("docker run -it --rm --network host img", "--network=host"),
    ("docker container run --network=host img", "--network=host"),
    ("docker create --cap-add=SYS_PTRACE img", "docker create --cap-add"),
    # remote or alternate daemon
    ("docker -H tcp://10.0.0.1:2375 ps", "remote or alternate daemon"),
    ("docker --host=unix:///tmp/d.sock ps", "remote or alternate daemon"),
    ("docker -Hunix:///tmp/d.sock ps", "remote or alternate daemon"),
    ("docker --context prod ps", "remote or alternate daemon"),
    ("docker -c prod ps", "remote or alternate daemon"),
    ("docker --config /tmp/cfg ps", "remote or alternate daemon"),
    ("DOCKER_HOST=tcp://10.0.0.1:2375 docker ps", "remote or alternate daemon"),
    ("DOCKER_CONTEXT=prod docker compose ps", "remote or alternate daemon"),
    ("env DOCKER_HOST=tcp://x:1 docker ps", "remote or alternate daemon"),
    ("export DOCKER_HOST=tcp://10.0.0.1:2375", "remote or alternate daemon"),
    # a bare assignment carries to the next statement of the same command
    ("DOCKER_HOST=tcp://10.0.0.1:2375; docker ps", "remote or alternate daemon"),
    ("DOCKER_CONTEXT=remote\ndocker ps", "remote or alternate daemon"),
    # readonly / local, and a value set by a builtin or an expansion
    (
        "readonly DOCKER_HOST=tcp://10.0.0.1:2375; docker ps",
        "remote or alternate daemon",
    ),
    ("read DOCKER_HOST < f; docker ps", "remote or alternate daemon"),
    ("docker -H tcp://x:1 compose ps", "remote or alternate daemon"),
    # a more specific label wins; either way the AND gate is reached
    ("docker -H tcp://x:1 compose up", "compose up"),
    ("docker context use prod", "docker context change"),
    ("docker context create prod --docker host=tcp://x:1", "docker context change"),
    # destructive or host-affecting
    ("docker system prune -a --volumes", "docker system prune"),
    ("docker system prune", "docker system prune"),
    ("docker volume rm data", "docker volume rm"),
    ("docker volume prune -f", "docker volume prune"),
    ("docker image prune -a", "docker image prune -a"),
    ("docker image prune --all", "docker image prune -a"),
    ("docker rm -f web", "docker rm -f"),
    ("docker rm --force web", "docker rm -f"),
    ("docker rm -fv web", "docker rm -f"),
    ("docker container rm -f web", "docker rm -f"),
    ("docker cp web:/etc/passwd .", "docker cp"),
    ("docker cp ./x web:/x", "docker cp"),
    ("docker container cp web:/a .", "docker cp"),
    ("docker push me/app", "docker push"),
    ("docker image push me/app", "docker push"),
    ("docker login", "docker login"),
    ("docker commit web me/app", "docker commit"),
    ("docker container commit web me/app", "docker commit"),
    ("docker network connect bridge web", "docker network connect"),
    # exec as root / privileged / with a denied client inside
    ("docker exec -u 0 web sh", "docker exec as root"),
    ("docker exec -u root web sh", "docker exec as root"),
    ("docker exec --user=0:0 web id", "docker exec as root"),
    ("docker exec -u root:root web id", "docker exec as root"),
    ("docker exec -itu0 web sh", "docker exec as root"),
    ("docker exec -it -u 0 web sh", "docker exec as root"),
    ("docker exec --privileged web sh", "docker exec --privileged"),
    ("docker exec --env-file ~/.zprofile web env", "docker exec --env-file"),
    ("docker container exec -u 0 web sh", "docker exec as root"),
    ("docker exec db psql -c 'select 1'", "docker exec runs psql"),
    ("docker exec -i db mysql", "docker exec runs mysql"),
    # case-insensitive filesystems (APFS) reach the real directory
    ("docker run -v ~/.SSH:/s alpine", "protected path mount"),
    (f"docker run -v {FAKE_HOME.lower()}:/h alpine", "home directory mount"),
    ("docker run -v /users:/u alpine", "home directory mount"),
    ("docker run -v /ETC:/e alpine", "system path mount"),
    # option scan edge shapes
    ("docker run --privileged", "--privileged"),
    ("docker run -v ./docker.sock:/s alpine", "docker.sock mount"),
    ("docker run -v /srv/backup/.ssh:/s alpine", "protected path mount"),
]

DOCKER_NOT_HIGH_RISK_CASES = [
    "docker ps",
    "docker ps -a",
    "docker logs -f web",
    "docker images",
    "docker inspect web",
    "docker build -t app .",
    "docker build -f Dockerfile.dev .",
    "docker version",
    "docker info",
    "docker pull alpine",
    "docker run alpine echo hi",
    "docker run --rm alpine echo hi",
    "docker run -it --rm alpine sh",
    # the everyday bind mount of the working tree
    'docker run --rm -v "$PWD":/app node:20 npm test',
    'docker run --rm -v "$(pwd)":/app -w /app node:20 npm test',
    "docker run --rm -v ${PWD}:/app node:20 npm test",
    "docker run --rm -v `pwd`:/app node:20 npm test",
    'docker run --rm -v "$PWD/src":/app/src node:20 npm test',
    "docker run -v .:/app img",
    "docker run -v ./data:/data img",
    "docker run -v /home/me/app:/app img",
    "docker run --mount type=bind,source=/home/me,target=/data img",
    "docker run -v ~/work/app:/app img",
    "docker run -v $HOME/work/app:/app img",
    "docker run -v /tmp/x:/x img",
    "docker run -v /etc/localtime:/etc/localtime:ro img",
    # named and anonymous volumes are not host paths
    "docker run -v pgdata:/var/lib/postgresql/data postgres",
    "docker run --mount type=volume,src=data,dst=/d img",
    "docker run --mount source=data,target=/d img",
    "docker run -v /data img",
    "docker run --tmpfs /tmp img",
    "docker run --mount type=tmpfs,dst=/tmp img",
    # look-alike values
    "docker run --pid=container:web img",
    "docker run --cap-add NET_BIND_SERVICE img",
    "docker run --network mynet img",
    "docker run --network=none img",
    "docker run --security-opt no-new-privileges img",
    "docker run -p 8080:80 nginx",
    "docker run -u 1000:1000 img",
    "docker run -e MODE=host img",
    # container arguments after the image are the container's, not docker's
    "docker run img ls -v /",
    "docker run alpine grep -v foo /etc/hosts",
    "docker run img sh -c 'echo --privileged'",
    # network egress from inside a container is accepted (documented)
    "docker run alpine wget -q -O- https://example.com",
    # compose read-only / lifecycle forms
    "docker compose ps",
    "docker compose logs -f web",
    "docker compose config",
    "docker compose build",
    "docker compose pull",
    "docker compose down",
    "docker compose stop",
    "docker compose --env-file .env ps",
    "docker compose -f a.yml config",
    "docker-compose ps",
    "docker compose exec web sh",
    "docker compose exec -T web npm test",
    "docker exec -it web sh",
    "docker exec -u 1000 web id",
    "docker exec -u node web id",
    "docker exec mysql bash",
    "docker rm web",
    "docker rmi app",
    "docker stop web",
    "docker network ls",
    "docker volume ls",
    "docker volume create data",
    "docker context ls",
    "docker context inspect",
    "docker image ls",
    "docker system df",
    "docker image prune",
    "docker run --rm -- alpine echo hi",
    "docker run --privileged=false alpine",
    "docker exec web",
    "docker container",
]

# The sandbox CLI (later phases) and its containers must not be made high-risk,
# harness-asked or denied by these rules: the allow path is layered on top of them.
SANDBOX_CLI_CASES = [
    "csb exec 0123456789ab -w /work -e A=b -- npm test",
    "csb verify 0123456789ab --patch 0f1e2d -- pytest -q",
    "csb ls",
    "csb status --json",
    "csb export 0123456789ab",
    "~/.claude/hooks/claude_sandbox.py ls",
    "~/.local/bin/csb status",
    "docker exec csb-0123456789ab sh",
    "docker exec -i csb-0123456789ab sh -c 'cd /work && make'",
]


class TestDockerHighRisk:
    @pytest.mark.parametrize(("command", "expected"), DOCKER_HIGH_RISK_CASES)
    def test_dangerous_shapes_reach_the_and_gate(self, command, expected):
        label = _risk(command)
        assert expected in label, f"{command!r} -> {label!r}"

    @pytest.mark.parametrize("command", DOCKER_NOT_HIGH_RISK_CASES)
    def test_everyday_shapes_stay_on_the_single_model_path(self, command):
        assert _risk(command) == "", command

    @pytest.mark.parametrize("command", SANDBOX_CLI_CASES)
    def test_sandbox_cli_shapes_are_untouched(self, command):
        assert _risk(command) == "", command
        assert _harness(command) == "", command
        assert _deny(command) == (False, ""), command

    def test_labels_do_not_echo_command_text(self):
        # Labels reach the dialog and the summary log; a mount source, a -H value or
        # an env-file path may carry anything, so only fixed vocabulary is allowed.
        for command in (
            "docker -H tcp://secret-host:2375 ps",
            "docker run -v /secret-dir/../:/h img",
            "docker run --env-file secret-file img",
            "docker run -v $SECRET_VAR:/x img",
            "git config credential.https://example.com/secret.helper store",
        ):
            assert "secret" not in _risk(command).lower(), command

    def test_home_is_resolved_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/runner")
        assert "home directory mount" in _risk("docker run -v /home/runner:/h img")
        assert "home directory mount" in _risk("docker run -v ~:/h img")
        assert _risk(f"docker run -v {FAKE_HOME}/app:/h img") == ""


# ---------------------------------------------------------------------------
# harness writes (docker mounts, docker cp, sed w)
# ---------------------------------------------------------------------------

HARNESS_DOCKER_CASES = [
    "docker run -v ~/.claude:/c alpine sh -c 'echo x > /c/settings.json'",
    "docker run -v $HOME/.claude:/c alpine true",
    f"docker run -v {FAKE_HOME}/.claude:/c alpine true",
    "docker run -v ~/.claude/hooks:/h alpine true",
    "docker run -v ./.claude:/c alpine true",
    "docker run -v .claude/hooks:/h alpine true",
    'docker run -v "$PWD/.claude":/c alpine true',
    f"docker run --mount type=bind,src={FAKE_HOME}/.codex,dst=/x alpine true",
    "docker run -v ~/.codex:/x alpine true",
    "docker run -v ~/.gemini:/x alpine true",
    "docker run -v ~/.zshrc:/r alpine true",
    "docker run -v ~/.bashrc:/r alpine true",
    "docker run -v ./.zshrc:/r alpine true",
    "docker run -v ~/.ZSHRC:/r alpine true",
    f"docker run -v {FAKE_HOME.upper()}/.claude:/c alpine true",
    "docker run -v ~/.local/state/claude-sandbox:/s alpine true",
    "docker run -v ~/.local/bin:/b alpine true",
    "docker run -v ~/.local:/l alpine true",
    "docker run -v ~:/h alpine true",
    "docker run -v /:/host alpine true",
    "docker run -v /Users:/u alpine true",
    "docker run -itv ~/.claude:/c alpine sh",
    "docker create -v ~/.codex:/x alpine",
    "docker container run -v ~/.claude:/c alpine true",
    "docker compose run -v ~/.claude:/c web sh",
    "docker-compose run -v ~/.claude:/c web sh",
    "docker cp web:/x ~/.claude/settings.json",
    "docker cp web:/x .claude/hooks/lint.sh",
    "docker cp web:/x ~/.zshrc",
    "docker container cp web:/x ~/.codex/",
    "docker cp -a web:/x ~/.local/bin/csb",
    "docker compose cp web:/x .claude/settings.local.json",
]

HARNESS_DOCKER_NOT_A_WRITE_CASES = [
    "docker run -v ~/.claude:/c:ro alpine cat /c/settings.json",
    "docker run -v /:/host:ro alpine ls /host",
    "docker run -v ~:/h:ro,z alpine ls /h",
    f"docker run --mount type=bind,src={FAKE_HOME}/.claude,dst=/c,readonly alpine",
    f"docker run --mount type=bind,src={FAKE_HOME}/.claude,dst=/c,ro=true alpine",
    'docker run --rm -v "$PWD":/app node:20 npm test',
    "docker run -v ~/work/app:/app img",
    "docker run -v ./src:/src img",
    "docker cp ./x web:/root/.claude/settings.json",
    "docker cp web:/x ./out",
    "docker cp web:/x -",
    "docker run img cp /a /root/.claude/settings.json",
    "docker run -v $X:/c alpine true",
]

HARNESS_SED_CASES = [
    "sed -n 'w .claude/settings.local.json' f",
    "sed 's/x/y/w ~/.claude/hooks/lint.sh' f",
    "sed -e 's/a/b/gw .claude/settings.json' f",
    "sed --expression='W .claude/hooks/x' f",
    "sed --expression 'W .claude/hooks/x' f",
    "sed -n '/foo/w .claude/rules/x.md' f",
    "sed -E 's|a|b|w .claude/settings.json' f",
    "sed '1w ~/.codex/config.toml' f",
    "sed -ne 'w .claude/settings.json' f",
    "sed -n -e p -e 'w .claude/settings.json' f",
    "sed -n '$w .claude/CLAUDE.md' f",
    "sed -n -e '1,3{' -e 'w .claude/settings.json' -e '}' f",
    "sed 'y/ab/cd/;w .claude/settings.json' f",
    "sed -i '' 's/a/b/w .claude/settings.json' f",
    "sed -i .bak 's/a/b/w .claude/settings.json' f",
    "gsed -n 'w .claude/settings.json' f",
    "sed -n 'w ~/.local/state/claude-sandbox/x/attest/y.json' f",
    "sed -n -- 'w .claude/settings.json' f",
    "sed '-es/a/b/w .claude/settings.json' f",
    "sed --expression='w .claude/settings.json' f",
    "sed -n '\\#foo#w .claude/settings.json' f",
]

HARNESS_SED_NOT_A_WRITE_CASES = [
    "sed -n p .claude/settings.json",
    "sed 's/w/x/' .claude/settings.json",
    "sed -n 'w out.txt' .claude/settings.json",
    "sed 's/a/b/w /dev/stdout' f",
    "sed 's#.claude/hooks#x#g' README.md",
    "sed -n '/\\.claude\\/hooks/p' notes.md",
    "sed 's/x/.claude\\/hooks/' f",
    "sed -e 's/a/b/' -e 's/w/v/' f",
    "sed --line-length 5 -n l f",
    "sed -l 5 -n l f",
    "sed --file=prog.sed f",
    "sed 'a\\\nw .claude/settings.json' f",
]

HARNESS_STATE_PATH_CASES = [
    "echo x > ~/.local/state/claude-sandbox/abc/meta.json",
    "rm -rf ~/.local/state/claude-sandbox",
    "ln -sf /tmp/evil ~/.local/bin/csb",
    "cp evil $HOME/.local/bin/csb",
    f"tee {FAKE_HOME}/.local/state/claude-sandbox/x/attest/y.json",
    "touch .local/state/claude-sandbox/x",
    # ~/.local/bin is protected as a whole directory, not only the csb link
    "echo x > ~/.local/bin/csbx",
]

HARNESS_STATE_PATH_NOT_A_WRITE_CASES = [
    "cat ~/.local/state/claude-sandbox/x/meta.json",
    "ls ~/.local/bin",
    "echo x > ~/.local/state/claude-sandbox-old/x",
    "~/.local/bin/csb ls",
]


class TestHarnessWritesAreAsked:
    @pytest.mark.parametrize(
        "command", HARNESS_DOCKER_CASES + HARNESS_SED_CASES + HARNESS_STATE_PATH_CASES
    )
    def test_write_forms_are_asked(self, command):
        assert _harness(command) != "", command

    @pytest.mark.parametrize(
        "command",
        HARNESS_DOCKER_NOT_A_WRITE_CASES
        + HARNESS_SED_NOT_A_WRITE_CASES
        + HARNESS_STATE_PATH_NOT_A_WRITE_CASES,
    )
    def test_reads_and_lookalikes_are_not_asked(self, command):
        assert _harness(command) == "", command

    @pytest.mark.parametrize(
        "command",
        [
            "docker run -v ~/.claude:/c:ro alpine cat /c/settings.json",
            "docker run -v /:/host:ro alpine ls /host",
        ],
    )
    def test_read_only_mounts_of_protected_paths_still_reach_the_and_gate(
        self, command
    ):
        assert _risk(command) != "", command

    def test_state_and_cli_paths_are_harness_targets(self):
        for path in (
            "~/.local/state/claude-sandbox",
            "~/.local/state/claude-sandbox/x/meta.json",
            "$HOME/.local/bin/csb",
            "${HOME}/.local/bin/csb",
            f"{FAKE_HOME}/.local/state/claude-sandbox/",
        ):
            assert _common._is_harness_target(path), path

    def test_reason_is_fixed_vocabulary(self):
        reason = _harness("docker run -v ~/.claude:/secret-dir alpine true")
        assert "secret" not in reason
        assert reason.endswith("— confirm manually")


# ---------------------------------------------------------------------------
# credential files are never safe-skipped
# ---------------------------------------------------------------------------


class TestCredentialFilesAreNotSafeSkipped:
    @pytest.mark.parametrize(
        "command",
        [
            "cat .docker/config.json",
            "head .docker/config.json",
            "cat .docker//config.json",
            "cat .kube/config",
            "ls .kube",
            "ls .gnupg",
            "cat .gnupg/pubring.kbx",
            "cat .config/gcloud/configurations/config_default",
            "ls .config/gcloud",
            "cat .config//gcloud/x",
            "cat .config/./gcloud/x",
            "cat .azure/azureProfile.json",
            "ls .azure",
            "cat .config/op/config",
            "ls .config/op",
        ],
    )
    def test_credential_paths_force_review(self, command):
        assert _common._can_skip_review(command) is False, command

    @pytest.mark.parametrize(
        "command",
        [
            "cat .dockerignore",
            "cat docker-compose.yml",
            "cat Dockerfile",
            "cat .config/app.yml",
            "ls .configs",
            "cat src/kube.ts",
            "cat docs/azure.md",
            "cat .operator.yml",
        ],
    )
    def test_lookalikes_stay_fast(self, command):
        assert _common._can_skip_review(command) is True, command


# ---------------------------------------------------------------------------
# permissions.deny mirror
# ---------------------------------------------------------------------------

DENY_MIRROR_CASES = [
    ("/usr/bin/printenv KEY", "printenv"),
    ("(printenv KEY)", "printenv"),
    ("printenv", "printenv"),
    ("env printenv HOME", "printenv"),
    ("command printenv HOME", "printenv"),
    ("ls && printenv KEY", "printenv"),
    ("/opt/homebrew/bin/gh auth token", "gh auth token"),
    ("gh auth token", "gh auth token"),
    ("(gh auth token)", "gh auth token"),
    ("env gh auth token", "gh auth token"),
    ("gh auth token -h github.com", "gh auth token"),
    ("gh auth status --show-token", "gh auth status --show-token"),
    ("gh auth status -t", "gh auth status --show-token"),
    ("gh auth status -h github.com -t", "gh auth status --show-token"),
    ("git credential fill", "git credential"),
    ("git -C . credential fill", "git credential"),
    ("/usr/bin/git credential get", "git credential"),
    ("(git credential fill)", "git credential"),
    ("git -c credential.helper= credential fill", "git credential"),
    ("git credential-osxkeychain get", "git credential"),
    ("git credential-store get", "git credential"),
    ("security find-generic-password -w -s x", "security find-generic-password"),
    ("/usr/bin/security find-internet-password -w", "security find-internet-password"),
    ("security find-generic-password -s x", "security find-generic-password"),
    ("security dump-keychain", "security dump-keychain"),
    ("security -q find-generic-password -a me -w", "security find-generic-password"),
    ("security -p x find-generic-password -w", "security find-generic-password"),
    ("(security find-generic-password -w)", "security find-generic-password"),
    # environment dumps
    ("env", "env"),
    ("/usr/bin/env", "env"),
    ("(env)", "env"),
    ("( env )", "env"),
    ("env -0", "env"),
    ("env FOO=1", "env"),
    ("nohup env", "env"),
    ("ls; env", "env"),
    ("set", "set"),
    ("(set)", "set"),
    ("( set )", "set"),
    ("{ set; }", "set"),
    ("export", "export"),
    ("export -p", "export"),
    ("(export -p)", "export"),
    ("builtin export -p", "export"),
    ("declare", "declare"),
    ("declare -p", "declare"),
    ("declare -x", "declare"),
    ("typeset", "typeset"),
    ("typeset -p", "typeset"),
    # host power and database clients
    ("shutdown -h now", "shutdown"),
    ("/sbin/reboot", "reboot"),
    ("(reboot)", "reboot"),
    ("psql -U postgres", "psql"),
    ("/opt/homebrew/bin/psql -c 'select 1'", "psql"),
    ("mysql -uroot", "mysql"),
    ("mongod --dbpath x", "mongod"),
]

DENY_MIRROR_NOT_DENIED_CASES = [
    "env FOO=1 make",
    "/usr/bin/env python3 script.py",
    "env -i PATH=/usr/bin make",
    "env -u FOO make",
    "set -e",
    "set -euo pipefail",
    "set -o pipefail",
    "set +x",
    "set --",
    "export FOO=1",
    'export PATH="$PATH:/x"',
    "export -n FOO",
    "declare -A m",
    "declare -a arr=(1 2)",
    "declare -x FOO=1",
    "declare -p HOME",
    "typeset -i n=1",
    "gh auth status",
    "gh auth login",
    "gh auth refresh",
    "gh pr list",
    "gh api user",
    "git config credential.helper",
    "git help credential",
    "git log --grep credential",
    "security find-certificate -a",
    "security list-keychains",
    "command -v printenv",
    "command -v psql",
    "which psql",
    "brew install postgresql",
    "man psql",
    "echo set",
    "echo env",
    "git commit -m 'set'",
    "ENV FOO=bar",
    "cat > Dockerfile <<'EOF'\nFROM alpine\nENV FOO=bar\nEOF",
    "docker compose exec db psql",
    "docker run --rm postgres psql --version",
    "pg_dump db",
]


class TestDenyMirror:
    @pytest.mark.parametrize(("command", "expected"), DENY_MIRROR_CASES)
    def test_denied_forms_are_denied_however_they_are_reached(self, command, expected):
        matched, name = _deny(command)
        assert matched, command
        assert name == expected, (command, name)

    @pytest.mark.parametrize("command", DENY_MIRROR_NOT_DENIED_CASES)
    def test_everyday_forms_are_not_denied(self, command):
        assert _deny(command) == (False, ""), command

    def test_named_declare_print_reaches_the_and_gate(self):
        assert "declare -p" in _risk("declare -p GITHUB_TOKEN")
        assert "declare -p" in _risk("typeset -p GITHUB_TOKEN")

    def test_every_new_deny_executable_has_a_settings_backstop(self):
        # The config-wiring suite pins DENY_EXECUTABLES ⊆ Bash(x:*) deny rules; keep
        # the mirror inside that set so it never needs a settings change first.
        import json

        settings = json.loads(
            (REPO_ROOT / ".claude/settings.json").read_text(encoding="utf-8")
        )
        deny = set(settings["permissions"]["deny"])
        for exe in ("printenv", "shutdown", "reboot", "psql", "mysql", "mongod"):
            assert exe in _common.DENY_EXECUTABLES, exe
            assert f"Bash({exe}:*)" in deny, exe


# ---------------------------------------------------------------------------
# git config injection, execution hooks, destructive git / gh
# ---------------------------------------------------------------------------

GIT_HIGH_RISK_CASES = [
    # config that makes a later safe-skipped `git status` run a program
    ("git config core.fsmonitor 'touch /tmp/x'", "git config"),
    ("git config core.fsmonitor ./evil.sh", "git config"),
    ("git config --global core.hooksPath /tmp/h", "git config"),
    ("git config --local core.sshCommand 'sh -c x'", "git config"),
    ("git config core.pager 'sh -c evil'", "git config"),
    ("git config core.editor 'sh -c x'", "git config"),
    ("git config alias.x '!sh -c id'", "git config"),
    ("git config --add alias.st '!rm -rf .'", "git config"),
    ("git config alias.st '-c core.pager=sh status'", "git config"),
    ("git config diff.pdf.textconv 'sh x'", "git config"),
    ("git config diff.external x", "git config"),
    ("git config filter.lfs.smudge 'sh x'", "git config"),
    ("git config filter.x.clean x", "git config"),
    ("git config filter.x.process x", "git config"),
    ("git config merge.x.driver x", "git config"),
    ("git config credential.helper '!sh x'", "git config"),
    ("git config credential.https://example.com.helper x", "git config"),
    ("git config --global include.path /tmp/evil", "git config"),
    ("git config includeIf.gitdir:~/w/.path /tmp/evil", "git config"),
    ("git config pager.log 'sh -c x'", "git config"),
    ("git config sequence.editor x", "git config"),
    ("git config gpg.program x", "git config"),
    ("git config protocol.ext.allow always", "git config"),
    ("git config -f .git/config core.fsmonitor x", "git config"),
    ("git config --file=.git/config core.hooksPath h", "git config"),
    ("git config --file .git/config core.hooksPath h", "git config"),
    ("git config set core.hooksPath h", "git config"),
    ("git config set --global core.pager 'sh x'", "git config"),
    ("git config --replace-all core.pager 'sh x'", "git config"),
    ("git config CORE.HOOKSPATH h", "git config"),
    ("git config --type=path core.hooksPath h", "git config"),
    ("git config core.hooksPath .githooks", "git config"),
    ("git -C repo config core.hooksPath h", "git config"),
    ("git -c core.fsmonitor='touch /tmp/x' status", "git -c"),
    ("git -c alias.x='!id' x", "git -c"),
    ("git -c core.pager='sh -c id' log", "git -c"),
    ("git -c core.hooksPath=/tmp/h commit -m x", "git -c"),
    ("git -C repo -c core.hooksPath=/tmp/h commit -m x", "git -c"),
    ("git --config-env=core.sshCommand=EVIL fetch", "git -c"),
    ("git --config-env core.pager=EVIL log", "git -c"),
    ("git --config-env=alias.x=EVIL x", "git -c"),
    (
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=x git status",
        "git config via environment",
    ),
    (
        "GIT_CONFIG_PARAMETERS=\"'core.pager'='sh'\" git log",
        "git config via environment",
    ),
    (
        "GIT_CONFIG_PARAMETERS=\"'user.name'='x' 'core.hooksPath=/tmp/h'\" git log",
        "git config via environment",
    ),
    ("GIT_CONFIG_PARAMETERS=unparsable git log", "git config via environment"),
    ("GIT_CONFIG_GLOBAL=/tmp/evil git status", "git config via environment"),
    ("GIT_SSH_COMMAND='sh -c x' git fetch", "git command via environment"),
    ("GIT_EXTERNAL_DIFF=x git diff", "git command via environment"),
    ("GIT_PAGER='sh -c id' git log", "git command via environment"),
    # a known pager / editor whose arguments run a command or open a file
    ("git -c core.editor=\"vim -c '!id'\" status", "git -c"),
    ('GIT_PAGER="less +!id" git log', "git command via environment"),
    ("git -c core.pager='less -oFILE' log", "git -c"),
    ("git -c core.pager='less -R -O log' log", "git -c"),
    ("git config core.pager 'less -k keys'", "git config"),
    ("GIT_PAGER='less --use-color' git log", "git command via environment"),
    ("GIT_EDITOR='vim -S x' git commit", "git command via environment"),
    ("git -c core.editor='vim -u x' commit", "git -c"),
    ("GIT_EDITOR='vim --cmd x' git commit", "git command via environment"),
    (
        "GIT_SEQUENCE_EDITOR='nvim -c x' git rebase -i main",
        "git command via environment",
    ),
    ("GIT_PAGER='cat -v' git log", "git command via environment"),
    # a pager / editor that is benign bare, with an argument it does not allow
    ("git -c core.editor='nano -s x' commit", "git -c"),
    ("GIT_EDITOR='emacs --eval x' git commit", "git command via environment"),
    ("git -c core.pager='delta --pager x' log", "git -c"),
    ("GIT_EDITOR='code --wait --new-window' git commit", "git command via environment"),
    ("git -c core.pager='more +/x' log", "git -c"),
    # git (and man, psql, crontab …) fall back to PAGER / EDITOR / VISUAL, and less
    # reads LESS and runs LESSOPEN / LESSCLOSE; the lesskey variables rebind keys
    ('PAGER="less +!id" git log', "pager / editor via environment"),
    ('EDITOR="vim -c x" git commit', "pager / editor via environment"),
    ("VISUAL=x git commit", "pager / editor via environment"),
    ("PAGER='less +!id' man ls", "pager / editor via environment"),
    ("export PAGER='less +!id'", "pager / editor via environment"),
    ("declare -x EDITOR='vim -c x'", "pager / editor via environment"),
    ("LESS=+!id git log", "pager / editor via environment"),
    ("LESS='-R +G' git log", "pager / editor via environment"),
    ("LESS=-oFILE git log", "pager / editor via environment"),
    (
        'LESSOPEN="|id %s" git -c core.pager=less log',
        "pager / editor via environment",
    ),
    ("LESSCLOSE=x git log", "pager / editor via environment"),
    ("LESSKEY=x git log", "pager / editor via environment"),
    ("LESSKEYIN=x git log", "pager / editor via environment"),
    ("LESSKEY_CONTENT=x git log", "pager / editor via environment"),
    ("LESSECHO=x git log", "pager / editor via environment"),
    ("export GIT_SSH_COMMAND='sh -c x'", "git command via environment"),
    ("env GIT_ASKPASS=x git fetch", "git command via environment"),
    # a bare assignment (a statement of its own) is a shell variable that the next
    # statement of the same command sees, and an exported name's new value reaches
    # its environment: judged like the prefix and `export` forms above
    ("PAGER='less +!id'; git -p log", "pager / editor via environment"),
    ("GIT_PAGER='sh -c id'; git -p log", "git command via environment"),
    ("GIT_EDITOR='sh -c id'; git commit", "git command via environment"),
    ("EDITOR='sh -c id'\ngit commit", "pager / editor via environment"),
    ("PAGER='less +!id'", "pager / editor via environment"),
    ("GIT_SSH_COMMAND='sh -c id'; git fetch", "git command via environment"),
    ("GIT_CONFIG_GLOBAL=/tmp/x; git status", "git config via environment"),
    ("A=1 LESSOPEN='|id %s'; git log", "pager / editor via environment"),
    # readonly and local assign like declare / typeset (zsh's local works outside a
    # function too); the value is judged by the same rule
    ("readonly PAGER='less +!id'; git -p log", "pager / editor via environment"),
    ("local GIT_EDITOR='sh -c id'; git commit", "git command via environment"),
    # a value no static reading can know (read / printf -v / mapfile / readarray /
    # `${NAME:=…}` / `${NAME=…}`): a name the rules above judge is labelled whatever
    # the value
    ("read PAGER <<< 'less +!id'; git -p log", "pager / editor via environment"),
    ("read -r -t 5 X PAGER < f; git -p log", "pager / editor via environment"),
    ("read -a EDITOR < f; git commit", "pager / editor via environment"),
    ("printf -v PAGER %s 'less +!id'; git -p log", "pager / editor via environment"),
    ("printf -vGIT_EDITOR %s x; git commit", "git command via environment"),
    ("mapfile -t EDITOR < f; git commit", "pager / editor via environment"),
    ("readarray LESSOPEN < f; git log", "pager / editor via environment"),
    (": ${PAGER:='less +!id'}; git -p log", "pager / editor via environment"),
    ("echo ${GIT_SSH_COMMAND=x}; git fetch", "git command via environment"),
    ("X=${GIT_EDITOR:=x}; git commit", "git command via environment"),
    ("read GIT_CONFIG_KEY_0 <<< core.pager; git log", "git config via environment"),
    ("read LESS < f; git log", "pager / editor via environment"),
    # a bare assignment beside a reserved word, a brace or a redirection
    (
        "if true; then PAGER='less +!id'; fi; git -p log",
        "pager / editor via environment",
    ),
    ("{ PAGER='less +!id'; }; git -p log", "pager / editor via environment"),
    (
        "for i in 1; do PAGER='less +!id'; done; git -p log",
        "pager / editor via environment",
    ),
    ("PAGER='less +!id' >/dev/null; git -p log", "pager / editor via environment"),
    # zsh: print -v, and the `${NAME::=word}` expansion
    ("print -v PAGER 'less +!id'; git -p log", "pager / editor via environment"),
    (": ${PAGER::='less +!id'}; git -p log", "pager / editor via environment"),
    # an array element ($PAGER is element 0) and a loop variable
    ("printf -v 'PAGER[0]' %s x; git -p log", "pager / editor via environment"),
    ("read 'PAGER[0]' < f; git -p log", "pager / editor via environment"),
    ("for PAGER in 'less +!id'; do git -p log; done", "pager / editor via environment"),
    # already high-risk before the environment check, and the label stays: an
    # unquoted `[0]` is a glob that cannot be resolved, and `select` is not a
    # command the wrappers know
    ("PAGER[0]='less +!id'; git -p log", "wrapped command"),
    ("select EDITOR in 'sh -c id'; do git commit; done", "wrapped command"),
    # the expansion is matched on the raw text, so a quoted one counts too (the
    # judgement falls to the AND gate: README)
    ("echo '${PAGER:=x}'", "pager / editor via environment"),
    # zsh's print takes -v inside a bundle of flags (`-rv NAME`, `-rvNAME`)
    ("print -rv PAGER 'less +!id'; git -p log", "pager / editor via environment"),
    ("print -nv PAGER x; git -p log", "pager / editor via environment"),
    ("print -rvPAGER x; git -p log", "pager / editor via environment"),
    # a subshell whose `(` sticks to the assignment
    ("(PAGER='less +!id'; git -p log)", "pager / editor via environment"),
    ("(PAGER='less +!id' git -p log)", "pager / editor via environment"),
    # getopts sets its variable to the option letter it read
    ("getopts a PAGER; git -p log", "pager / editor via environment"),
    # execution hooks
    ("git rebase -x 'make test' main", "git rebase --exec"),
    ("git rebase --exec 'make' main", "git rebase --exec"),
    ("git rebase --exec=make main", "git rebase --exec"),
    ("git rebase -i -x make main", "git rebase --exec"),
    ("git rebase -ix make main", "git rebase --exec"),
    ("git rebase -xmake main", "git rebase --exec"),
    ("git rebase --exe make main", "git rebase --exec"),
    ("git bisect run make test", "git bisect run"),
    ("git submodule foreach 'git pull'", "git submodule foreach"),
    ("git submodule --quiet foreach x", "git submodule foreach"),
    ("git -C x submodule foreach x", "git submodule foreach"),
    ("git clone --upload-pack='touch /tmp/x' a b", "git --upload-pack"),
    ("git clone -u sh a b", "git --upload-pack"),
    ("git fetch --upload-pack=x origin", "git --upload-pack"),
    ("git ls-remote --upload-pack=x origin", "git --upload-pack"),
    ("git push --receive-pack=x origin", "git --upload-pack"),
    ("git push --exec=x origin", "git --upload-pack"),
    ("git archive --exec=x --remote=r HEAD", "git --upload-pack"),
    ("git --exec-path=/tmp/x status", "git --exec-path"),
    ("git filter-branch --tree-filter x HEAD", "git filter-branch"),
    ("git difftool -x 'sh' HEAD", "git difftool --extcmd"),
    ("git difftool --extcmd=x", "git difftool --extcmd"),
    ("git grep -Oevil foo", "git grep --open-files-in-pager"),
    ("git grep --open-files-in-pager=x foo", "git grep --open-files-in-pager"),
    # worktree discard and other destructive forms
    ("git checkout -- .", "git checkout"),
    ("git checkout .", "git checkout"),
    ("git checkout HEAD -- .", "git checkout"),
    ("git checkout ./", "git checkout"),
    ("git checkout :/", "git checkout"),
    ("git checkout -- '*'", "git checkout"),
    ("git checkout -f main", "git checkout"),
    ("git checkout --force main", "git checkout"),
    ("git restore .", "git restore"),
    ("git restore -W .", "git restore"),
    ("git restore --staged --worktree .", "git restore"),
    ("git restore -SW .", "git restore"),
    ("git restore -s HEAD~1 .", "git restore"),
    ("git restore --source=HEAD :/", "git restore"),
    ("git branch -D feature", "git branch -D"),
    ("git branch --delete --force feature", "git branch -D"),
    ("git branch -df feature", "git branch -D"),
    ("git branch -d -f feature", "git branch -D"),
    ("git branch -D -r origin/x", "git branch -D"),
    ("git stash drop", "git stash drop"),
    ("git stash drop stash@{1}", "git stash drop"),
    ("git stash clear", "git stash clear"),
    ("gh repo delete me/repo --yes", "gh repo delete"),
    ("gh release delete v1.0 -y", "gh release delete"),
    ("gh release delete-asset v1 a.zip", "gh release delete-asset"),
]

GIT_NOT_HIGH_RISK_CASES = [
    "git status",
    "git log --oneline",
    "git config user.name x",
    "git config --global user.email a@b.c",
    "git config --get core.hooksPath",
    "git config --get-all alias.x",
    "git config --get-regexp alias",
    "git config core.hooksPath",
    "git config get core.pager",
    "git config --list",
    "git config -l",
    "git config list --show-origin",
    "git config --unset core.hooksPath",
    "git config unset core.hooksPath",
    "git config --global --unset-all credential.helper",
    "git config alias.co checkout",
    "git config alias.st 'status -sb'",
    "git config core.fsmonitor false",
    "git config core.fsmonitor true",
    "git config core.pager cat",
    "git config core.pager 'less -FRX'",
    "git config core.editor vim",
    "git config pull.rebase true",
    "git -c core.pager=cat log",
    "git -c color.ui=always log",
    "git -c core.editor=true rebase --continue",
    "git -c user.name=x commit -m y",
    "GIT_PAGER=cat git log",
    "GIT_EDITOR=true git commit --amend --no-edit",
    # the bare pager / editor forms, as `-c` and as the environment
    "git -c core.pager=true log",
    "git -c core.pager='less -R' log",
    "git -c core.pager='less -FRXSKi' log",
    "git -c core.pager= log",
    "git -c core.editor=vim commit",
    "git config core.editor nvim",
    "GIT_PAGER='less -R' git log",
    "GIT_PAGER='less -R -F -X -S -K -i' git log",
    "GIT_EDITOR=vim git commit",
    # every pager / editor name is benign bare; code also takes --wait / -w
    "GIT_EDITOR=: git rebase --continue",
    "GIT_EDITOR=true git commit",
    "git -c core.editor=false commit",
    "git -c core.pager=delta log",
    "git -c core.pager=more log",
    "GIT_PAGER=most git log",
    "GIT_EDITOR=nano git commit",
    "git config core.editor emacs",
    "git -c core.editor='code --wait' commit",
    "GIT_EDITOR='code -w' git commit",
    # the fallbacks and the less variables in their everyday forms
    "PAGER=cat git log",
    "PAGER= git log",
    "export PAGER=less",
    # bare assignments: the same names and the same benign values as above, and the
    # names the environment check does not judge (unchanged by the bare form)
    "PAGER=cat; git log",
    "EDITOR=vim\ngit commit",
    "GIT_EDITOR=:; git rebase --continue",
    "GIT_CONFIG_GLOBAL=/dev/null; git status",
    "X=1; git status",
    "HOME=/tmp/x; git status",
    "PATH=/tmp/x:$PATH; git status",
    # readonly / local with a name the rules do not judge, or a benign value; the
    # value-setting builtins and expansions on a name the rules do not judge
    "readonly X=1",
    "readonly PAGER=cat; git log",
    "local EDITOR=vim; git commit",
    "read line",
    "read -r a b < f",
    "printf -v out %s x",
    "printf '%s' PAGER",
    ": ${FOO:=1}",
    "echo ${HOME:=/x}",
    "mapfile -t lines < f",
    # the same shapes on names the rules do not judge, or with a benign value
    "if true; then X=1; fi; git log",
    "{ PAGER=cat; }; git log",
    "PAGER=cat >/dev/null; git log",
    "print -v out x",
    ": ${FOO::=1}",
    "printf -v 'arr[0]' %s x",
    "read 'arr[0]' < f",
    "for f in a b; do git add $f; done",
    "print -rv out x",
    "(PAGER=cat git log)",
    "(X=1; git status)",
    "getopts ab opt",
    "EDITOR=vim git commit",
    "VISUAL='code --wait' git commit",
    "LESS=FRX git log",
    "LESS=-R git log",
    "LESS='-R -F -X' git log",
    "LESSOPEN= git log",
    "GIT_SEQUENCE_EDITOR=vi git rebase -i main",
    "GIT_CONFIG_NOSYSTEM=1 git status",
    "GIT_CONFIG_GLOBAL=/dev/null git status",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x git commit",
    "GIT_CONFIG_PARAMETERS=\"'user.name'='x'\" git log",
    "git -c core.fsmonitor status",
    "git -c alias.x status",
    "git checkout feature-branch",
    "git checkout -b new",
    "git checkout -b fix/x main",
    "git checkout -- src/app.py",
    "git checkout main -- README.md",
    "git restore --staged file",
    "git restore --staged .",
    "git restore -S .",
    "git restore src/app.py",
    "git branch -d merged",
    "git branch feature",
    "git branch -m old new",
    "git stash",
    "git stash list",
    "git stash pop",
    "git stash push -m x",
    "git stash show -p",
    "git rebase main",
    "git rebase -i main",
    "git rebase --continue",
    "git rebase --empty=drop main",
    "git rebase -X theirs main",
    "git rebase -s ours main",
    "git bisect start",
    "git bisect good",
    "git submodule update --init",
    "git submodule status",
    "git clone https://github.com/a/b",
    "git fetch origin",
    "git pull --unshallow",
    "git push -u origin main",
    "git grep -n foo",
    "git difftool HEAD",
    "gh repo view",
    "gh repo clone a/b",
    "gh release list",
    "gh release create v1",
]


class TestGitHighRisk:
    @pytest.mark.parametrize(("command", "expected"), GIT_HIGH_RISK_CASES)
    def test_dangerous_git_reaches_the_and_gate(self, command, expected):
        label = _risk(command)
        assert expected in label, f"{command!r} -> {label!r}"

    @pytest.mark.parametrize("command", GIT_NOT_HIGH_RISK_CASES)
    def test_everyday_git_stays_on_the_single_model_path(self, command):
        assert _risk(command) == "", command

    @pytest.mark.parametrize(
        "command",
        ["git reset --hard", "git clean -fd", "git push --force origin main"],
    )
    def test_existing_destructive_labels_are_kept(self, command):
        assert _risk(command) != "", command

    @pytest.mark.parametrize(
        "word", ["[", "a["], ids=["brackets", "name-then-brackets"]
    )
    def test_a_subscripted_name_is_matched_in_linear_time(self, word):
        # Every argument of read / mapfile / getopts / for / printf -v is checked for
        # a trailing `[subscript]`. The check must stay linear in the argument's
        # length: the CPU time for four times the input stays well under the x16 of
        # a quadratic one (process_time, not the wall clock).
        def cost(n: int) -> float:
            arg = word[:-1] + "[" * n
            start = time.process_time()
            for _ in range(5):
                _common._assigned_env_label("", ["read", arg])
            return time.process_time() - start

        small, big = cost(20_000), cost(80_000)
        assert big < max(0.05, 8 * small), (small, big)


# ---------------------------------------------------------------------------
# End to end through both entry points
# ---------------------------------------------------------------------------


def _blocked(res, hook) -> bool:
    return res.exit_code == 2 if hook == CODEX_HOOK else res.decision != "allow"


def _text(res, hook) -> str:
    return res.stderr if hook == CODEX_HOOK else res.reason


@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestEntryPoints:
    @pytest.mark.parametrize(
        "command",
        [
            "/usr/bin/printenv KEY",
            "(printenv KEY)",
            "/opt/homebrew/bin/gh auth token",
            "git -C . credential fill",
            "security find-generic-password -w -s x",
        ],
    )
    def test_deny_mirror_blocks_without_a_model(self, run_hook, hook, command):
        res = run_hook(hook, hook_payload(command))
        assert _blocked(res, hook)
        if hook == HOOK:
            assert res.decision == "deny"
        assert "Blocked dangerous command" in _text(res, hook)

    @pytest.mark.parametrize(
        "command",
        [
            "docker run -v ~/.claude:/c alpine true",
            "sed -n 'w .claude/settings.local.json' f",
            "docker cp web:/x ~/.claude/settings.json",
            # A read-write host-root mount can rewrite the harness too, so it is a
            # static ask, not the AND gate.
            "docker run -v /:/host alpine true",
        ],
    )
    def test_harness_writes_ask_without_a_model(self, run_hook, hook, command):
        res = run_hook(hook, hook_payload(command))
        assert _blocked(res, hook)
        assert "writes to the agent harness configuration" in _text(res, hook)

    @pytest.mark.parametrize(
        "command",
        [
            "docker compose up -d",
            "docker run --network=host alpine",
            "docker run -v /:/host:ro alpine ls /host",
            "git config core.hooksPath /tmp/h",
            "git checkout -- .",
            "git -c core.editor=\"vim -c '!id'\" status",
            'GIT_PAGER="less +!id" git log',
            'PAGER="less +!id" git log',
            'LESSOPEN="|id %s" git -c core.pager=less log',
            # bare assignments, on the same line and on the line before
            "PAGER='less +!id'; git -p log",
            "GIT_EDITOR='sh -c id'\ngit commit",
            "DOCKER_HOST=tcp://10.0.0.1:2375; docker ps",
            # readonly, and values no static reading can know
            "readonly PAGER='less +!id'; git -p log",
            "read PAGER <<< 'less +!id'; git -p log",
            ": ${GIT_EDITOR:='sh -c id'}; git commit",
            # a bare assignment inside a compound, an array element, a loop
            # variable (an unquoted `PAGER[0]=…` is asked earlier, by the harness
            # check: its command word is a glob)
            "if true; then PAGER='less +!id'; fi; git -p log",
            "{ PAGER='less +!id'; }; git -p log",
            "printf -v 'PAGER[0]' %s x; git -p log",
            "for PAGER in 'less +!id'; do git -p log; done",
            # a subshell's `(` stuck to the assignment, zsh's bundled print -v
            "(PAGER='less +!id'; git -p log)",
            "print -rv PAGER 'less +!id'; git -p log",
        ],
    )
    def test_high_risk_needs_both_models(self, run_hook, hook, command):
        # Gemini ALLOW alone would allow on the single-model path; Codex ASK must
        # stop it, which only happens on the AND gate.
        res = run_hook(
            hook,
            hook_payload(command),
            urlopen=fake_gemini("ALLOW"),
            run=fake_run(stdout="ASK"),
        )
        assert _blocked(res, hook)
        assert "High-risk" in _text(res, hook)

    @pytest.mark.parametrize(
        "command",
        [
            'docker run --rm -v "$PWD":/app node:20 npm test',
            "docker compose logs web",
            "git config user.name x",
            "git checkout feature-branch",
            "git -c core.pager='less -R' log",
            "GIT_EDITOR=: git rebase --continue",
        ],
    )
    def test_everyday_commands_keep_the_single_model_path(
        self, run_hook, hook, command
    ):
        # No fake codex: reaching it would raise and turn into a fail-closed ask.
        res = run_hook(hook, hook_payload(command), urlopen=fake_gemini("ALLOW"))
        assert not _blocked(res, hook), _text(res, hook)
