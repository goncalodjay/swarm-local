import shutil
from pathlib import Path

from .tmux_ops import sh, sh_out


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


def ensure_runtime_git_excludes(working_dir: Path):
    exclude_file = Path(
        sh_out(
            ["git", "-C", str(working_dir), "rev-parse", "--git-path", "info/exclude"]
        )
    )
    ensure_in_file(exclude_file, ".swarmforge/")
    ensure_in_file(exclude_file, ".worktrees/")


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


def sync_worktree_scripts(script_dir: Path, state_dir: Path, roles, sessions_file, roles_file, tmux_socket_file, tmux_env_file):
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
        shutil.copy2(tmux_socket_file, role_state_dir / "tmux-socket")
        shutil.copy2(tmux_env_file, role_state_dir / "tmux-env")
