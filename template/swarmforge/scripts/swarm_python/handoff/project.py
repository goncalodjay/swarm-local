import subprocess
import sys
from pathlib import Path


def _git_root_or_common() -> Path | None:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        root = Path(r.stdout.strip())
        if (root / ".swarmforge" / "roles.tsv").exists():
            return root
    r = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        common = Path(r.stdout.strip())
        if not common.is_absolute():
            common = (Path.cwd() / common).resolve()
        candidate = common.parent
        if (candidate / ".swarmforge" / "roles.tsv").exists():
            return candidate
    return None


def project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / ".swarmforge" / "roles.tsv").exists():
        return cwd
    found = _git_root_or_common()
    if found is None:
        print("Cannot find SwarmForge project root", file=sys.stderr)
        sys.exit(1)
    return found


def state_dir() -> Path:
    return Path.cwd() / ".swarmforge" / "handoffs"


def inbox_dir() -> Path:
    return state_dir() / "inbox"


def roles_file() -> Path:
    return project_root() / ".swarmforge" / "roles.tsv"
