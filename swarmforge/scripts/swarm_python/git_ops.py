import shutil
from pathlib import Path

from .proc import sh, sh_out

# Paths the swarm puts in the project: the runtime state, the worktrees and
# the copy of the scripts that every role worktree carries. They are excluded
# from git so a worktree never looks dirty because of them (a dirty worktree
# blocks the post-merge sync, and with it the dispatch of the next feature).
RUNTIME_EXCLUDES = (".swarmforge/", ".worktrees/", "swarmforge/")


def ensure_in_file(file: Path, pattern: str):
    file.parent.mkdir(parents=True, exist_ok=True)
    if not file.exists():
        file.write_text("")
    lines = set(file.read_text(encoding="utf-8").splitlines())
    if pattern not in lines:
        with file.open("a", encoding="utf-8") as f:
            f.write(pattern + "\n")


def ensure_initial_gitignore(working_dir: Path):
    gitignore = working_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".swarmforge/\n.worktrees/\n")
        return
    ensure_in_file(gitignore, ".swarmforge/")
    ensure_in_file(gitignore, ".worktrees/")


def _tracked(working_dir: Path, pattern: str):
    """True when the project tracks that path on purpose (a vendored swarmforge/)."""
    out = sh_out(["git", "-C", str(working_dir), "ls-files", "--", pattern])
    return bool(out)


def ensure_runtime_git_excludes(working_dir: Path):
    """Keep the swarm's own paths out of git's way.

    `info/exclude` lives in the repository's COMMON dir, so the patterns apply
    to the master checkout AND to every worktree: one write covers them all.
    A path the project tracks on purpose (a vendored swarmforge/) is left
    alone: excluding a tracked path would only hide it from `git status`."""
    exclude_file = Path(
        sh_out(
            ["git", "-C", str(working_dir), "rev-parse", "--git-path", "info/exclude"]
        )
    )
    for pattern in RUNTIME_EXCLUDES:
        if _tracked(working_dir, pattern):
            continue
        ensure_in_file(exclude_file, pattern)


def init_repo_if_missing(working_dir: Path):
    if (working_dir / ".git").exists():
        return
    sh(["git", "init", str(working_dir)])
    sh(["git", "-C", str(working_dir), "branch", "-M", "master"])
    ensure_initial_gitignore(working_dir)
    sh(["git", "-C", str(working_dir), "add", "."])
    sh(["git", "-C", str(working_dir), "commit", "-m", "Initial swarmforge repository"])


def prepare_worktrees(working_dir: Path, roles):
    for r in roles:
        if r.worktree_name in ("none", "master"):
            continue
        if (r.worktree_path / ".git").exists():
            continue
        branch = f"swarmforge-{r.worktree_name}"
        sh(
            [
                "git",
                "-C",
                str(working_dir),
                "worktree",
                "add",
                "--force",
                "-B",
                branch,
                str(r.worktree_path),
                "HEAD",
            ]
        )


def prepare_handoff_dirs(roles):
    for r in roles:
        for sub in (
            "outbox/tmp",
            "sent",
            "failed",
            "inbox/new",
            "inbox/in_process",
            "inbox/completed",
        ):
            (r.worktree_path / ".swarmforge" / "handoffs" / sub).mkdir(
                parents=True, exist_ok=True
            )


def _sync_role_documents(source: Path, target: Path):
    """The role prompts and the constitution are read from the role's own
    worktree when its launch prompt is assembled. swarmforge/ is excluded from
    git, so a worktree never receives them by checkout: copy them here."""
    for name in ("roles", "constitution"):
        if (source / name).is_dir():
            shutil.copytree(source / name, target / name, dirs_exist_ok=True)
    if (source / "constitution.prompt").is_file():
        shutil.copy2(source / "constitution.prompt", target / "constitution.prompt")


def sync_worktree_scripts(script_dir: Path, state_dir: Path, roles, sessions_file, roles_file, herdr_session_file):
    for r in roles:
        if str(r.worktree_path) == str(state_dir.parent):
            continue
        role_scripts_dir = r.worktree_path / "swarmforge" / "scripts"
        role_state_dir = r.worktree_path / ".swarmforge"
        role_scripts_dir.mkdir(parents=True, exist_ok=True)
        for entry in script_dir.iterdir():
            target = role_scripts_dir / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, target)
        _sync_role_documents(script_dir.parent, r.worktree_path / "swarmforge")
        (role_state_dir / "notify").mkdir(parents=True, exist_ok=True)
        toolchain_src = state_dir / "toolchain"
        if toolchain_src.exists():
            shutil.copytree(
                toolchain_src,
                role_state_dir / "toolchain",
                dirs_exist_ok=True,
            )
        shutil.copy2(sessions_file, role_state_dir / "sessions.tsv")
        shutil.copy2(roles_file, role_state_dir / "roles.tsv")
        shutil.copy2(herdr_session_file, role_state_dir / "herdr-session")
