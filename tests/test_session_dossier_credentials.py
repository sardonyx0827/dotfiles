"""session-dossier share mode: credentials that arrive as CLI flags or URL userinfo.

The shared secret scanners are tuned for precision: they stop at the first "@"
of a URL and know no short flag (`-p`, `-u`, `-a`), because a false hit there
blocks a command. A shared page needs recall, so the redactor carries its own
rules for these forms. Two things decide whether they are safe to ship:

* the URL rule runs before the shared scanners (the scanner would otherwise cut
  the userinfo at the first "@" and leave the rest of the password behind);
* every flag rule is tied to its own command, because `-p` is a password for
  mysql and sshpass and a port for ssh and redis-cli, and `-u` is a login for
  curl and a user for sudo and docker.

Exact-output cases run with no hook scanners, so they pin these rules and
nothing else; the same cases then run against the real scanners, where only
the absence of the secret is asserted (the hook owns its own rewriting).
"""

import html
import importlib.util
import sys
import time

import pytest
from conftest import REPO_ROOT
from dossier_fixtures import SessionBuilder

SKILL_DIR = REPO_ROOT / ".claude/skills/session-dossier"
sys.path.insert(0, str(SKILL_DIR))

import dossier_link  # noqa: E402
import dossier_locate  # noqa: E402
import dossier_redact  # noqa: E402
import dossier_render  # noqa: E402
import dossier_scope  # noqa: E402

HOME = "/Users/tester"
PW = "Zq7vXk2mPw9"
PW_AT = "Sup3r@Secret99"
# A secret key as a cloud console prints it: base64 with slashes. The user is
# shaped like an access key id, assembled at runtime so the scanners' own
# access-key pattern does not flag this file.
PW_SLASH = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"
R = dossier_redact.REDACTED
# Assembled at runtime so that no URL with a password in it sits in the source,
# where the repo's own secret scanners would (rightly) flag it.
SEP = ":" + "//"


def redactor(*, bare=True):
    """A redactor; `bare` drops the hook scanners so only this module's rules run."""
    return dossier_redact.Redactor(home=HOME, authors=[], scanners=[] if bare else None)


# (text, expected) with PW standing for the secret and R for its replacement.
SECRET_CASES = [
    # -- curl -u / --user -------------------------------------------------
    (f"curl -u admin:{PW} https://x.test", f"curl -u admin:{R} https://x.test"),
    (f"curl --user u:{PW} https://x.test", f"curl --user u:{R} https://x.test"),
    (f"curl --user=u:{PW} https://x.test", f"curl --user=u:{R} https://x.test"),
    (f"curl -uadmin:{PW} https://x.test", f"curl -uadmin:{R} https://x.test"),
    (f"curl -sSu admin:{PW} https://x.test", f"curl -sSu admin:{R} https://x.test"),
    (
        f"curl -H 'Accept: json' -X POST -u admin:{PW} https://x.test",
        f"curl -H 'Accept: json' -X POST -u admin:{R} https://x.test",
    ),
    (
        f"/usr/bin/curl -u a:{PW} https://x.test",
        f"/usr/bin/curl -u a:{R} https://x.test",
    ),
    (f"curl -u 'admin:{PW}' https://x.test", f"curl -u 'admin:{R}' https://x.test"),
    (f'curl -u "admin:{PW}" https://x.test', f'curl -u "admin:{R}" https://x.test'),
    (
        'curl -u "admin:my pass phrase" https://x.test',
        f'curl -u "admin:{R}" https://x.test',
    ),
    # the spelling a JSON dump gives a double-quoted shell word
    (
        f'curl -u \\"admin:{PW}\\" https://x.test',
        f'curl -u \\"admin:{R}\\" https://x.test',
    ),
    ("curl -u admin:p@ss,w0rd! https://x.test", f"curl -u admin:{R} https://x.test"),
    (f"curl -u :{PW} https://x.test", f"curl -u :{R} https://x.test"),
    (
        f"curl --proxy-user pu:{PW} https://x.test",
        f"curl --proxy-user pu:{R} https://x.test",
    ),
    (f"curl -u 'admin:{PW}", f"curl -u 'admin:{R}"),
    # -- mysql family: only an attached -p is a password ------------------
    (f"mysql -u root -p{PW} db", f"mysql -u root -p{R} db"),
    (f"mysql -p{PW}", f"mysql -p{R}"),
    (f"mysql -u root -p'{PW}' db", f"mysql -u root -p'{R}' db"),
    ('mariadb -u root -p"my pw" db', f'mariadb -u root -p"{R}" db'),
    (
        f"mysqldump -h db -p{PW} --all-databases",
        f"mysqldump -h db -p{R} --all-databases",
    ),
    (f"mysqladmin -u root -p{PW} status", f"mysqladmin -u root -p{R} status"),
    # -- sshpass -----------------------------------------------------------
    (f"sshpass -p {PW} ssh host", f"sshpass -p {R} ssh host"),
    (f"sshpass -p '{PW}' ssh host", f"sshpass -p '{R}' ssh host"),
    ('sshpass -p "my pass phrase" ssh host', f'sshpass -p "{R}" ssh host'),
    (f"sshpass -p{PW} ssh host", f"sshpass -p{R} ssh host"),
    (f"sshpass -v -p {PW} ssh host", f"sshpass -v -p {R} ssh host"),
    # the ssh port after the password is a port, on the first pass and again
    # on the renderer's second scrub of the same string
    (f"sshpass -p '{PW}' ssh -p 2222 host", f"sshpass -p '{R}' ssh -p 2222 host"),
    (f"sshpass -p {R} ssh -p 2222 host", f"sshpass -p {R} ssh -p 2222 host"),
    # -- redis-cli ---------------------------------------------------------
    (
        f"redis-cli -h cache -p 6379 -a {PW} ping",
        f"redis-cli -h cache -p 6379 -a {R} ping",
    ),
    (f"redis-cli -a '{PW}' ping", f"redis-cli -a '{R}' ping"),
    (f"redis-cli --pass {PW} ping", f"redis-cli --pass {R} ping"),
    # -- docker login ------------------------------------------------------
    (
        f"docker login -u user -p {PW} registry.test",
        f"docker login -u user -p {R} registry.test",
    ),
    (f"docker login -p{PW} registry.test", f"docker login -p{R} registry.test"),
    (f"docker login -p '{PW}' reg.test", f"docker login -p '{R}' reg.test"),
    (
        f"docker login --password {PW} reg.test",
        f"docker login --password {R} reg.test",
    ),
    (
        f"docker login --password={PW} reg.test",
        f"docker login --password={R} reg.test",
    ),
    (
        'docker login --password "my pass phrase" reg.test',
        f'docker login --password "{R}" reg.test',
    ),
    (f"podman login -p {PW} reg.test", f"podman login -p {R} reg.test"),
    # a secret in one command does not hide the next command's port
    (
        f"docker login -p {PW} reg.test && docker run -p 8080:80 img",
        f"docker login -p {R} reg.test && docker run -p 8080:80 img",
    ),
    # -- wide runs of blanks between a flag and its value ------------------
    (f"curl -u    admin:{PW} https://x.test", f"curl -u    admin:{R} https://x.test"),
    (f"curl --user    u:{PW} https://x.test", f"curl --user    u:{R} https://x.test"),
    (f"curl -u\t\tadmin:{PW} https://x.test", f"curl -u\t\tadmin:{R} https://x.test"),
    (
        f"curl -u{' ' * 200}admin:{PW} https://x.test",
        f"curl -u{' ' * 200}admin:{R} https://x.test",
    ),
    (f"sshpass -p    {PW} ssh host", f"sshpass -p    {R} ssh host"),
    (f"redis-cli -a    {PW} ping", f"redis-cli -a    {R} ping"),
    (f"docker login -p    {PW} reg.test", f"docker login -p    {R} reg.test"),
    (
        f"docker login --password    {PW} reg.test",
        f"docker login --password    {R} reg.test",
    ),
    (f"docker    login  -p {PW} reg.test", f"docker    login  -p {R} reg.test"),
    # -- options between the command and its subcommand or flag ------------
    (
        f"docker --config /x login -p {PW} reg.test",
        f"docker --config /x login -p {R} reg.test",
    ),
    (
        f"docker -H tcp://h login -p {PW} reg.test",
        f"docker -H tcp://h login -p {R} reg.test",
    ),
    (
        f"docker --config=/x --debug login --password {PW} reg.test",
        f"docker --config=/x --debug login --password {R} reg.test",
    ),
    (
        f"podman --root /r login -p {PW} reg.test",
        f"podman --root /r login -p {R} reg.test",
    ),
    (
        f"sshpass -P assword -p {PW} ssh host",
        f"sshpass -P assword -p {R} ssh host",
    ),
    (
        f"sshpass -v -P assword -p '{PW}' ssh -p 2222 host",
        f"sshpass -v -P assword -p '{R}' ssh -p 2222 host",
    ),
    (
        f"sshpass -P assword -p {R} ssh -p 2222 host",
        f"sshpass -P assword -p {R} ssh -p 2222 host",
    ),
]

# What is not a credential, so precision does not collapse.
HARMLESS = [
    # -p is a port or a mode outside mysql / sshpass
    "ssh -p 22 host",
    "ssh -p22 host",
    "scp -P 22 file host:/tmp/",
    "redis-cli -h cache -p 6379 ping",
    "docker run -p 8080:80 -u 1000:1000 img",
    "mkdir -p /a/b && cp -p a b",
    # -u / -a as a user or a listing flag
    "sudo -u bob ls",
    "git push -u origin main",
    "ls -a /tmp",
    "grep -a needle file",
    # no value, or no credential in the value
    "curl -u",
    "curl -u admin https://x.test",
    "curl -u https://x.test",
    "curl -u admin: https://x.test",
    "curl --user-agent 'Mozilla:5' https://x.test",
    "curl -o out -L https://x.test:8443/path",
    "redis-cli -a",
    # mysql -p without an attached value is the interactive prompt: the next
    # word is the database, not a password
    "mysql -p dbname",
    "mysql -u root -p mydb -e 'select 1'",
    "mysql -h host -P 3306 -u root -p",
    "mysql --protocol=tcp -u root mydb",
    "mysql -u root -p -e 'select 1'",
    'mysql -u root -p"" mydb',
    # sshpass options that are not a password on the command line
    "sshpass -e ssh -p 2222 host",
    "sshpass -f pwfile ssh -p 2222 host",
    # a variable reference is not the secret
    "sshpass -p $PASSWORD ssh host",
    'sshpass -p "$PASSWORD" ssh host',
    "docker login -p $TOKEN reg.test",
    "mysql -u root -p$DB_PASS db",
    'curl -u "admin:$TOKEN" https://x.test',
    # docker login without a password argument
    "docker login --password-stdin -u user registry.test",
    "docker login -u user registry.test",
    "docker login registry.test",
    # options before the subcommand do not turn another subcommand into login:
    # a container named "login" and its published port are not a credential
    "docker run --name login -p 8080:80 img",
    "docker --debug run --name login -p 8080:80 img",
    "docker -H tcp://h run --name login -p 8080:80 img",
    "docker -H tcp://h ps",
    "docker --config /x run -p 8080:80 img",
    # sshpass's own options that take a value do not make the ssh port one
    "sshpass -P assword ssh -p 2222 host",
    "sshpass -v -f pwfile ssh -p 2222 host",
    # the gap between a command and its flag stops at the next command
    "curl -s https://x.test | mysql -p dbname",
    "curl https://x.test; sudo -u bob foo:bar",
    "curl https://x.test && docker run -u 1000:1000 img",
    "curl https://x.test\\nsudo -u bob:grp ls",
    "redis-cli ping\nls -a /tmp",
]


class TestCliFlags:
    @pytest.mark.parametrize(("text", "expected"), SECRET_CASES)
    def test_the_value_is_redacted_and_the_flag_stays(self, text, expected):
        assert redactor().scrub(text) == expected

    @pytest.mark.parametrize(("text", "expected"), SECRET_CASES)
    def test_the_secret_is_gone_with_the_real_scanners_too(self, text, expected):
        out = redactor(bare=False).scrub(text)
        for word in (PW, "my pass phrase", "my pw", "p@ss,w0rd!"):
            assert word not in out

    @pytest.mark.parametrize("text", HARMLESS)
    def test_ordinary_commands_are_left_alone(self, text):
        assert redactor().scrub(text) == text

    @pytest.mark.parametrize("text", HARMLESS)
    def test_ordinary_commands_survive_the_real_scanners(self, text):
        assert redactor(bare=False).scrub(text) == text

    @pytest.mark.parametrize(("text", "expected"), SECRET_CASES)
    def test_scrubbing_again_does_not_grow_the_marker(self, text, expected):
        # The renderer scrubs one string up to three times (leaf, dump, page).
        once = redactor().scrub(text)
        assert redactor().scrub(once) == once == expected
        assert f"{R}{R}" not in once
        assert f"{R}]" not in once

    def test_secrets_are_counted_in_the_existing_category(self):
        r = redactor()
        r.scrub(f"sshpass -p {PW} ssh host && mysql -p{PW} db")
        assert r.replaced["secret"] == 2
        r.scrub(f"curl -u a:{PW} x", count=False)
        assert r.replaced["secret"] == 2

    def test_a_command_in_a_json_dump_is_scrubbed(self):
        dump = (
            '{"command": "mysql -h db -p' + PW + ' app", '
            '"description": "ssh -p 22 host"}'
        )
        out = redactor().scrub(dump)
        assert PW not in out
        assert "ssh -p 22 host" in out

    def test_ordinary_emails_next_to_flags_are_still_ordinary(self):
        out = redactor().scrub("ssh -p 22 someone@example.com")
        assert out == f"ssh -p 22 {R}"


class TestUrlUserinfo:
    def test_password_with_an_at_sign_is_redacted_up_to_the_last_at(self):
        text = f"postgres{SEP}admin:{PW_AT}@10.0.0.5/db"
        assert redactor().scrub(text) == f"postgres{SEP}admin:{R}@10.0.0.5/db"

    def test_the_share_rule_runs_before_the_shared_scanner(self):
        # The hook's URL regex stops at the first "@" and would leave
        # "Secret99@" behind; it must find the userinfo already redacted.
        out = redactor(bare=False).scrub(f"postgres{SEP}admin:{PW_AT}@10.0.0.5/db")
        assert "Sup3r" not in out
        assert "Secret99" not in out
        assert "10.0.0.5/db" in out

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (f"redis{SEP}:{PW}@10.0.0.5:6379/0", f"redis{SEP}:{R}@10.0.0.5:6379/0"),
            (
                # an e-mail user: the password is redacted by this rule, the
                # user by the e-mail rule
                f"smtp{SEP}relay@mail.test:{PW_AT}@smtp.test:587",
                f"smtp{SEP}{R}:{R}@smtp.test:587",
            ),
            (f"amqp{SEP}guest:{PW}:x@broker:5672/", f"amqp{SEP}guest:{R}@broker:5672/"),
            (
                f"https{SEP}u:{PW}@h1/a https{SEP}u:{PW}@h2/b",
                f"https{SEP}u:{R}@h1/a https{SEP}u:{R}@h2/b",
            ),
            (
                f'{{"a":"https{SEP}u:{PW_AT}@h1","b":"https{SEP}v:{PW}@h2"}}',
                f'{{"a":"https{SEP}u:{R}@h1","b":"https{SEP}v:{R}@h2"}}',
            ),
            (f"https{SEP}u:{PW}@host?to=a@b", f"https{SEP}u:{R}@host?to=a@b"),
            (f"https{SEP}u:{PW}@host#frag@x", f"https{SEP}u:{R}@host#frag@x"),
            (
                f"git clone 'https{SEP}u:{PW}@host/r.git'",
                f"git clone 'https{SEP}u:{R}@host/r.git'",
            ),
            (f"https{SEP}u:{PW}%40x@host/", f"https{SEP}u:{R}@host/"),
        ],
    )
    def test_userinfo_forms(self, text, expected):
        assert redactor().scrub(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            f"https{SEP}u:{PW_AT}@host/r",
            f"redis{SEP}:{PW}@h:6379/0",
            f"smtp{SEP}relay@mail.test:{PW_AT}@smtp.test:587",
        ],
    )
    def test_no_form_leaks_through_the_real_scanners(self, text):
        out = redactor(bare=False).scrub(text)
        for word in (PW, "Sup3r", "Secret99", "p@ss"):
            assert word not in out

    @pytest.mark.parametrize(
        "text",
        [
            "https://example.com:8080/path",
            "http://localhost:3000",
            "ssh://git@gitserver:22/repo.git",
            "https://host/users/a@b",
            "https://host/a:b@c",
            "postgres://u:$DB_PASS@h/db",
            "postgres://u:${DB_PASS}@h/db",
            f"postgres{SEP}u:{R}@h/db",
            "see mailto:someone@host and http://host:80/x",
            'echo "http://a:1" "b@c"',
            # a password that holds a "/" reaches an "@" only through the path
            # of an ordinary URL when the "@" comes after a "?" or "#", after
            # whitespace, after a quote or after another scheme
            "http://localhost:3000/api?email=a@b",
            "http://localhost:3000/api#frag@x",
            "http://localhost:3000/api @b",
            'http://localhost:3000/api"@b',
            "http://localhost:3000/api'@b",
            "https://a:1/go/https://b@c",
            "https://a:1/go/redirect?to=https://b@c",
        ],
    )
    def test_urls_without_a_password_are_left_alone(self, text):
        assert redactor().scrub(text) == text

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # the key a console prints, with the slashes in it
            (
                f"s3a{SEP}{KEY_ID}:{PW_SLASH}@bucket/p",
                f"s3a{SEP}{KEY_ID}:{R}@bucket/p",
            ),
            (f"mongodb{SEP}u:pa/ss@h/db", f"mongodb{SEP}u:{R}@h/db"),
            (f"mongodb{SEP}u:/@h/db", f"mongodb{SEP}u:{R}@h/db"),
            (f"mongodb{SEP}u:{PW}//{PW}@h/db", f"mongodb{SEP}u:{R}@h/db"),
            # still up to the last "@": the "@" in the password is part of it
            (f"mongodb{SEP}u:pa/ss@w0rd@h/db", f"mongodb{SEP}u:{R}@h/db"),
            (
                f"git clone 'mongodb{SEP}u:pa/ss@h/db'",
                f"git clone 'mongodb{SEP}u:{R}@h/db'",
            ),
            (
                f'{{"a":"mongodb{SEP}u:pa/ss@h1","b":"s3a{SEP}k:x/y@h2"}}',
                f'{{"a":"mongodb{SEP}u:{R}@h1","b":"s3a{SEP}k:{R}@h2"}}',
            ),
            (f"mongodb{SEP}u:pa/ss@h/db?x=a@b", f"mongodb{SEP}u:{R}@h/db?x=a@b"),
            (
                f"mongodb{SEP}u:pa/ss@h/db mongodb{SEP}v:q/r@h2/db",
                f"mongodb{SEP}u:{R}@h/db mongodb{SEP}v:{R}@h2/db",
            ),
        ],
    )
    def test_a_password_may_hold_a_slash(self, text, expected):
        assert redactor().scrub(text) == expected

    def test_a_slash_password_does_not_leak_through_the_real_scanners(self):
        out = redactor(bare=False).scrub(f"s3a{SEP}{KEY_ID}:{PW_SLASH}@bucket/p")
        for part in ("wJalrXUtnFEMI", "K7MDENG", "bPxRfiCYEXAMPLEKEY", KEY_ID):
            assert part not in out
        assert "bucket/p" in out

    def test_an_ordinary_userinfo_keeps_its_host_when_the_path_has_an_at(self):
        # The slash-free reading wins: the last "@" before the first "/" ends
        # the userinfo, however many "@" the path holds.
        text = f"https{SEP}u:{PW}@host/users/a@b/c@d"
        assert redactor().scrub(text) == f"https{SEP}u:{R}@host/users/a@b/c@d"

    def test_a_port_before_an_at_in_the_path_is_over_redacted_by_design(self):
        # `host:443/@scope` is indistinguishable from a password with a "/";
        # a shared page takes the over-redaction. Pinned so a change is chosen.
        assert redactor().scrub("http://host:443/@scope/pkg") == (
            f"http{SEP}host:{R}@scope/pkg"
        )

    def test_an_email_in_a_path_is_only_an_email(self):
        out = redactor().scrub("https://host/users/someone@example.com/x")
        assert out == f"https://host/users/{R}/x"

    def test_scrubbing_again_does_not_grow_the_marker(self):
        once = redactor().scrub(f"postgres{SEP}admin:{PW_AT}@10.0.0.5/db")
        assert redactor().scrub(once) == once
        assert f"{R}]" not in once

    def test_secrets_are_counted_in_the_existing_category(self):
        r = redactor()
        r.scrub(f"postgres{SEP}admin:{PW_AT}@10.0.0.5/db")
        assert r.replaced["secret"] == 1


# Credential forms that `scan_secrets` (secret_scan.py and the bash-review
# pre-send scan) flags through `_CONTEXT_SCANNERS`, and that the redactor does
# not mask, because it loads `_SECRET_SCANNERS` only. They are pinned here, not
# fixed: the xfail is strict, so closing a hole turns the test into an XPASS
# failure and whoever did it must delete the entry (and the matching "at the time
# of writing" text in dossier_redact.py's docstring and session-dossier's
# SKILL.md, step 3). Assembled from PW at runtime like the rest of this file.
CONTEXT_SCANNER_GAPS = [
    pytest.param(f"DEPLOY_PAT={PW * 2}", id="pat-variable-without-a-known-prefix"),
    pytest.param(f"'password' => '{PW}',", id="hash-rocket-field"),
    pytest.param(f"machine example.test login bob password {PW}", id="netrc-line"),
]


def _scan_secrets():
    """`scan_secrets` from the hook, loaded by path so sys.path stays untouched."""
    spec = importlib.util.spec_from_file_location(
        "_dossier_gap_probe", REPO_ROOT / ".claude/hooks/_bash_review_common.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scan_secrets


class TestContextScannerGaps:
    @pytest.mark.parametrize("text", CONTEXT_SCANNER_GAPS)
    def test_secret_scan_flags_the_form(self, text):
        flagged, _label = _scan_secrets()(text, None)
        assert flagged

    @pytest.mark.xfail(
        strict=True,
        reason="the redactor does not load _CONTEXT_SCANNERS (see dossier_redact.py)",
    )
    @pytest.mark.parametrize("text", CONTEXT_SCANNER_GAPS)
    def test_the_redactor_masks_the_form(self, text):
        assert PW not in redactor(bare=False).scrub(text)


class TestSharePage:
    def test_a_bash_command_is_scrubbed_in_the_page_and_the_json_dump(self, tmp_path):
        command = (
            f"sshpass -p '{PW}' ssh -p 2222 host && mysql -u root -p{PW}x app "
            f"&& psql postgres{SEP}admin:{PW_AT}@10.0.0.5/db"
        )
        b = SessionBuilder()
        b.prompt("deploy")
        b.bash("t1", command, "done")
        session = dossier_link.load_session(dossier_locate.paths_for(b.write(tmp_path)))
        options = dossier_render.RenderOptions(generated_at="2026-01-02T03:04:05Z")
        page = dossier_render.render(
            session,
            dossier_scope.select_scope(session),
            options,
            redactor=redactor(bare=False),
        ).html
        text = html.unescape(page)
        for secret in (PW, "Sup3r", "Secret99"):
            assert secret not in text
        assert "ssh -p 2222 host" in text
        assert "mysql -u root -p[REDACTED] app" in text
        assert "[REDACTED]]" not in page


class TestLinearTime:
    @pytest.mark.parametrize(
        "unit",
        [
            "curl -u ",
            "curl --user ",
            "sshpass -p ",
            "sshpass -v ",
            "mysql -p ",
            "redis-cli -h ",
            "docker login -u ",
            "docker --config ",
            "docker -H tcp://h ",
            "docker -a -b -c -d -e -f -g -h ",
            "sshpass -P ",
            "sshpass -f x -v -e -P x ",
            "curl -u" + " " * 200,
            "://a:",
            "://:",
            "://" + "a" * 60 + ":",
            # paths and ports with no "@": each URL's tail is read once
            "://a:" + "b/" * 100,
            "://a:1/" + "c/" * 500 + " ",
        ],
    )
    def test_repeated_openers_scrub_in_linear_time(self, unit):
        start = time.perf_counter()
        # about 300k characters whatever the unit's length
        redactor(bare=False).scrub(unit * (300_000 // len(unit)))
        assert time.perf_counter() - start < 2.0

    def test_a_long_authority_with_many_at_signs_scrubs_in_linear_time(self):
        start = time.perf_counter()
        redactor(bare=False).scrub("://a:" + "b@" * 100_000)
        assert time.perf_counter() - start < 2.0

    def test_a_long_unterminated_quote_scrubs_in_linear_time(self):
        start = time.perf_counter()
        redactor(bare=False).scrub("sshpass -p '" * 20_000 + "x" * 100_000)
        assert time.perf_counter() - start < 2.0
