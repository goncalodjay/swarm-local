#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import dataclasses
import os
import subprocess
import time
from dataclasses import replace

from swarm_python.ansi import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from swarm_python.config import AGENT_WINDOW, RoleRow, parse_config
from swarm_python.deps import check_backends, command_exists, require
from swarm_python.env import env_long
from swarm_python.git_ops import (
    init_repo_if_missing,
    prepare_handoff_dirs,
    prepare_worktrees,
    sync_worktree_scripts,
    ensure_runtime_git_excludes,
)
from swarm_python.launch import build_launch_command, send_launch_command
from swarm_python.paths import build_context
from swarm_python.sleep_inhibit import prefix as sleep_inhibit_prefix
from swarm_python.terminal import (
    detect_backend,
    terminal_ok,
    terminal_out,
)
from swarm_python.tmux_ops import (
    agent_target,
    create_role_session,
    detect_base_indexes,
    kill_tmux_session,
    tmux_attach,
    tmux_display_message,
)
from swarm_python.terminal import tmux_has_session
from swarm_python.tsv import write_tsv

HANDOFFD = Path(__file__).resolve().parent / "handoffd.py"
STOP_DAEMON = Path(__file__).resolve().parent / "stop_handoff_daemon.py"
WINDOW_WATCHDOG = Path(__file__).resolve().parent / "window_watchdog.py"

REQUIRED_SH_HELPERS = [
    "swarmforge.sh",
    "swarm_handoff.sh",
    "ready_for_next.sh",
    "done_with_current.sh",
    "stop_handoff_daemon.sh",
    "swarm-cleanup.sh",
    "swarm-window-watchdog.sh",
    "swarm-terminal-adapter.sh",
]

REQUIRED_PY_HELPERS = [
    "swarm_cli.py",
    "swarm_handoff.py",
    "ready_for_next.py",
    "done_with_current.py",
    "handoffd.py",
    "stop_handoff_daemon.py",
    "window_watchdog.py",
]

TERMINAL_HELPERS = [
    "terminal-app.sh",
    "iterm2.sh",
    "ghostty.sh",
    "windows-terminal.sh",
    "none.sh",
]


def fail(message):
    print(f"{RED}Error:{RESET} {message}", file=sys.stderr)
    sys.exit(1)


def check_helpers(ctx):
    for helper in REQUIRED_SH_HELPERS:
        path = ctx.script_dir / helper
        if not (path.exists() and os.access(path, os.X_OK)):
            fail(f"Required helper script not found or not executable: {path}")
    entrypoints_dir = ctx.script_dir / "swarm_python" / "entrypoints"
    for helper in REQUIRED_PY_HELPERS:
        path = entrypoints_dir / helper
        if not (path.exists() and os.access(path, os.X_OK)):
            fail(f"Required Python helper not found or not executable: {path}")
    for helper in TERMINAL_HELPERS:
        path = ctx.script_dir / "terminal-adapters" / helper
        if not (path.exists() and os.access(path, os.X_OK)):
            fail(f"Required terminal adapter not found or not executable: {path}")


def write_sessions_tsv(ctx, roles):
    rows = [
        [str(i + 1), r.role, r.session, r.display_name, r.agent]
        for i, r in enumerate(roles)
    ]
    write_tsv(ctx.sessions_file, rows)


def write_roles_tsv(ctx, roles):
    rows = [
        [
            r.role,
            r.worktree_name,
            str(r.worktree_path),
            r.session,
            r.display_name,
            r.agent,
            r.receive_mode,
        ]
        for r in roles
    ]
    write_tsv(ctx.roles_file, rows)


def prepare_workspace(ctx, roles):
    for d in (
        ctx.state_dir,
        ctx.notify_dir,
        ctx.prompts_dir,
        ctx.worktrees_dir,
        ctx.tmux_socket_dir,
        ctx.daemon_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    ctx.tmux_socket_file.write_text(ctx.tmux_socket + "\n", encoding="utf-8")
    check_helpers(ctx)
    write_sessions_tsv(ctx, roles)
    write_roles_tsv(ctx, roles)


def write_tmux_env_file(ctx):
    value = tmux_display_message(ctx.tmux_socket, "#{socket_path},#{pid},#{pane_id}")
    ctx.tmux_env_file.write_text(value + "\n", encoding="utf-8")


def stop_handoff_daemon(ctx):
    subprocess.run(
        [sys.executable, str(STOP_DAEMON), str(ctx.working_dir)],
        capture_output=True,
    )


def start_handoff_daemon(ctx):
    (ctx.daemon_dir / "stop").unlink(missing_ok=True)
    command = list(sleep_inhibit_prefix())
    command += [str(HANDOFFD), str(ctx.working_dir)]
    log = open(ctx.handoff_daemon_log, "ab")
    try:
        subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()
    extra = " with OS sleep prevention" if len(command) > 2 else ""
    print(f"{GREEN}Started handoff daemon{extra}.{RESET}")


def launch_role(ctx, index, row: RoleRow):
    prompt_file = ctx.prompts_dir / f"{row.role}.md"
    command = build_launch_command(ctx, index, row)
    target = agent_target(row.display_name, ctx.tmux_pane_base_index, row.session)
    send_launch_command(ctx.tmux_socket, target, command)
    print(
        f"  {CYAN}[{row.display_name}]{RESET} started in session {row.session}"
    )


def kill_existing_sessions(ctx, roles):
    for r in roles:
        if tmux_has_session(ctx.tmux_socket, r.session):
            print(
                f"{YELLOW}Existing SwarmForge session found: {r.session}. "
                f"Killing it...{RESET}"
            )
            kill_tmux_session(ctx.tmux_socket, r.session)


def open_terminal_surfaces(ctx):
    if not terminal_ok(
        ctx.script_dir,
        str(ctx.working_dir),
        ctx.tmux_socket,
        ctx.terminal_backend,
        "terminal_backend_can_open_sessions",
    ):
        print(
            f"{YELLOW}No terminal backend found; attaching current shell to "
            f"'{ctx.roles[0].session}' instead.{RESET}"
        )
        tmux_attach(ctx.tmux_socket, ctx.roles[0].session)
        return

    label = terminal_out(
        ctx.script_dir,
        str(ctx.working_dir),
        ctx.tmux_socket,
        ctx.terminal_backend,
        "terminal_backend_label",
    )
    print(f"Opening separate {label} surfaces for each session...")

    tracks = terminal_ok(
        ctx.script_dir,
        str(ctx.working_dir),
        ctx.tmux_socket,
        ctx.terminal_backend,
        "terminal_backend_tracks_windows",
    )
    if tracks:
        ctx.window_ids_file.write_text("", encoding="utf-8")
        ctx.window_state_file.write_text("", encoding="utf-8")

    previous_window_id = ""
    for i, r in enumerate(ctx.roles):
        window_id = terminal_out(
            ctx.script_dir,
            str(ctx.working_dir),
            ctx.tmux_socket,
            ctx.terminal_backend,
            "terminal_open_session",
            r.session,
            f"SwarmForge {r.display_name}",
            previous_window_id,
        )
        if tracks:
            with ctx.window_ids_file.open("a", encoding="utf-8") as f:
                f.write(window_id + "\n")
            with ctx.window_state_file.open("a", encoding="utf-8") as f:
                f.write(f"{i + 1}\t{window_id}\t{r.session}\tSwarmForge {r.display_name}\n")
            previous_window_id = window_id

    if tracks:
        log = open(ctx.window_watchdog_log, "ab")
        try:
            subprocess.Popen(
                [
                    str(WINDOW_WATCHDOG),
                    str(ctx.window_state_file),
                    str(ctx.window_ids_file),
                    "1",
                    ctx.tmux_socket,
                    str(ctx.working_dir),
                    ctx.terminal_backend,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            log.close()
    else:
        print(
            f"{YELLOW}{label} surfaces are not trackable; window watchdog is "
            f"disabled for this backend.{RESET}"
        )


def run_main(root: str):
    require("tmux")
    require("git")

    ctx = build_context(Path(root).resolve(), Path(__file__).resolve().parent.parent.parent)

    w_base, p_base = detect_base_indexes(ctx.tmux_socket, ctx.tmux_socket_dir)
    ctx = replace(ctx, tmux_window_base_index=w_base, tmux_pane_base_index=p_base)

    init_repo_if_missing(ctx.working_dir)
    ensure_runtime_git_excludes(ctx.working_dir)

    roles = parse_config(ctx)
    check_backends(roles)

    prepare_workspace(ctx, roles)
    prepare_worktrees(ctx.working_dir, roles)
    prepare_handoff_dirs(roles)

    ctx = replace(ctx, terminal_backend=detect_backend(), roles=roles)

    stop_handoff_daemon(ctx)
    kill_existing_sessions(ctx, roles)

    print(CYAN + BOLD)
    print("  SwarmForge v1.0 Starting")
    print("  Disciplined agents build better software")
    print(RESET)
    print(f"{GREEN}Launching SwarmForge tmux sessions...{RESET}")

    for r in roles:
        create_role_session(ctx.tmux_socket, r.session, r.display_name)

    write_tmux_env_file(ctx)
    sync_worktree_scripts(
        ctx.script_dir,
        ctx.state_dir,
        roles,
        ctx.sessions_file,
        ctx.roles_file,
        ctx.tmux_socket_file,
        ctx.tmux_env_file,
    )
    start_handoff_daemon(ctx)

    print(f"{GREEN}Starting agents...{RESET}")
    delay = env_long("SWARMFORGE_AGENT_START_DELAY_MS", 1500)
    for i, r in enumerate(roles):
        if i > 0:
            time.sleep(delay / 1000)
        launch_role(ctx, i, r)

    print()
    print(f"{GREEN}{BOLD}SwarmForge is ready.{RESET}")
    print(f"Working directory: {ctx.working_dir}")
    print("Sessions:")
    for r in roles:
        print(f"  {r.display_name}: {r.session}")
    print()
    print(f"{GREEN}Tip: Write a handoff draft and run swarm_handoff.sh while "
          f"the swarm is running.{RESET}")
    print(f"{GREEN}Tip: Reattach manually with 'tmux -S {ctx.tmux_socket} "
          f"attach-session -t <session-name>' if needed.{RESET}")
    print()

    open_terminal_surfaces(ctx)


def run_tui(root: str):
    tui_bundle = Path(root) / ".swarmforge" / "tui" / "swarm-tui.js"
    if not tui_bundle.exists():
        fail(f"TUI bundle not found at {tui_bundle}")
    print(f"{GREEN}Starting SwarmForge TUI in {RESET}{root}")
    os.chdir(str(root))
    os.execvp("node", ["node", str(tui_bundle)])


def test_parse(root: str):
    ctx = build_context(Path(root).resolve(), Path(__file__).resolve().parent.parent.parent)
    roles = parse_config(ctx)
    prepare_workspace(ctx, roles)
    for r in roles:
        extra = f" {r.extra_args}" if r.extra_args else ""
        print(f"{r.role} {r.display_name} {r.worktree_path} {r.receive_mode}{extra}")
    sys.stdout.write(ctx.roles_file.read_text(encoding="utf-8"))
    sys.stdout.write(ctx.sessions_file.read_text(encoding="utf-8"))


def test_terminal_bridge(root: str, backend: str):
    ctx = build_context(Path(root).resolve(), Path(__file__).resolve().parent.parent.parent)
    local_script_dir = ctx.working_dir / "swarmforge" / "scripts"
    if local_script_dir.exists():
        ctx = replace(ctx, script_dir=local_script_dir)
    out = terminal_out(
        ctx.script_dir,
        str(ctx.working_dir),
        ctx.tmux_socket,
        backend,
        "terminal_open_session",
        "swarmforge-specifier",
        "SwarmForge Specifier",
        "",
    )
    print(out)


def test_tmux_base_indexes(tmux_socket: str):
    w_base, p_base = detect_base_indexes(
        tmux_socket, Path(tmux_socket).parent
    )
    print(w_base, p_base)


def test_launch_command(root: str, agent: str, extra_args: str = ""):
    ctx = build_context(Path(root).resolve(), Path(__file__).resolve().parent.parent.parent)
    ctx = replace(ctx, terminal_backend="none")
    row = RoleRow(
        role="coder",
        agent=agent,
        session="swarmforge-coder",
        display_name="Coder",
        worktree_name="master",
        worktree_path=ctx.working_dir,
        receive_mode="task",
        extra_args=extra_args or None,
    )
    ctx.prompts_dir.mkdir(parents=True, exist_ok=True)
    print(build_launch_command(ctx, 1, row))


def test_agent_start_delay():
    print(env_long("SWARMFORGE_AGENT_START_DELAY_MS", 1500))


def test_sleep_inhibitor_prefix():
    print(" ".join(sleep_inhibit_prefix()))


def main():
    args = sys.argv[1:]
    cwd = str(Path.cwd())

    if not args:
        run_main(cwd)
        return

    cmd = args[0]
    if cmd == "tui":
        run_tui(args[1] if len(args) > 1 else cwd)
    elif cmd == "--test-parse":
        test_parse(args[1] if len(args) > 1 else cwd)
    elif cmd == "--test-terminal-bridge":
        test_terminal_bridge(args[1] if len(args) > 1 else cwd, args[2])
    elif cmd == "--test-launch-command":
        root = args[1] if len(args) > 1 else cwd
        test_launch_command(root, args[2], args[3] if len(args) > 3 else "")
    elif cmd == "--test-agent-start-delay":
        test_agent_start_delay()
    elif cmd == "--test-sleep-inhibitor-prefix":
        test_sleep_inhibitor_prefix()
    elif cmd == "--test-tmux-base-indexes":
        test_tmux_base_indexes(args[1])
    else:
        run_main(cmd)


if __name__ == "__main__":
    main()
