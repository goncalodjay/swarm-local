import os
import zlib
from dataclasses import dataclass
from pathlib import Path


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
    window_ids_file: Path
    window_state_file: Path
    window_watchdog_log: Path
    sessions_file: Path
    roles_file: Path
    prompts_dir: Path
    daemon_dir: Path
    handoff_daemon_log: Path
    tmux_socket_dir: Path
    tmux_socket: str
    tmux_socket_file: Path
    tmux_env_file: Path
    tmux_window_base_index: int = 0
    tmux_pane_base_index: int = 0
    terminal_backend: str = ""
    roles: tuple = ()


def build_context(working_dir: Path, script_dir: Path) -> Context:
    working_dir = working_dir.resolve()
    swarm_forge_dir = working_dir / "swarmforge"
    state_dir = working_dir / ".swarmforge"
    daemon_dir = state_dir / "daemon"
    uid = os.environ.get("UID")
    if not uid:
        uid = os.environ.get("USER") or "0"
    tmux_socket_dir = Path("/tmp") / f"swarmforge-{uid}"
    crc = zlib.crc32(str(working_dir).encode("utf-8"))
    tmux_socket = str(tmux_socket_dir / f"{crc}.sock")
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
        window_ids_file=state_dir / "window-ids",
        window_state_file=state_dir / "windows.tsv",
        window_watchdog_log=state_dir / "window-watchdog.log",
        sessions_file=state_dir / "sessions.tsv",
        roles_file=state_dir / "roles.tsv",
        prompts_dir=state_dir / "prompts",
        daemon_dir=daemon_dir,
        handoff_daemon_log=daemon_dir / "handoffd.log",
        tmux_socket_dir=tmux_socket_dir,
        tmux_socket=tmux_socket,
        tmux_socket_file=state_dir / "tmux-socket",
        tmux_env_file=state_dir / "tmux-env",
    )
