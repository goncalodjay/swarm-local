import os
import subprocess
from pathlib import Path


def sh(args):
    return subprocess.run(args, capture_output=True, text=True)


def sh_ok(args):
    return subprocess.run(args, capture_output=True).returncode == 0


def sh_out(args):
    return sh(args).stdout.strip()


def tmux_info(socket):
    return sh_ok(["tmux", "-S", socket, "info"])


def tmux_new_session(socket, session, window_name, command=None):
    args = ["tmux", "-S", socket, "new-session", "-d", "-s", session, "-n", window_name]
    if command:
        args.append(command)
    sh(args)


def tmux_rename_window(socket, session, old_window, new_name):
    sh(["tmux", "-S", socket, "rename-window", "-t", f"{session}:{old_window}", new_name])


def tmux_set_window_option(socket, session, window, option, value):
    sh(
        [
            "tmux",
            "-S",
            socket,
            "set-window-option",
            "-t",
            f"{session}:{window}",
            option,
            value,
        ]
    )


def tmux_send_keys(socket, target, *keys):
    sh(["tmux", "-S", socket, "send-keys", "-t", target, *keys])


def tmux_attach(socket, session):
    os.execvp("tmux", ["tmux", "-S", socket, "attach-session", "-t", session])


def tmux_display_message(socket, fmt):
    return sh_out(["tmux", "-S", socket, "display-message", "-p", fmt])


def tmux_option(socket, option, scope, default):
    args = ["tmux", "-S", socket, "show-options"]
    if scope == "session":
        args.extend(["-gqv", option])
    elif scope == "window":
        args.extend(["-gwqv", option])
    value = sh_out(args)
    if value.isdigit():
        return int(value)
    return default


def detect_base_indexes(socket, socket_dir):
    socket_dir.mkdir(parents=True, exist_ok=True)
    probe_session = None
    if not tmux_info(socket):
        probe_session = f"swarmforge-probe-{os.getpid()}"
        tmux_new_session(socket, probe_session, "swarm", "sleep 60")
    window_base = tmux_option(socket, "base-index", "session", 0)
    pane_base = tmux_option(socket, "pane-base-index", "window", 0)
    if probe_session:
        kill_tmux_session(socket, probe_session)
    return window_base, pane_base


def kill_tmux_session(socket, session):
    sh(["tmux", "-S", socket, "kill-session", "-t", session])


def create_role_session(socket, session, title):
    tmux_new_session(socket, session, "swarm")
    tmux_rename_window(socket, session, "swarm", title)
    tmux_set_window_option(socket, session, title, "allow-rename", "off")


def agent_target(window, pane_base_index, session):
    return f"{session}:{window}.{pane_base_index}"
