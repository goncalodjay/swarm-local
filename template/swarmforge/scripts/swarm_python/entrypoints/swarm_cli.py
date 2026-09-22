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
from swarm_python.herdr_ops import (
    ensure_session_server,
    workspace_close,
    workspace_create,
    workspace_list,
)
from swarm_python.launch import build_launch_command, send_launch_command, write_launch_script
from swarm_python.paths import build_context
from swarm_python.sleep_inhibit import prefix as sleep_inhibit_prefix
from swarm_python.tsv import write_tsv

HANDOFFD = Path(__file__).resolve().parent / "handoffd.py"
STOP_DAEMON = Path(__file__).resolve().parent / "stop_handoff_daemon.py"

REQUIRED_SH_HELPERS = [
    "swarmforge.sh",
    "swarm_handoff.sh",
    "ready_for_next.sh",
    "done_with_current.sh",
    "stop_handoff_daemon.sh",
    "swarm-cleanup.sh",
]

REQUIRED_PY_HELPERS = [
    "swarm_cli.py",
    "swarm_handoff.py",
    "ready_for_next.py",
    "done_with_current.py",
    "handoffd.py",
    "stop_handoff_daemon.py",
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
            r.pane_id,
            r.workspace_id,
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
        ctx.daemon_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    ctx.herdr_session_file.write_text(ctx.herdr_session + "\n", encoding="utf-8")
    check_helpers(ctx)
    write_sessions_tsv(ctx, roles)
    write_roles_tsv(ctx, roles)


def stop_handoff_daemon(ctx):
    subprocess.run(
        [sys.executable, str(STOP_DAEMON), str(ctx.working_dir)],
        capture_output=True,
    )


def start_handoff_daemon(ctx):
    (ctx.daemon_dir / "stop").unlink(missing_ok=True)
    inhibit_prefix = sleep_inhibit_prefix()
    command = list(inhibit_prefix) + [sys.executable, str(HANDOFFD), str(ctx.working_dir)]
    log = open(ctx.handoff_daemon_log, "ab")
    try:
        subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()
    extra = " with OS sleep prevention" if inhibit_prefix else ""
    print(f"{GREEN}Started handoff daemon{extra}.{RESET}")


def launch_role(ctx, index, row: RoleRow):
    command = build_launch_command(ctx, index, row)
    script_path = write_launch_script(ctx, row.role, command)
    send_launch_command(ctx.herdr_session, row.pane_id, script_path)
    print(
        f"  {CYAN}[{row.display_name}]{RESET} started in workspace {row.workspace_id} "
        f"({row.pane_id})"
    )


def create_role_workspaces(ctx, roles):
    # cwd is ctx.working_dir, not the role's worktree: worktrees are created
    # by prepare_worktrees() right after this, and the launch command's own
    # `cd` moves each pane into its worktree once it exists.
    for r in roles:
        workspace_id, pane_id = workspace_create(ctx.herdr_session, ctx.working_dir, r.session)
        r.workspace_id = workspace_id
        r.pane_id = pane_id


def kill_existing_workspaces(ctx, roles):
    labels = {r.session for r in roles}
    for w in workspace_list(ctx.herdr_session):
        if w.get("label") in labels:
            print(
                f"{YELLOW}Existing SwarmForge workspace found: {w['label']}. "
                f"Closing it...{RESET}"
            )
            workspace_close(ctx.herdr_session, w["workspace_id"])


def run_main(root: str):
    require("herdr")
    require("git")
    require("engram")

    ctx = build_context(Path(root).resolve(), Path(__file__).resolve().parent.parent.parent)
    ensure_session_server(ctx.herdr_session)

    init_repo_if_missing(ctx.working_dir)
    ensure_runtime_git_excludes(ctx.working_dir)

    roles = parse_config(ctx)
    check_backends(roles)

    stop_handoff_daemon(ctx)
    kill_existing_workspaces(ctx, roles)

    print(CYAN + BOLD)
    print("  SwarmForge v1.0 Starting")
    print("  Disciplined agents build better software")
    print(RESET)
    print(f"{GREEN}Launching SwarmForge herdr workspaces...{RESET}")

    create_role_workspaces(ctx, roles)
    ctx = replace(ctx, roles=roles)

    prepare_workspace(ctx, roles)
    prepare_worktrees(ctx.working_dir, roles)
    prepare_handoff_dirs(roles)
    sync_worktree_scripts(
        ctx.script_dir,
        ctx.state_dir,
        roles,
        ctx.sessions_file,
        ctx.roles_file,
        ctx.herdr_session_file,
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
    print("Workspaces:")
    for r in roles:
        print(f"  {r.display_name}: {r.workspace_id} ({r.pane_id})")
    print()
    print(f"{GREEN}Tip: Write a handoff draft and run swarm_handoff.sh while "
          f"the swarm is running.{RESET}")
    print(f"{GREEN}Tip: View or attach with 'herdr session attach "
          f"{ctx.herdr_session}' if needed.{RESET}")
    print()


def run_tui(root: str):
    tui_dir = Path(root) / ".swarmforge" / "tui"
    tui_bundle = tui_dir / "swarm-tui"
    if not tui_bundle.exists():
        windows_bundle = tui_dir / "swarm-tui.exe"
        if windows_bundle.exists():
            tui_bundle = windows_bundle
        else:
            fail(f"TUI bundle not found at {tui_bundle}")
    print(f"{GREEN}Starting SwarmForge TUI in {RESET}{root}")
    os.chdir(str(root))
    # The TUI is a self-contained binary; it needs no interpreter on PATH.
    os.execv(str(tui_bundle), [str(tui_bundle)])


def test_parse(root: str):
    ctx = build_context(Path(root).resolve(), Path(__file__).resolve().parent.parent.parent)
    roles = parse_config(ctx)
    prepare_workspace(ctx, roles)
    for r in roles:
        extra = f" {r.extra_args}" if r.extra_args else ""
        print(f"{r.role} {r.display_name} {r.worktree_path} {r.receive_mode}{extra}")
    sys.stdout.write(ctx.roles_file.read_text(encoding="utf-8"))
    sys.stdout.write(ctx.sessions_file.read_text(encoding="utf-8"))


def test_launch_command(root: str, agent: str, extra_args: str = ""):
    ctx = build_context(Path(root).resolve(), Path(__file__).resolve().parent.parent.parent)
    row = RoleRow(
        role="coder",
        agent=agent,
        session="swarmforge-coder",
        display_name="Coder",
        worktree_name="master",
        worktree_path=ctx.working_dir,
        receive_mode="task",
        extra_args=extra_args or None,
        workspace_id="w1",
        pane_id="w1:p1",
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
    elif cmd == "--test-launch-command":
        root = args[1] if len(args) > 1 else cwd
        test_launch_command(root, args[2], args[3] if len(args) > 3 else "")
    elif cmd == "--test-agent-start-delay":
        test_agent_start_delay()
    elif cmd == "--test-sleep-inhibitor-prefix":
        test_sleep_inhibitor_prefix()
    else:
        run_main(cmd)


if __name__ == "__main__":
    main()
