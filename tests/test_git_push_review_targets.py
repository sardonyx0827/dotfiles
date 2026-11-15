"""Tests for what git-push-review.sh says about WHERE a push goes and WHAT it sends.

Two defects are pinned here (both variants, .claude JSON-ask and .codex exit-2):

1. The commit list followed `@{upstream}..HEAD` whatever remote/refspec the push named,
   so `git push backup HEAD:main` against an empty second remote said "(none)" while the
   remote received every commit, and a new branch cut from origin/main listed five
   commits that were not its own. The list now follows the push target, labels itself an
   estimate from local refs as of the last fetch, and never reports "(none)" for a
   target it only approximates.
2. `git push && cd B && git push` summarised the hook's cwd repository only, although the
   second push runs in B. More than one push with a directory change between them is now
   reported as "target repository could not be determined", the same way two pushes with
   different -C directories already were.

The fixtures use real local bare remotes and real tracking refs: nothing here touches
the network or a real remote. Any "tracking ref" is created by a real push to a local
bare repository or by `update-ref`, exactly as `git fetch` would leave it.
"""

import concurrent.futures
import itertools
import os
import re
import shutil
import subprocess
import time

import pytest
from conftest import run_git
from test_git_push_review import (
    VARIANTS,
    _push_reason,
    _repo_at,
    _summary_text,
    payload,
)

ESTIMATE = "as of the last fetch"
NOTE = "could not be determined"


# --- fixtures -----------------------------------------------------------------------
def _commit(repo, subject):
    """One commit with its own file, so every subject is a distinct commit."""
    name = re.sub(r"\W+", "-", subject) + ".txt"
    (repo / name).write_text(subject + "\n", encoding="utf-8")
    run_git(repo, "add", name)
    run_git(repo, "commit", "-q", "-m", subject)


def _bare(base, name):
    path = base / f"{name}.git"
    run_git(base, "init", "-q", "--bare", "-b", "main", str(path))
    return path


@pytest.fixture
def world(tmp_path, git_repo):
    """git_repo (main: initial, second, third) pushed to a bare `origin` (up to date,
    upstream set) plus an EMPTY bare `backup` that main has never been sent to."""
    _commit(git_repo, "second commit")
    _commit(git_repo, "third commit")
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    origin = _bare(remotes, "origin")
    backup = _bare(remotes, "backup")
    run_git(git_repo, "remote", "add", "origin", str(origin))
    run_git(git_repo, "remote", "add", "backup", str(backup))
    run_git(git_repo, "push", "-q", "-u", "origin", "main")
    return {"repo": git_repo, "origin": origin, "backup": backup}


def _listed(reason):
    """The commit subjects the reason lists (oldest last, as `git log` prints them)."""
    return re.findall(r"(?m)^[0-9a-f]{7,40} (.+)$", reason)


def _bare_count(bare):
    return int(run_git(bare, "rev-list", "--count", "main").strip())


def _hook(shell_env, hook, variant, cmd, cwd):
    return _push_reason(shell_env, hook, variant, cmd, cwd)


# --- (b) an explicit remote with no tracking ref ------------------------------------
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push backup HEAD:main",
        "git push backup main",
        "git push backup HEAD",
        "git push -u backup main",
        "git push --force-with-lease backup main",
        "git push backup refs/heads/main:refs/heads/main",
        "git push backup +main:main",
    ],
)
def test_a_remote_with_no_tracking_ref_is_never_reported_as_having_nothing_to_send(
    shell_env, world, variant, hook, cmd
):
    # origin is up to date, backup is empty: the push sends all three commits.
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert _listed(reason) == ["third commit", "second commit", "initial commit"], (
        reason
    )
    assert "commits not on any backup ref known locally" in reason, reason
    assert "approximate" in reason, reason
    assert ESTIMATE in reason, reason
    assert "(none)" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_list_for_an_untracked_remote_matches_what_the_real_push_sends(
    shell_env, world, variant, hook
):
    reason = _hook(shell_env, hook, variant, "git push backup HEAD:main", world["repo"])
    # backup was empty, so the commits it holds afterwards are exactly the ones sent
    run_git(world["repo"], "push", "-q", "backup", "HEAD:main")
    assert _bare_count(world["backup"]) == len(_listed(reason)), reason


# --- (a) an explicit remote WITH a tracking ref -------------------------------------
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push backup HEAD:main",
        "git push backup main",
        "git push backup main:main",
        "git push backup refs/heads/main:refs/heads/main",
        "git push backup +HEAD:main",
        "git push --force backup main",
        "git push -u backup HEAD",
    ],
)
def test_a_remote_with_a_tracking_ref_lists_the_commits_past_it(
    shell_env, world, variant, hook, cmd
):
    # backup already has everything but the newest commit (a real push, so the
    # tracking ref is the one git itself leaves behind).
    run_git(world["repo"], "push", "-q", "backup", "HEAD~1:refs/heads/main")
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert _listed(reason) == ["third commit"], reason
    assert "refs/remotes/backup/main" in reason, reason
    assert ESTIMATE in reason, reason
    assert "approximate" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_list_for_a_tracked_remote_matches_what_the_real_push_sends(
    shell_env, world, variant, hook
):
    run_git(world["repo"], "push", "-q", "backup", "HEAD~1:refs/heads/main")
    before = _bare_count(world["backup"])
    reason = _hook(shell_env, hook, variant, "git push backup HEAD:main", world["repo"])
    run_git(world["repo"], "push", "-q", "backup", "HEAD:main")
    assert _bare_count(world["backup"]) - before == len(_listed(reason)), reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_tracked_remote_that_is_up_to_date_says_none(shell_env, world, variant, hook):
    reason = _hook(shell_env, hook, variant, "git push origin main", world["repo"])
    assert _listed(reason) == [], reason
    assert "(none)" in reason, reason
    assert "refs/remotes/origin/main" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_destination_name_picks_the_tracking_ref(shell_env, world, variant, hook):
    repo = world["repo"]
    run_git(repo, "update-ref", "refs/remotes/backup/release", "HEAD~2")
    reason = _hook(shell_env, hook, variant, "git push backup main:release", repo)
    assert _listed(reason) == ["third commit", "second commit"], reason
    assert "refs/remotes/backup/release" in reason, reason
    # ... and a destination with no tracking ref of its own is not compared with the
    # release ref: it falls back to "not on any backup ref known locally" (the initial
    # commit is already on refs/remotes/backup/release, so it is not listed)
    reason = _hook(shell_env, hook, variant, "git push backup main:other", repo)
    assert _listed(reason) == ["third commit", "second commit"], reason
    assert "commits not on any backup ref known locally" in reason, reason
    assert "refs/remotes/backup/release" not in reason, reason


# --- a new branch cut from origin/main ----------------------------------------------
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push -u origin feat",
        "git push -u origin HEAD",
        "git push -u origin feat:feat",
        "git push --set-upstream origin feat",
        "git push -u origin",
    ],
)
def test_a_new_branch_pushed_with_dash_u_lists_only_its_own_commits(
    shell_env, world, variant, hook, cmd
):
    repo = world["repo"]
    run_git(repo, "checkout", "-q", "-b", "feat", "origin/main")
    _commit(repo, "feature work")
    reason = _hook(shell_env, hook, variant, cmd, repo)
    assert _listed(reason) == ["feature work"], reason
    assert ESTIMATE in reason, reason
    assert "(none)" not in reason, reason


# --- a default push (no remote, no refspec) -----------------------------------------
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_default_push_lists_the_commits_past_its_upstream(
    shell_env, world, variant, hook
):
    _commit(world["repo"], "fourth commit")
    _commit(world["repo"], "fifth commit")
    reason = _hook(shell_env, hook, variant, "git push", world["repo"])
    assert _listed(reason) == ["fifth commit", "fourth commit"], reason
    assert "refs/remotes/origin/main" in reason, reason
    assert ESTIMATE in reason, reason
    assert "approximate" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("config_key", ["branch.main.pushRemote", "remote.pushDefault"])
def test_a_default_push_follows_the_push_remote_not_the_upstream(
    shell_env, world, variant, hook, config_key
):
    # push.default is left unset ("simple"), which is what most people have: git cannot
    # name a single @{push} here (the push remote and the upstream differ), yet a real
    # `git push` sends main to backup under its own name.
    repo = world["repo"]
    run_git(repo, "config", config_key, "backup")
    reason = _hook(shell_env, hook, variant, "git push", repo)
    assert _listed(reason) == ["third commit", "second commit", "initial commit"], (
        reason
    )
    assert "commits not on any backup ref known locally" in reason, reason
    assert "(none)" not in reason, reason
    # ... and once backup has a tracking ref, the list is exact against it
    run_git(repo, "push", "-q", "backup", "HEAD~1:refs/heads/main")
    reason = _hook(shell_env, hook, variant, "git push", repo)
    assert _listed(reason) == ["third commit"], reason
    assert "refs/remotes/backup/main" in reason, reason
    assert "approximate" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_default_push_to_the_push_remote_matches_what_the_real_push_sends(
    shell_env, world, variant, hook
):
    repo = world["repo"]
    run_git(repo, "config", "branch.main.pushRemote", "backup")
    reason = _hook(shell_env, hook, variant, "git push", repo)
    run_git(repo, "push", "-q")
    assert _bare_count(world["backup"]) == len(_listed(reason)), reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_push_default_current_resolves_the_push_ref_through_at_push(
    shell_env, world, variant, hook
):
    repo = world["repo"]
    run_git(repo, "config", "push.default", "current")
    run_git(repo, "config", "branch.main.pushRemote", "backup")
    run_git(repo, "push", "-q", "backup", "HEAD~1:refs/heads/main")
    reason = _hook(shell_env, hook, variant, "git push", repo)
    assert _listed(reason) == ["third commit"], reason
    assert "refs/remotes/backup/main" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_default_push_with_nothing_to_resolve_is_labelled_approximate(
    shell_env, git_repo, variant, hook
):
    reason = _hook(shell_env, hook, variant, "git push", git_repo)
    assert "approximate" in reason, reason
    assert ESTIMATE in reason, reason
    assert _listed(reason) == ["initial commit"], reason


# --- the cap: more than 10 commits --------------------------------------------------
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "extra,more",
    [(9, None), (10, None), (11, "(+1 more)"), (13, "(+3 more)")],
)
def test_a_list_past_ten_commits_says_how_many_more(
    shell_env, world, variant, hook, extra, more
):
    for i in range(extra):
        _commit(world["repo"], f"bulk {i:02d}")
    reason = _hook(shell_env, hook, variant, "git push", world["repo"])
    assert len(_listed(reason)) == min(extra, 10), reason
    if more is None:
        assert "more)" not in reason, reason
    else:
        assert more in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_cap_applies_to_a_remote_with_no_tracking_ref(
    shell_env, world, variant, hook
):
    for i in range(13):
        _commit(world["repo"], f"bulk {i:02d}")
    # 16 commits in all, none of them on backup
    reason = _hook(shell_env, hook, variant, "git push backup HEAD:main", world["repo"])
    assert len(_listed(reason)) == 10, reason
    assert "(+6 more)" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_cap_applies_to_the_list_that_is_not_what_the_push_sends(
    shell_env, world, variant, hook
):
    for i in range(12):
        _commit(world["repo"], f"bulk {i:02d}")
    reason = _hook(shell_env, hook, variant, "git push --tags origin", world["repo"])
    assert "NOT necessarily" in reason, reason
    assert len(_listed(reason)) == 10, reason
    assert "(+2 more)" in reason, reason


# --- (d) everything else keeps the approximate / foreign labelling ------------------
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push origin main main",
        "git push origin main:main HEAD:other",
        "git push origin HEAD:refs/tags/v1",
        "git push origin HEAD:refs/for/main",
        "git push origin HEAD:HEAD",
        'git push origin "HEAD:$DEST"',
        "git push --unknown-flag origin main",
    ],
)
def test_an_unclear_target_is_approximate_and_cites_no_remote_ref(
    shell_env, world, variant, hook, cmd
):
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert "refs/remotes/" not in reason, reason
    assert "approximate" in reason or "NOT necessarily" in reason, reason
    assert ESTIMATE in reason, reason
    # an approximation is never presented as "nothing to send"
    assert "(none)" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_remote_given_as_a_path_is_not_looked_up_as_a_remote_name(
    shell_env, world, variant, hook
):
    cmd = f"git push {world['backup']} main"
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert "refs/remotes/" not in reason, reason
    assert "approximate" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_detached_head_does_not_borrow_the_remote_head_ref(
    shell_env, world, variant, hook
):
    repo = world["repo"]
    run_git(repo, "checkout", "-q", "--detach")
    # `origin HEAD` has no destination name to look up ...
    reason = _hook(shell_env, hook, variant, "git push origin HEAD", repo)
    assert "refs/remotes/origin/HEAD" not in reason, reason
    assert "approximate" in reason, reason
    # ... while an explicit destination still does
    reason = _hook(shell_env, hook, variant, "git push origin HEAD:main", repo)
    assert "refs/remotes/origin/main" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_push_in_a_chain_gets_its_own_commit_section(
    shell_env, world, variant, hook
):
    reason = _hook(
        shell_env,
        hook,
        variant,
        "git push origin main && git push backup main",
        world["repo"],
    )
    target_tags = re.findall(r"push target \[(\d)/(\d)\]:", reason)
    commit_tags = re.findall(r"commits to push \[(\d)/(\d)\]:", reason)
    assert target_tags == [("1", "2"), ("2", "2")], reason
    assert commit_tags == target_tags, reason
    first, second = reason.split("commits to push [2/2]:")
    assert "refs/remotes/origin/main" in first.split("commits to push [1/2]:")[1], (
        reason
    )
    assert "commits not on any backup ref known locally" in second, reason
    assert _listed(second) == ["third commit", "second commit", "initial commit"], (
        reason
    )


# Every label owns up to being an estimate, whichever branch of the logic made it.
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push",
        "git push origin main",
        "git push backup main",
        "git push --tags origin",
        "git push origin main main",
        'eval "git push origin main"',
        "git push origin main && git push backup main",
    ],
)
def test_every_commit_list_says_it_is_an_estimate_from_local_refs(
    shell_env, world, variant, hook, cmd
):
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert reason.count(ESTIMATE) >= max(1, reason.count("push target")), reason


# =============================================================================
# Several pushes with a directory change between them
# =============================================================================
@pytest.fixture
def two_repos(tmp_path):
    """A = the hook's cwd (branch cwd-branch), B = where a `cd` goes (target-branch)."""
    a = _repo_at(tmp_path / "repo-a", "A_MARKER", branch="cwd-branch")
    b = _repo_at(tmp_path / "repo-b", "B_MARKER", branch="target-branch")
    return a, b


def _fill(template, **values):
    """Substitute {A} / {B} by hand: str.format would choke on a shell `{ ...; }` group."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _assert_multi_note(reason, *, why=None):
    assert NOTE in reason, reason
    assert "A_MARKER" not in reason, reason
    assert "B_MARKER" not in reason, reason
    assert "branch:" not in reason, reason
    if why:
        assert why in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "template",
    [
        "git push && cd {B} && git push",
        "git push; cd {B}; git push",
        "git push origin a && cd {B} && git push origin b",
        "git push \n cd {B} \n git push",
        "git push && { cd {B}; git push; }",
        "git push && (cd {B} && git push)",
        "git push && cd {B} && time git push",
        "time git push && cd {B} && git push",
        "git push && cd {B} && FOO=1 git push",
        "git push && cd {B} && bash -c 'git push'",
        'git push && cd {B} && eval "git push"',
        "git push && cd {B} && git send-pack origin main",
        "git push || cd {B} && git push",
        "git push && cd {B} || git push",
        "git push & cd {B} ; git push",
        "git push && cd ../repo-b && git push",
        "cd {A} && git push && cd {B} && git push",
        # pushd / popd move the shell like cd does
        "git push && pushd {B} && git push",
        "git push && pushd {B} > /dev/null; git push",
        "pushd {B} && git push && popd && git push",
        "git push && popd && git push",
        # `&` ends the list the cd belongs to: the first push runs in B, the second in A
        "cd {B} && git push & git push",
        "cd {B} && git push & git push origin main",
    ],
)
def test_two_pushes_with_a_cd_between_them_get_the_note(
    shell_env, two_repos, variant, hook, template
):
    a, b = two_repos
    cmd = _fill(template, A=a, B=b)
    reason = _hook(shell_env, hook, variant, cmd, a)
    _assert_multi_note(reason)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_note_for_two_pushes_names_the_reason(shell_env, two_repos, variant, hook):
    a, b = two_repos
    reason = _hook(shell_env, hook, variant, f"git push && cd {b} && git push", a)
    _assert_multi_note(reason, why="more than one push")
    # both pushes are still reported
    assert reason.count("push target") == 2, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "template",
    [
        "cd {B} && git push && git -C {A} push",
        "cd {B} && git push && git -C ../repo-a push",
    ],
)
def test_two_pushes_resolving_to_different_directories_get_the_note(
    shell_env, two_repos, variant, hook, template
):
    a, b = two_repos
    reason = _hook(shell_env, hook, variant, _fill(template, A=a, B=b), a)
    _assert_multi_note(reason)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "template,expect_marker",
    [
        # both pushes run in the same directory: still summarised
        ("git push && git push", "A_MARKER"),
        ("git push; git push origin main", "A_MARKER"),
        ("cd {B} && git push && git push", "B_MARKER"),
        ("cd {B} && git push; git push origin main", "B_MARKER"),
        ("git -C {B} push && git -C {B} push origin main", "B_MARKER"),
        # a cd AFTER the last push changes nothing about where the pushes ran
        ("cd {B} && git push && git push && cd {A}", "B_MARKER"),
        ("git push && git push && cd {B}", "A_MARKER"),
        # a lone push, with a cd before or after it (unchanged behaviour)
        ("cd {B} && git push", "B_MARKER"),
        ("git push && cd {B}", "A_MARKER"),
        # push in a commit message is just text
        ("git commit --allow-empty -m 'fix push' && cd {B} && git push", "B_MARKER"),
    ],
)
def test_pushes_that_share_a_directory_keep_their_summary(
    shell_env, two_repos, variant, hook, template, expect_marker
):
    a, b = two_repos
    reason = _hook(shell_env, hook, variant, _fill(template, A=a, B=b), a)
    assert expect_marker in reason, reason
    assert NOTE not in reason, reason


# --- differential: the hook's claim against a real shell ----------------------------
# For two pushes the claim must hold for BOTH of them. `{ pwd -P >> f; true; }` stands
# in for a push that succeeds and `{ pwd -P >> f; false; }` for one that fails: a push
# can fail, and `||` / `&&` then decide whether a later cd runs at all.
_SEPS = [" ; ", " && ", " || ", " & ", " | ", " \n "]


def _multi_push_templates():
    out = []
    for s1, s2 in itertools.product(_SEPS, _SEPS):
        out.append(f"{{PUSH}}{s1}cd {{D}}{s2}{{PUSH}}")
        out.append(f"cd {{D}}{s1}{{PUSH}}{s2}{{PUSH}}")
        out.append(f"{{PUSH}}{s1}{{PUSH}}{s2}cd {{D}}")
    return out


MULTI_PUSH_TEMPLATES = _multi_push_templates()


def _shell_cwds(argv, script, cwd, outfile):
    """Every directory the real shell was in when it reached a push stand-in."""
    try:
        outfile.unlink()
    except FileNotFoundError:
        pass
    subprocess.run(
        [*argv, script + "\nwait\n"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if not outfile.exists():
        return []
    return [ln for ln in outfile.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_multi_push_claims_hold_for_every_push_in_a_real_shell(
    shell_env, tmp_path, variant, hook
):
    cwd_dir = _repo_at(tmp_path / "cwd-repo", "CWD_MARKER", branch="cwd-branch")
    target_dir = _repo_at(
        tmp_path / "target-repo", "TARGET_MARKER", branch="target-branch"
    )
    cwd_real = os.path.realpath(cwd_dir)
    target_real = os.path.realpath(target_dir)
    shells = [("bash", ["/bin/bash", "-c"])]
    if shutil.which("zsh"):
        shells.append(("zsh", ["zsh", "-f", "-c"]))
    outdir = tmp_path / "oracle-out"
    outdir.mkdir()

    def _check_one(i, template):
        hook_cmd = template.format(D=str(target_dir), PUSH="git push")
        res = shell_env.run(hook, stdin=payload(hook_cmd), cwd=cwd_dir)
        reason = _summary_text(res, variant)
        if "TARGET_MARKER" in reason:
            claim = target_real
        elif "CWD_MARKER" in reason:
            claim = cwd_real
        else:
            return []  # a note: always acceptable
        violations = []
        for shell_name, argv in shells:
            for outcome in ("true", "false"):
                outfile = outdir / f"{i}-{shell_name}-{outcome}.out"
                stand_in = f'{{ pwd -P >> "{outfile}"; {outcome}; }}'
                script = template.format(D=str(target_dir), PUSH=stand_in)
                for actual in _shell_cwds(argv, script, cwd_dir, outfile):
                    if os.path.realpath(actual) != claim:
                        violations.append(
                            f"{shell_name}/{outcome}: hook claimed {claim!r} but a "
                            f"push ran in {actual!r} for: {hook_cmd!r}"
                        )
        return violations

    started = time.monotonic()
    violations = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_check_one, i, t) for i, t in enumerate(MULTI_PUSH_TEMPLATES)
        ]
        for fut in concurrent.futures.as_completed(futures):
            violations.extend(fut.result())
    assert not violations, (
        f"{variant}: {len(violations)} violation(s) among "
        f"{len(MULTI_PUSH_TEMPLATES)} shapes in {time.monotonic() - started:.1f}s:\n"
        + "\n".join(violations[:20])
    )


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_multi_push_sweep_is_not_vacuous(shell_env, tmp_path, variant, hook):
    """The sweep above passes trivially if the hook only ever answers with a note.

    Shapes where both pushes provably share a directory must still be summarised."""
    cwd_dir = _repo_at(tmp_path / "cwd-repo", "CWD_MARKER", branch="cwd-branch")
    target_dir = _repo_at(
        tmp_path / "target-repo", "TARGET_MARKER", branch="target-branch"
    )
    res = shell_env.run(
        hook,
        stdin=payload(f"cd {target_dir} && git push && git push"),
        cwd=cwd_dir,
    )
    assert "TARGET_MARKER" in _summary_text(res, variant)
    res = shell_env.run(hook, stdin=payload("git push ; git push"), cwd=cwd_dir)
    assert "CWD_MARKER" in _summary_text(res, variant)


# =============================================================================
# The reason stays bounded however many pushes the command chains
# =============================================================================
# One section per push made the approval text grow linearly (871 B for 1 push, 14 KB for
# 20, ~110 KB for 190), each push repeating the same commit list. The first five pushes
# are shown in full; the rest collapse into one line, but a force / delete among them is
# still called out, because the reader is told to review the command itself and a
# `--force` buried in push #50 must not be the thing that disappears.
SHOWN_PUSHES = 5
REASON_BUDGET_BYTES = 20_000


def _chain(n, push="git push"):
    return " && ".join([push] * n)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("count", [1, 2, SHOWN_PUSHES])
def test_up_to_five_pushes_are_each_shown_in_full(
    shell_env, world, variant, hook, count
):
    # unchanged behaviour: one `push target` line and one commit section per push,
    # identical ones included, and no "more pushes" line
    reason = _hook(shell_env, hook, variant, _chain(count), world["repo"])
    assert reason.count("push target") == count, reason
    assert reason.count("commits to push") == count, reason
    assert "more pushes" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_sixth_push_is_collapsed_into_one_line(shell_env, world, variant, hook):
    # six different targets, so the sixth cannot be called a repeat of one shown above
    cmd = " && ".join(f"git push r{i} a{i}" for i in range(SHOWN_PUSHES + 1))
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert reason.count("push target") == SHOWN_PUSHES, reason
    assert reason.count("commits on the current branch") == SHOWN_PUSHES, reason
    assert reason.count("more pushes") >= 1, reason
    assert "(+1 more pushes not summarised -- review the command itself)" in reason, (
        reason
    )


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        _chain(200),
        # every push different, so nothing can be deduplicated
        " && ".join(f"git push r{i} a{i}" for i in range(150)),
        " && ".join(f"git push backup HEAD:b{i}" for i in range(110)),
        _chain(100, "git push --force origin main"),
    ],
    ids=["identical", "distinct", "distinct-untracked", "identical-force"],
)
def test_many_chained_pushes_keep_the_reason_small(
    shell_env, world, variant, hook, cmd
):
    assert len(cmd) <= 4000, "the command must stay inside the hook's parsing limit"
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert len(reason.encode("utf-8")) < REASON_BUDGET_BYTES, len(reason)
    assert reason.count("push target") == SHOWN_PUSHES, reason
    assert "more pushes not summarised -- review the command itself" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_identical_pushes_past_the_fifth_say_they_repeat_a_section(
    shell_env, world, variant, hook
):
    reason = _hook(shell_env, hook, variant, _chain(30), world["repo"])
    assert reason.count("commits to push") == SHOWN_PUSHES, reason
    assert "(+25 more pushes not summarised -- review the command itself" in reason, (
        reason
    )
    assert "25 of them repeat a section above" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_distinct_push_past_the_fifth_is_not_called_a_repeat(
    shell_env, world, variant, hook
):
    cmd = _chain(SHOWN_PUSHES) + " && git push backup main"
    reason = _hook(shell_env, hook, variant, cmd, world["repo"])
    assert "(+1 more pushes not summarised -- review the command itself)" in reason, (
        reason
    )
    assert "repeat a section" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "hidden_push,needle",
    [
        ("git push --force origin main", "--force"),
        ("git push -f origin main", "--force"),
        ("git push origin :old", "delete refspec"),
        ("git push --delete origin old", "--delete"),
        ("git push --mirror origin", "--mirror"),
    ],
)
def test_a_destructive_push_hidden_past_the_fifth_is_still_called_out(
    shell_env, world, variant, hook, hidden_push, needle
):
    parts = ["git push"] * 49 + [hidden_push] + ["git push"] * 20
    reason = _hook(shell_env, hook, variant, " && ".join(parts), world["repo"])
    assert len(reason.encode("utf-8")) < REASON_BUDGET_BYTES, len(reason)
    warning = [ln for ln in reason.splitlines() if ln.startswith("WARNING")]
    assert len(warning) == 1, reason
    assert "1 of the pushes not listed" in warning[0], reason
    assert needle in warning[0], reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_hidden_pushes_with_an_unverifiable_target_are_counted(
    shell_env, world, variant, hook
):
    parts = ["git push"] * 10 + ['git push origin "$B"', "git push o x --bogus-flag"]
    reason = _hook(shell_env, hook, variant, " && ".join(parts), world["repo"])
    notes = [ln for ln in reason.splitlines() if "pushes not listed" in ln]
    assert any("2 of the pushes not listed" in ln for ln in notes), reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_five_shown_pushes_still_carry_their_own_warnings(
    shell_env, world, variant, hook
):
    parts = ["git push --force origin main"] + ["git push"] * 9
    reason = _hook(shell_env, hook, variant, " && ".join(parts), world["repo"])
    assert "WARNING [1/10]: force/destructive push options: --force" in reason, reason
    assert "pushes not listed" not in reason.split("WARNING [1/10]")[1].split("\n")[0]


# =============================================================================
# `remote.<r>.push` changes what a push without a refspec sends
# =============================================================================
# `git config remote.origin.push main:deploy` makes `git push origin` (and a bare
# `git push` that resolves to origin) send main to `deploy`. The list must never be built
# as if the destination were the branch of the same name: against refs/remotes/origin/main
# it says "(none)" while the push creates a whole new branch. The configured refspec is
# followed only when it is one plain `src[:dst]` whose src is the current branch; anything
# else is an approximation, labelled as one.
def _bare_ref(bare, ref):
    return run_git(bare, "for-each-ref", "--format=%(objectname)", ref).strip()


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_premise_a_configured_push_refspec_sends_main_to_deploy(
    shell_env, world, variant, hook
):
    repo = world["repo"]
    run_git(repo, "config", "remote.origin.push", "main:deploy")
    run_git(repo, "push", "-q", "origin")
    head = run_git(repo, "rev-parse", "HEAD").strip()
    assert _bare_ref(world["origin"], "refs/heads/deploy") == head


# the ways a push can arrive at origin without a refspec on the command line
_NO_REFSPEC_FORMS = [
    ("git push origin", {}),
    ("git push", {"branch.main.pushRemote": "origin"}),
    ("git push", {"remote.pushDefault": "origin"}),
]
_NO_REFSPEC_IDS = ["remote-only", "bare-pushRemote", "bare-pushDefault"]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd,extra_config", _NO_REFSPEC_FORMS, ids=_NO_REFSPEC_IDS)
@pytest.mark.parametrize(
    "refspec",
    ["main:deploy", "+main:deploy", "HEAD:deploy", "refs/heads/main:refs/heads/deploy"],
)
def test_a_configured_push_refspec_with_a_tracking_ref_lists_against_it(
    shell_env, world, variant, hook, cmd, extra_config, refspec
):
    # (git's own `@{push}` only understands the full-name spelling; the short ones are
    # the hook's own reading of the setting)
    repo = world["repo"]
    run_git(repo, "config", "remote.origin.push", refspec)
    for key, value in extra_config.items():
        run_git(repo, "config", key, value)
    run_git(repo, "push", "-q", "origin", "HEAD~1:refs/heads/deploy")
    reason = _hook(shell_env, hook, variant, cmd, repo)
    assert _listed(reason) == ["third commit"], reason
    assert "refs/remotes/origin/deploy" in reason, reason
    assert "refs/remotes/origin/main" not in reason, reason
    assert ESTIMATE in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd,extra_config", _NO_REFSPEC_FORMS, ids=_NO_REFSPEC_IDS)
@pytest.mark.parametrize(
    "refspec",
    ["main:deploy", "+main:deploy", "refs/heads/main:refs/heads/deploy", "HEAD:deploy"],
)
def test_a_configured_push_refspec_without_a_tracking_ref_is_never_none(
    shell_env, world, variant, hook, cmd, extra_config, refspec
):
    # origin/main is up to date, so a list against it is empty; the push, though, creates
    # origin/deploy from scratch
    repo = world["repo"]
    run_git(repo, "config", "remote.origin.push", refspec)
    for key, value in extra_config.items():
        run_git(repo, "config", key, value)
    reason = _hook(shell_env, hook, variant, cmd, repo)
    assert "(none)" not in reason, reason
    assert "refs/remotes/origin/main" not in reason, reason
    assert "approximate" in reason, reason
    assert ESTIMATE in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd,extra_config", _NO_REFSPEC_FORMS, ids=_NO_REFSPEC_IDS)
@pytest.mark.parametrize(
    "configured",
    [
        ["other:deploy"],  # not the current branch
        ["refs/heads/*:refs/heads/deploy-*"],  # a wildcard (main -> deploy-main)
        [":deploy"],  # a delete
        ["main:refs/tags/v1"],  # not a plain branch on the other side
        ["main:refs/for/main"],
        ["main:deploy", "main:backup"],  # more than one value
        ["main:main", "main:deploy"],
    ],
    ids=[
        "other-src",
        "wildcard",
        "delete",
        "tag-dst",
        "for-dst",
        "two-values",
        "main-and-deploy",
    ],
)
def test_a_push_refspec_that_is_not_one_plain_branch_is_approximate(
    shell_env, world, variant, hook, cmd, extra_config, configured
):
    repo = world["repo"]
    for value in configured:
        run_git(repo, "config", "--add", "remote.origin.push", value)
    for key, value in extra_config.items():
        run_git(repo, "config", key, value)
    reason = _hook(shell_env, hook, variant, cmd, repo)
    assert "(none)" not in reason, reason
    assert "refs/remotes/origin/main" not in reason, reason
    assert "approximate" in reason, reason
    assert ESTIMATE in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_wildcard_refspec_that_git_resolves_itself_is_trusted(
    shell_env, world, variant, hook
):
    # `refs/heads/*:refs/heads/*` maps main to main, and `@{push}` says so: nothing to
    # second-guess, and the tracking ref it names exists
    repo = world["repo"]
    run_git(repo, "config", "remote.origin.push", "refs/heads/*:refs/heads/*")
    _commit(repo, "fourth commit")
    reason = _hook(shell_env, hook, variant, "git push origin", repo)
    assert _listed(reason) == ["fourth commit"], reason
    assert "refs/remotes/origin/main" in reason, reason
    assert "approximate" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_push_refspec_of_another_remote_does_not_apply(
    shell_env, world, variant, hook
):
    # backup has the odd refspec; the push goes to origin, whose answer is unchanged
    repo = world["repo"]
    run_git(repo, "config", "remote.backup.push", "main:deploy")
    reason = _hook(shell_env, hook, variant, "git push origin", repo)
    assert "refs/remotes/origin/main" in reason, reason
    assert "(none)" in reason, reason
    assert "approximate" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd,extra_config", _NO_REFSPEC_FORMS, ids=_NO_REFSPEC_IDS)
def test_without_a_push_refspec_the_branch_name_is_still_the_destination(
    shell_env, world, variant, hook, cmd, extra_config
):
    # unchanged behaviour: no remote.<r>.push, so main goes to origin/main
    repo = world["repo"]
    for key, value in extra_config.items():
        run_git(repo, "config", key, value)
    _commit(repo, "fourth commit")
    reason = _hook(shell_env, hook, variant, cmd, repo)
    assert _listed(reason) == ["fourth commit"], reason
    assert "refs/remotes/origin/main" in reason, reason
    assert "approximate" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_an_explicit_refspec_on_the_command_line_ignores_the_configured_one(
    shell_env, world, variant, hook
):
    repo = world["repo"]
    run_git(repo, "config", "remote.origin.push", "main:deploy")
    _commit(repo, "fourth commit")
    reason = _hook(shell_env, hook, variant, "git push origin main", repo)
    assert _listed(reason) == ["fourth commit"], reason
    assert "refs/remotes/origin/main" in reason, reason
