"""Credential shapes the shared scanner (`scan_secrets`) must refuse, and the
ordinary commands it must keep letting through.

`scan_secrets` is the only gate before a Bash command goes to Gemini / Codex
(bash-review) and before an editor buffer goes to an AI tool
(scripts/secret_scan.py), so a shape it does not know goes out in clear text.
The shapes below are the ones a plain `NAME=value` / vendor-prefix scan misses:
env-style password names (`DB_PASS` / `MYSQL_PWD` / `PASS`), `{"pwd": ...}` and
`'password' => ...` literals, `curl -u user:pw`, `sshpass -p`, a netrc line, a
URL whose user is empty (`redis://:pw@host`), `Authorization: Token ...`, Azure
storage keys, and a handful of vendor token formats.

The negative corpus is as important as the positive one: a false positive here
is a refused command, i.e. stopped work. Every entry in `CLEAN` is an ordinary
development command (or code) that must stay `(False, "")`.

Fake credentials are assembled at runtime from a prefix and a repeated
character, never written as a well-formed literal: a literal in this file would
trip GitHub push protection and would itself be refused by `secret_scan.py` when
the diff is quoted to a reviewer.
"""

import json
import sys
import time

import pytest
from conftest import REPO_ROOT, hook_payload

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as common  # noqa: E402

HOOK = ".claude/hooks/bash-review.py"

HEX = "0123456789abcdef"
PW = "hunter2hunter2"  # a stand-in password: 14 chars, no real entropy


def _hex(n: int) -> str:
    return (HEX * (n // len(HEX) + 1))[:n]


def _scan(text: str) -> tuple[bool, str]:
    return common.scan_secrets(text, {"command": text})


def _scan_in_json_only(text: str) -> tuple[bool, str]:
    """The text sits in a non-command field, so only json.dumps(tool_input) sees
    it, with every quote turned into `\\"`."""
    return common.scan_secrets("ls", {"command": "ls", "description": text})


# ---------------------------------------------------------------------------
# Positive corpus: (id, text, label)
# ---------------------------------------------------------------------------
BARE_TOKENS = [
    ("shopify-admin", "shpat_" + _hex(32), "Shopify token"),
    ("shopify-custom-app", "shpca_" + _hex(32), "Shopify token"),
    ("shopify-private-app", "shppa_" + _hex(32), "Shopify token"),
    ("shopify-shared-secret", "shpss_" + _hex(32), "Shopify token"),
    ("linear", "lin_api_" + "a" * 40, "Linear key"),
    ("netlify-personal", "nfp_" + "a" * 36, "Netlify token"),
    ("netlify-cli", "nfc_" + "a" * 36, "Netlify token"),
    ("netlify-oauth", "nfo_" + "a" * 36, "Netlify token"),
    ("databricks", "dapi" + _hex(32), "Databricks token"),
    ("databricks-suffixed", "dapi" + _hex(32) + "-2", "Databricks token"),
    ("stripe-secret-test", "sk_test_" + "a" * 24, "Stripe key"),
    ("stripe-restricted-test", "rk_test_" + "a" * 24, "Stripe key"),
    ("stripe-webhook", "whsec_" + "a" * 32, "Stripe webhook secret"),
    (
        "discord-bot",
        "M" + "a" * 23 + "." + "b" * 6 + "." + "c" * 27,
        "Discord bot token",
    ),
    (
        "discord-bot-new-format",
        "N" + "a" * 25 + "." + "b" * 6 + "." + "c" * 38,
        "Discord bot token",
    ),
    ("telegram-bot", "123456789:AA" + "a" * 33, "Telegram bot token"),
    ("twilio-api-key", "SK" + _hex(32), "Twilio API key"),
]


@pytest.mark.parametrize(
    ("token", "label"),
    [(t, lbl) for _id, t, lbl in BARE_TOKENS],
    ids=[i for i, _t, _l in BARE_TOKENS],
)
class TestBareVendorTokens:
    def test_bare_token_is_detected_with_its_kind(self, token, label):
        assert _scan(f"echo {token}") == (True, label)

    def test_token_inside_an_assignment_is_detected(self, token, label):
        # Whatever the variable is called, the prefix alone is enough.
        found, _ = _scan(f"export X_VALUE={token}")
        assert found is True

    def test_token_glued_to_japanese_prose_is_detected(self, token, label):
        # The follower rule is `ASCII word character`, not `\b`.
        assert _scan(f"鍵は {token}です") == (True, label)

    def test_token_in_a_non_command_field_is_detected(self, token, label):
        assert _scan_in_json_only(f"key {token}") == (True, label)


def test_telegram_token_in_a_bot_api_url_is_detected():
    # The id is glued to `bot` in the API URL, so a plain "no letter before it"
    # boundary would never see it.
    token = "123456789:AA" + "a" * 33
    assert _scan(f"wget -qO- https://api.telegram.org/bot{token}/getMe") == (
        True,
        "Telegram bot token",
    )


AZURE_KEY = "A" * 86 + "=="
AZURE_SAS_KEY = "B" * 43 + "="

CONTEXT_POSITIVES = [
    # --- env-style password names ------------------------------------------------
    ("DB_PASS", f"DB_PASS={PW} ./run", "credential variable"),
    ("DB_PASS-export", f"export DB_PASS={PW}", "credential variable"),
    ("DB_PASS-yaml", f"DB_PASS: {PW}", "credential variable"),
    ("DB_PASS-quoted", f'DB_PASS="{PW}" ./run', "credential variable"),
    ("DB_PASS-in-dotenv", f'echo "DB_PASS={PW}" >> .env', "credential variable"),
    ("MYSQL_PWD", f"MYSQL_PWD={PW} mysql -u root", "credential variable"),
    ("bare-PASS", f"PASS={PW} make", "credential variable"),
    ("json-DB_PASS", f'{{"DB_PASS": "{PW}"}}', "credential variable"),
    (
        "AZURE_DEVOPS_EXT_PAT",
        "AZURE_DEVOPS_EXT_PAT=" + "a" * 52 + " az devops login",
        "credential variable",
    ),
    ("GH_PAT", "GH_PAT=" + "a" * 40 + " gh api /user", "credential variable"),
    # --- literals with a quoted key ------------------------------------------------
    ("json-pwd", f'{{"pwd": "{PW}"}}', "credential field"),
    ("json-pass", f'{{"user": "bob", "pass": "{PW}"}}', "credential field"),
    ("json-db_pass", f'{{"db_pass": "{PW}"}}', "credential field"),
    ("php-pwd-arrow", f"'pwd' => '{PW}'", "credential field"),
    ("php-password-arrow", f"'password' => '{PW}',", "credential field"),
    ("ruby-password-arrow", f'"password" => "{PW}"', "credential field"),
    ("php-array", f"$cfg = ['host' => 'db', 'pass' => '{PW}'];", "credential field"),
    (
        "php-password-confirmation",
        f"'password_confirmation' => '{PW}'",
        "credential field",
    ),
    (
        "json-pwd-in-shell-string",
        f'curl -d "{{\\"pwd\\":\\"{PW}\\"}}" https://example.test/login',
        "credential field",
    ),
    # --- URLs with an empty user -------------------------------------------------
    ("redis-url", f"redis-cli -u redis://:{PW}@host:6379", "URL credentials"),
    ("https-url", f"git clone https://:{PW}@host/r.git", "URL credentials"),
    ("redis-url-var", f"REDIS_URL=redis://:{PW}@cache:6379/0", "URL credentials"),
    # --- curl basic auth ----------------------------------------------------------
    ("curl-u", f"curl -u admin:{PW} https://api.example.test/", "curl credential"),
    ("curl-user", f"curl --user admin:{PW} https://x.test", "curl credential"),
    ("curl-user-eq", f"curl --user=admin:{PW} https://x.test", "curl credential"),
    ("curl-grouped-flags", f"curl -sSu admin:{PW} https://x.test", "curl credential"),
    ("curl-flags-then-u", f"curl -sS -u admin:{PW} https://x.test", "curl credential"),
    ("curl-attached", f"curl -uadmin:{PW} https://x.test", "curl credential"),
    ("curl-single-quoted", f"curl -u 'admin:{PW}' https://x.test", "curl credential"),
    ("curl-double-quoted", f'curl -u "admin:{PW}" https://x.test', "curl credential"),
    ("curl-proxy-user", f"curl --proxy-user p:{PW} https://x.test", "curl credential"),
    ("curl-abs-path", f"/usr/bin/curl -u admin:{PW} https://x.test", "curl credential"),
    (
        "curl-user-from-var",
        f'curl -u "$API_USER:{PW}" https://x.test',
        "curl credential",
    ),
    ("curl-empty-user", f"curl -u :{PW} https://x.test", "curl credential"),
    (
        "curl-after-quoted-url",
        f'curl "https://x.test/?a=1&b=2" -u admin:{PW}',
        "curl credential",
    ),
    (
        "curl-after-header",
        f"curl -H 'Content-Type: application/json' -d '{{}}' -u admin:{PW} https://x",
        "curl credential",
    ),
    (
        "curl-line-continuation",
        f"curl -X POST \\\n  -H 'Accept: application/json' \\\n  -u admin:{PW} \\\n  https://x.test",
        "curl credential",
    ),
    # --- sshpass -------------------------------------------------------------------
    ("sshpass", f"sshpass -p {PW} ssh user@host", "sshpass password"),
    ("sshpass-single-quoted", f"sshpass -p '{PW}' ssh user@host", "sshpass password"),
    ("sshpass-double-quoted", f'sshpass -p "{PW}" scp f h:', "sshpass password"),
    ("sshpass-attached", f"sshpass -p{PW} ssh user@host", "sshpass password"),
    ("sshpass-verbose", f"sshpass -v -p {PW} ssh user@host", "sshpass password"),
    # --- netrc ---------------------------------------------------------------------
    (
        "netrc-one-line",
        f'echo "machine example.test login me password {PW}" >> ~/.netrc',
        "netrc credential",
    ),
    (
        "netrc-printf-escaped-newlines",
        f"printf 'machine h.test\\nlogin me\\npassword {PW}\\n' >> ~/.netrc",
        "netrc credential",
    ),
    (
        "netrc-heredoc",
        f"cat >> ~/.netrc <<EOF\nmachine h.test\nlogin me\npassword {PW}\nEOF",
        "netrc credential",
    ),
    (
        "netrc-password-first",
        f"echo 'machine h.test password {PW} login me' >> ~/.netrc",
        "netrc credential",
    ),
    # --- Authorization: Token ---------------------------------------------------
    (
        "authorization-token",
        'curl -H "Authorization: Token ' + _hex(40) + '" https://x.test/api',
        "authorization credential",
    ),
    (
        "authorization-token-single-quoted",
        "curl -H 'Authorization: Token " + _hex(40) + "' https://x.test/api",
        "authorization credential",
    ),
    (
        "authorization-apikey",
        'curl -H "Authorization: ApiKey ' + "a" * 32 + '" https://x.test/api',
        "authorization credential",
    ),
    # --- Azure storage / service bus --------------------------------------------
    (
        "azure-account-key",
        "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey="
        + AZURE_KEY
        + ";EndpointSuffix=core.windows.net",
        "Azure storage key",
    ),
    (
        "azure-account-key-env",
        f"AZURE_STORAGE_CONNECTION_STRING='AccountKey={AZURE_KEY}'",
        "Azure storage key",
    ),
    # Covered by the keyword table's `access[_-]?key`, not by an Azure scanner.
    # Pinned so the Service Bus form stays refused.
    (
        "azure-shared-access-key",
        f"Endpoint=sb://ns.servicebus.windows.net/;SharedAccessKey={AZURE_SAS_KEY}",
        "secret assignment",
    ),
]


@pytest.mark.parametrize(
    ("text", "label"),
    [(t, lbl) for _id, t, lbl in CONTEXT_POSITIVES],
    ids=[i for i, _t, _l in CONTEXT_POSITIVES],
)
class TestContextualShapes:
    def test_shape_is_detected_with_its_kind(self, text, label):
        assert _scan(text) == (True, label)


@pytest.mark.parametrize(
    "text",
    [
        t
        for i, t, _l in CONTEXT_POSITIVES
        if i
        in {
            "DB_PASS",
            "MYSQL_PWD",
            "json-pwd",
            "php-password-arrow",
            "redis-url",
            "curl-u",
            "curl-double-quoted",
            "sshpass-double-quoted",
            "authorization-token",
        }
    ],
)
def test_shape_is_detected_when_only_the_json_dump_carries_it(text):
    # tool_input fields other than `command` reach the reviewer as
    # json.dumps(...), where every quote is `\"`.
    found, _ = _scan_in_json_only(text)
    assert found is True, f"missed {text!r} in a non-command field"


# ---------------------------------------------------------------------------
# Negative corpus: ordinary commands / code that must stay clean
# ---------------------------------------------------------------------------
CLEAN = [
    # --- password-ish names with a non-secret value --------------------------
    "PASS=1 make",
    "PASS=true ./run-tests",
    "export PASSWORD_MIN_LEN=8",
    "export PASS_COUNT=100000000",
    "BYPASS=disabled-forever make",
    "bypass=disabled-forever make",
    "compass=north-north-west",
    "git log --author=foo",
    "grep -rn pass .",
    'echo "PASS: tests/unit/test_scanner.py"',
    "go test ./... | grep -E '^(PASS|FAIL)'",
    # --- references, not values ----------------------------------------------
    "export DB_PASS=$DB_PASS_FROM_VAULT",
    'DB_PASS="${DB_PASS}" ./run',
    'DB_PASS="$DB_PASS_FROM_VAULT" ./run',
    "MYSQL_PWD=$(cat /run/secrets/mysql_pw) mysql",
    "DB_PASS=$(vault read -field=password secret/db) ./run",
    "GH_PAT=$GITHUB_PERSONAL_ACCESS_TOKEN gh api /user",
    "AZURE_DEVOPS_EXT_PAT=${ADO_TOKEN_FROM_KEYVAULT_ENV} az devops",
    # --- `pwd` is the working directory ---------------------------------------
    "PWD=/Users/sardonyx0827/work/github/dotfiles make",
    "OLDPWD=/Users/sardonyx0827/work/github/dotfiles",
    "ORIG_PWD=/srv/app/releases/current ./deploy",
    "ORIG_PWD=~/work/github/dotfiles/scripts ./deploy",
    "ORIG_PWD=./work/github/dotfiles ./deploy",
    'export OLD_PWD="$PWD"',
    'cd "$PWD"',
    '{"pwd": "/Users/sardonyx0827/work/github/dotfiles"}',
    '{"pwd": "~/work/github/dotfiles"}',
    "'pwd' => '/var/www/html/releases/current'",
    # --- lowercase code identifiers --------------------------------------------
    "first_pass = first_pass_result",
    "second_pass = run_pass(x)",
    "pass_count = len(passes_found)",
    "skip_pat = re.compile(r'^release-.*')",
    'SKIP_PAT="^release-.*"',
    "LINE_PAT = re.compile(r'^[a-z]+(-[a-z]+)*$')",
    # --- arrow functions are not PHP arrays -----------------------------------------
    "items.map(token => token.value)",
    "xs.filter(secret => secret.active)",
    "const f = (password) => password.length > 8",
    "handlers.map(token => processToken(token))",
    "'passport' => 'AB1234567'",
    "'password' => Hash::make('s3cr3t-seed-pw'),",
    "'password' => env('DB_PASSWORD', ''),",
    "'password' => config('database.password'),",
    "'password' => $this->hasher->make($plain),",
    "'password' => $row['password'],",
    "'bypass' => 'enabled-forever'",
    "'token' => 'abc'",
    "array_map('trim', $values)",
    # --- URLs -------------------------------------------------------------------
    "curl ws://localhost:3000/socket",
    "curl http://[::1]:8080/",
    "curl http://[fe80::1]:80/x",
    "git clone ssh://git@github.com:22/org/repo",
    "curl https://example.com:8080/path",
    "psql postgres://user@host/db",
    "git clone git@github.com:org/repo.git",
    "redis-cli -u redis://localhost:6379/0",
    "redis-cli -u redis://:$REDIS_PASSWORD@cache:6379",
    "git clone https://:${GIT_TOKEN}@host/r.git",
    "curl https://:@host/",
    # --- -u / --user that are not basic auth -----------------------------------------
    "docker run -u 1000:1000 alpine",
    "docker run --user www-data:www-data img",
    "docker exec -u root:root c sh",
    "docker run --user=$(id -u):$(id -g) img",
    "git push -u origin main",
    "sudo -u postgres psql",
    "sort -u -t: -k2 file",
    "curl -u user https://x.test",
    'curl -u "$USER:$TOKEN" https://x.test',
    "curl -u $USER:$PASS https://x.test",
    'curl -u "${USER}:${PASS}" https://x.test',
    "curl -u admin: https://x.test",
    'curl -u "$(cat user):$(cat pw)" https://x.test',
    "curl --user-agent foo/1.0:beta https://x.test",
    'curl -A "Mozilla/5.0:abc" https://x.test',
    "curl --upload-file file.txt https://x.test",
    'curl -d "username=bob:secret123" https://x.test',
    "curl -dusername=bob:secret123 https://x.test",
    'curl -sS -o /dev/null -w "%{http_code}" https://host.test:8443/path',
    # another command after curl is not curl's flag
    "curl https://x.test; docker run -u root:root img",
    "curl https://x.test && docker run -u 1000:1000 img",
    "curl https://x.test | docker run -u 1000:1000 img",
    "curl https://x.test\ndocker run -u 1000:1000 img",
    "curl https://x.test\nsudo -u deploy:deploy make",
    # The share-page redactor (dossier_redact) pins the same ordinary commands
    # against its own rules; these are the ones that sit next to the new shapes.
    "curl -u https://x.test",
    "curl --user-agent 'Mozilla:5' https://x.test",
    "curl -o out -L https://x.test:8443/path",
    "curl https://x.test\\nsudo -u bob:grp ls",
    "redis-cli -a",
    "mysql -u root -p mydb -e 'select 1'",
    "docker login --password-stdin -u user registry.test",
    "docker run --name login -p 8080:80 img",
    "sshpass -P assword ssh -p 2222 host",
    "sshpass -v -f pwfile ssh -p 2222 host",
    # --- sshpass ---------------------------------------------------------------------
    "sshpass -e ssh -p 2222 user@host",
    "sshpass -f ~/.pwfile ssh user@host",
    'sshpass -p "$SSH_PASSWORD" ssh user@host',
    "sshpass -p $(cat ~/.pw) ssh user@host",
    "ssh -p 2222 user@host",
    "man sshpass",
    # --- netrc ---------------------------------------------------------------------------
    'git commit -m "machine learning password reset flow"',
    "echo machine learning login password",
    "echo 'machine h.test login me' >> ~/.netrc",
    "cat ~/.netrc",
    "echo 'machine h.test login me password $NETRC_PASSWORD' >> ~/.netrc",
    # --- Authorization -----------------------------------------------------------------
    'curl -H "Authorization: Token $API_TOKEN" https://x.test',
    'curl -H "Authorization: Token ${API_TOKEN}" https://x.test',
    'curl -H "Authorization: Token abc" https://x.test',
    'curl -H "Authorization: ApiKey $KEY" https://x.test',
    "Authorization: Token validation",
    # --- Azure ------------------------------------------------------------------------------
    "AccountKey=$AZURE_STORAGE_KEY",
    "AccountKey=short",
    "az storage blob list --account-key $KEY",
    "SharedAccessKey=${SB_KEY}",
    # --- near-miss tokens ---------------------------------------------------------------
    "echo shpat_" + _hex(31),
    "echo shpat_xxxx",
    "echo myshpat_" + _hex(32),
    "echo lin_api_short",
    "echo lin_api_" + "a" * 31,
    "echo nfp_short",
    "echo snfp_" + "a" * 36,
    "echo dapi" + _hex(31),
    "echo dapi_client",
    "echo mydapi" + _hex(32),
    "echo sk_test_short",
    "echo whsec_short",
    "echo whsec_" + "a" * 10,
    "echo M" + "a" * 10 + ".bbbbbb." + "c" * 10,
    "echo M" + "a" * 23 + "." + "b" * 6 + "." + "c" * 26,
    "echo com.example.ServiceConfig.production",
    "echo 1700000000:" + "a" * 35,
    "echo 123456789:AA" + "a" * 10,
    "echo 12345:AA" + "a" * 33,
    "echo SK" + _hex(31),
    "echo SKU-12345",
]


@pytest.mark.parametrize("text", CLEAN)
class TestNoFalsePositives:
    def test_command_stays_clean(self, text):
        assert _scan(text) == (False, ""), f"false positive on {text!r}"

    def test_command_stays_clean_in_a_non_command_field(self, text):
        # Same text, but seen only through json.dumps (quotes become `\"`,
        # newlines become `\n`).
        assert _scan_in_json_only(text) == (False, ""), f"false positive on {text!r}"


# ---------------------------------------------------------------------------
# Existing shapes keep their labels (new scanners are appended, never inserted)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("export API_KEY=" + "g" * 16, "secret assignment"),
        ("git clone https://user:s3cr3tpasss@github.com/a/b", "URL credentials"),
        ("deploy --client-secret=" + "i" * 12, "secret assignment"),
        ("echo ghp_" + "a" * 36, "GitHub token"),
        ("echo sk_live_" + "a" * 24, "Stripe key"),
    ],
)
def test_existing_labels_are_unchanged(text, label):
    assert _scan(text) == (True, label)


# The label is shown to the user and written to logs, so it must name a category and
# never carry the matched text. Labels are constants of the scanner tables; pinning
# the whole set means a new scanner (or a label built from its match) has to be
# added here on purpose.
SCANNER_LABELS = frozenset(
    {
        "AWS access key",
        "Azure storage key",
        "Databricks token",
        "DigitalOcean token",
        "Discord bot token",
        "Docker PAT",
        "GitHub PAT",
        "GitHub token",
        "GitLab token",
        "Google API key",
        "Google OAuth token",
        "Hugging Face token",
        "JWT",
        "Linear key",
        "Netlify token",
        "OpenAI key",
        "PyPI token",
        "SendGrid key",
        "Shopify token",
        "Slack token",
        "Slack webhook URL",
        "Stripe key",
        "Stripe webhook secret",
        "Telegram bot token",
        "Twilio API key",
        "URL credentials",
        "authorization credential",
        "basic auth credential",
        "bearer credential",
        "credential field",
        "credential variable",
        "curl credential",
        "netrc credential",
        "npm token",
        "private key",
        "secret CLI argument",
        "secret assignment",
        "secret flag",
        "sshpass password",
    }
)


def test_the_scanner_labels_are_a_fixed_set_of_categories():
    labels = {
        label for label, _p in (*common._SECRET_SCANNERS, *common._CONTEXT_SCANNERS)
    }
    assert labels == SCANNER_LABELS


# ---------------------------------------------------------------------------
# Hook level: the shapes stop the command before any LLM call, value redacted
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command",
    [
        # (curl is denied outright by the hook before the scan; `curl -u` is
        # covered by the scanner tests above and by the editor / consultant gates.)
        f"sshpass -p {PW} ssh user@host",
        f"DB_PASS={PW} ./run",
        f"git clone https://:{PW}@host/r.git",
        f"MYSQL_PWD={PW} mysqldump db",
    ],
    ids=["sshpass", "DB_PASS", "empty-user-url", "MYSQL_PWD"],
)
def test_hook_asks_without_an_llm_call_and_never_echoes_the_value(run_hook, command):
    res = run_hook(HOOK, hook_payload(command))
    assert res.decision == "ask"
    assert "credential" in res.reason.lower()
    assert PW not in res.reason
    assert PW not in json.dumps(res.stdout)


# ---------------------------------------------------------------------------
# Linear time: bounded gaps and separator-delimited groups only (no possessive
# quantifiers / atomic groups: Python 3.9-3.10 cannot compile them)
#
# What these pin: the scan time grows linearly with the input, so a near-miss
# repeated through a long command cannot stall the hook. The regression to catch
# is a pattern that rescans the rest of the input at every near-miss, or tries
# every split of a long run of blanks: both are quadratic. Each test scans an
# input and one GROWTH times longer and compares their CPU time: linear work grows
# about GROWTH-fold, quadratic work about GROWTH**2-fold. The CPU time of this
# process (process_time) is not stretched by other processes on a loaded
# single-core CI runner, the best of a few tries drops one-off pauses, and a ratio
# does not depend on how fast the machine is.
# ---------------------------------------------------------------------------
GROWTH = 16
MAX_RATIO = 40  # linear: about 16; quadratic: about 256
TRIES = 3
SMALL = 2_000  # characters in the shorter input


def _cpu_seconds(text: str) -> float:
    started = time.process_time()
    _scan(text)
    return time.process_time() - started


def _assert_scan_is_linear(make) -> None:
    """make(scale) builds the input; make(GROWTH) is GROWTH times make(1)."""
    short, long_ = make(1), make(GROWTH)
    best_short = best_long = float("inf")
    for _ in range(TRIES):
        best_short = min(best_short, _cpu_seconds(short))
        best_long = min(best_long, _cpu_seconds(long_))
        if best_long <= MAX_RATIO * best_short:
            return
    ratio = best_long / max(best_short, 1e-9)
    pytest.fail(f"{GROWTH}x the input took {ratio:.0f}x the CPU time (quadratic?)")


@pytest.mark.parametrize(
    "unit",
    [
        "curl ",
        "curl -s ",
        "curl -u ",
        "curl -u a: ",
        "sshpass ",
        "sshpass -v ",
        "machine ",
        "machine h login ",
        "machine h login u ",
        "A_",
        "A_B_C_",
        "A_PASS",
        '"pwd" ',
        '"pass',
        "'" + "a" * 60 + "' ",
        "authorization: token a ",
        "authorization: ",
        "M" + "a" * 24 + ".bbbbbb.",
        "123456789:AA",
        "://:",
        "AccountKey=",
        "shpat_",
        "dapi",
        "nf",
    ],
    ids=repr,
)
def test_repeated_near_miss_input_is_scanned_in_linear_time(unit):
    repeats = max(1, SMALL // len(unit))
    _assert_scan_is_linear(lambda scale: unit * (repeats * scale))


@pytest.mark.parametrize(
    "head",
    [
        "curl -u",
        "curl -sSu",
        "curl --user",
        "curl",
        "sshpass -p",
        "machine",
        "DB_PASS",
        '"pwd"',
    ],
    ids=repr,
)
def test_a_long_blank_run_is_scanned_in_linear_time(head):
    # Long blanks between the pieces: an ambiguous split of the run (two `[ \t]*`
    # around an optional `=`) is quadratic in its length, so the run grows with
    # the input instead of the number of pieces.
    pieces = 8
    blanks = SMALL // pieces
    _assert_scan_is_linear(lambda scale: (head + " " * (blanks * scale)) * pieces)


def test_a_long_run_after_one_curl_is_linear():
    # Many `-u` after a single curl: the word gap is bounded, so only the first
    # few are ever considered, and none of them is `user:pass`.
    def make(scale: int) -> str:
        return "curl " + "-u " * (SMALL // 3 * scale)

    assert _scan(make(GROWTH)) == (False, "")
    _assert_scan_is_linear(make)


def test_trailing_a_pass_key_after_a_long_underscore_chain_is_linear():
    def make(scale: int) -> str:
        return "A_" * (SMALL // 2 * scale) + "PASS="

    assert _scan(make(GROWTH)) == (False, "")
    _assert_scan_is_linear(make)
