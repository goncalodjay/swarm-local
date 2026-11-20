"""Git and worktree helpers shared by the plan flow and the handoff gate.

Two different moments need the same answers about a role worktree:

* after a merge, the swarm brings the checkout and the role worktrees back to
  the merged base commit, so the next feature starts from the merged result;
* when a feature STARTS, the handoff gate must prove that the coder's
  worktree descends from the base commit the handoff carries. A coder that
  keeps its own pre-merge history would stack the next feature on commits the
  base branch does not contain, and the PR would be refused at publish time
  (after the whole code/review/architect cycle was already spent).

Both live here so the rules cannot drift apart. Nothing in this module
raises: every failure is reported as (ok, message), and the messages are
written for the agent or the human that has to act on them.
"""
import datetime
import os
import subprocess
from pathlib import Path

GIT_TIMEOUT = 30

# Untracked paths the swarm itself puts in every role worktree (a copy of the
# scripts and the runtime state). They must never make a worktree look dirty,
# otherwise the post-merge sync would be deferred forever and the next
# feature would never be dispatched. Tracked changes anywhere, including
# inside these paths, still block.
SWARM_OWNED_UNTRACKED = ("swarmforge", ".swarmforge")

# Status kinds of `git status --porcelain --ignored`.
TRACKED = "tracked"
UNTRACKED = "untracked"
IGNORED = "ignored"
OTHER = "other"


def git(*args, cwd=None, timeout=GIT_TIMEOUT):
    """Run git and return (rc, out, err). Never raises."""
    argv = ["git", *args]
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        return 127, "", "git not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {timeout}s"
    except (subprocess.SubprocessError, OSError) as e:
        return 126, "", f"git failed to run: {e}"
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def resolve(rev, cwd=None):
    """Full hash of `rev`, or '' when it does not resolve to a commit."""
    rc, out, _ = git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", cwd=cwd)
    return out if rc == 0 and out else ""


def head_commit(path):
    """Full hash of the worktree's HEAD, or '' when there is none."""
    return resolve("HEAD", cwd=path)


def branch_of(path):
    """The branch checked out in the worktree ('' or 'HEAD' when detached)."""
    rc, out, _ = git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    return out if rc == 0 else ""


def is_ancestor(older, newer, cwd=None):
    """True when `older` is an ancestor of `newer` (or the same commit)."""
    if not older or not newer:
        return False
    rc, _, _ = git("merge-base", "--is-ancestor", older, newer, cwd=cwd)
    return rc == 0


def descends_from(base, commit, cwd=None):
    """True when `commit` contains `base` in its history."""
    return is_ancestor(base, commit, cwd=cwd)


def trees_equal(left, right, cwd=None):
    """True when both commits have exactly the same content."""
    if not left or not right:
        return False
    rc, _, _ = git("diff", "--quiet", f"{left}^{{tree}}", f"{right}^{{tree}}", cwd=cwd)
    return rc == 0


def roles_from_tsv(path):
    """role -> {role, worktree-name, worktree-path} from roles.tsv.

    roles.tsv is the file the daemon and the CLI read: it says which worktree
    every role works in."""
    out = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        fields = line.split("\t")
        if fields and fields[0]:
            out[fields[0]] = {
                "role": fields[0],
                "worktree-name": fields[1] if len(fields) > 1 else "",
                "worktree-path": fields[2] if len(fields) > 2 else "",
            }
    return out


def swarm_owned_untracked(porcelain_path):
    """True for a `git status --porcelain` untracked path under SWARM_OWNED_UNTRACKED."""
    path = porcelain_path.strip().strip('"').replace("\\", "/").rstrip("/")
    return any(path == owned or path.startswith(owned + "/") for owned in SWARM_OWNED_UNTRACKED)


def parse_status(output):
    """[(kind, path)] for a `git status --porcelain --ignored` output.

    The porcelain format is `XY <path>`: X is the index status and Y the
    worktree one, so the two characters are read as a pair (a file modified in
    the worktree only is " M", not "M"). kind is TRACKED (a modified or staged
    file), UNTRACKED, IGNORED or OTHER. The swarm-owned untracked copies are
    reported as UNTRACKED: the caller decides whether they matter."""
    entries = []
    for line in str(output or "").splitlines():
        if not line.strip():
            continue
        code = line[:2]
        name = line[3:].strip() if len(line) > 3 else ""
        if code == "??":
            entries.append((UNTRACKED, name))
        elif code == "!!":
            entries.append((IGNORED, name))
        elif code.strip() and set(code.strip()) <= set("MADRCUT"):
            entries.append((TRACKED, name))
        else:
            entries.append((OTHER, name))
    return entries


def status_entries(path):
    """([(kind, path)], error) for the worktree status; the list is None on
    failure."""
    rc, out, err = git("status", "--porcelain", "--ignored", cwd=path)
    if rc != 0:
        return None, err or "git status failed"
    return parse_status(out), ""


def cleanliness(path):
    """(ok, reason): may this worktree be moved to another commit?

    Tracked changes, uncommitted work of the agent and untracked files that
    are not the swarm's own copies all block. The swarm's copies
    (swarmforge/, .swarmforge/) never block."""
    entries, err = status_entries(path)
    if entries is None:
        return False, f"git status failed ({err})"
    for kind, name in entries:
        if kind == IGNORED:
            continue
        if kind == UNTRACKED and swarm_owned_untracked(name):
            continue
        if kind == TRACKED:
            return False, f"has uncommitted changes in '{name}'"
        if kind == UNTRACKED:
            return False, f"has an untracked file '{name}'"
        return False, f"has an unclean git state ('{name}')"
    return True, ""


def backup_ref(path, role, tip):
    """Keep the local commits a reset would orphan under refs/swarmforge/backup.

    Returns (ref, error). An empty ref means the backup failed."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ref = f"refs/swarmforge/backup/{role}/{stamp}"
    rc, _, err = git("update-ref", ref, tip, cwd=path)
    if rc != 0:
        return "", err or "git update-ref failed"
    return ref, ""


def fast_forward(path, base):
    """Move the worktree's branch to `base` with a fast-forward only."""
    rc, _, err = git("merge", "--ff-only", "-q", base, cwd=path)
    return rc == 0, err


def reset_to(path, branch, base):
    """Force the worktree's branch onto `base` (the branch keeps its name)."""
    rc, _, err = git("checkout", "-q", "-B", branch, base, cwd=path)
    return rc == 0, err


def ensure_descends(path, branch, base, role="coder", force=False):
    """Make the worktree's HEAD descend from `base`.

    Returns (ok, message). When ok is True the worktree already contained the
    base commit and nothing was touched. When ok is False the worktree could
    not be brought to the base safely and `message` says what blocks it: the
    work is never destroyed to make the check pass.

    `force` is the explicit human command (`swarm plan rebase`): the branch is
    reset onto the base even when its content differs from the base, and the
    commits that are dropped are kept in a backup ref.
    """
    head = head_commit(path)
    if not head:
        return False, f"the {role} worktree has no HEAD commit yet"
    if head == base or descends_from(base, head, cwd=path):
        return True, ""
    clean, why = cleanliness(path)
    if not clean:
        return False, (
            f"the {role} worktree {why}; commit, stash or discard that work before "
            f"starting the feature"
        )
    current = branch_of(path)
    if current != branch:
        where = "a detached HEAD" if current in ("", "HEAD") else f"'{current}'"
        return False, (
            f"the {role} worktree is on {where}, not '{branch}'; switch it back to "
            f"'{branch}' before starting the feature"
        )
    if descends_from(head, base, cwd=path):
        # Behind the base: a fast-forward is enough and keeps every local commit.
        ok, err = fast_forward(path, base)
        if ok:
            return True, ""
        return False, (
            f"the {role} worktree could not be fast-forwarded to the base commit "
            f"{base[:10]} ({err})"
        )
    if trees_equal(head, base, cwd=path) or force:
        # Same content, different history: the local commits are the pre-merge
        # duplicates of what the base already holds (a squash merge). Reset the
        # branch onto the base, keeping the old commits in a backup ref.
        ref, err = backup_ref(path, role, head)
        if not ref:
            return False, (
                f"the {role} worktree has local commits that do not descend from the base "
                f"commit {base[:10]} and they could not be backed up ({err})"
            )
        ok, err = reset_to(path, branch, base)
        if ok:
            return True, f"the {role} worktree was reset onto {base[:10]} (old commits kept in {ref})"
        return False, (
            f"the {role} worktree could not be reset onto the base commit {base[:10]} ({err}); "
            f"its previous commits are in {ref}"
        )
    return False, (
        f"the {role} worktree has local commits that do not descend from the base commit "
        f"{base[:10]} (its HEAD is {head[:10]}); reconcile it by hand (rebase or reset) and "
        f"hand the feature off again"
    )
