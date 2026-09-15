from dataclasses import dataclass
from pathlib import Path

from .herdr_ops import session_name as herdr_session_name


@dataclass(frozen=True)
class Context:
    working_dir: Path
    script_dir: Path
    swarm_forge_dir: Path
    worktrees_dir: Path
    config_file: Path
    roles_dir: Path
    constitution_file: Path
    state_dir: Path
    notify_dir: Path
    sessions_file: Path
    roles_file: Path
    prompts_dir: Path
    daemon_dir: Path
    handoff_daemon_log: Path
    herdr_session: str
    herdr_session_file: Path
    roles: tuple = ()


def build_context(working_dir: Path, script_dir: Path) -> Context:
    working_dir = working_dir.resolve()
    swarm_forge_dir = working_dir / "swarmforge"
    state_dir = working_dir / ".swarmforge"
    daemon_dir = state_dir / "daemon"
    return Context(
        working_dir=working_dir,
        script_dir=script_dir,
        swarm_forge_dir=swarm_forge_dir,
        worktrees_dir=working_dir / ".worktrees",
        config_file=swarm_forge_dir / "swarmforge.conf",
        roles_dir=swarm_forge_dir / "roles",
        constitution_file=swarm_forge_dir / "constitution.prompt",
        state_dir=state_dir,
        notify_dir=state_dir / "notify",
        sessions_file=state_dir / "sessions.tsv",
        roles_file=state_dir / "roles.tsv",
        prompts_dir=state_dir / "prompts",
        daemon_dir=daemon_dir,
        handoff_daemon_log=daemon_dir / "handoffd.log",
        herdr_session=herdr_session_name(working_dir),
        herdr_session_file=state_dir / "herdr-session",
    )
