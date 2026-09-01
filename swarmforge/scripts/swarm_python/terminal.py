import os
import shutil
import subprocess
from pathlib import Path


def normalize_backend(backend):
    b = backend.lower()
    if b in ("iterm", "iterm2", "iterm.app"):
        return "iterm2"
    if b in ("terminal", "terminal-app", "terminal.app"):
        return "terminal-app"
    if b in ("windows", "windows-terminal", "wt"):
        return "windows-terminal"
    if b in ("none", "current", "fallback"):
        return "none"
    return b


def detect_backend():
    env = os.environ.get("SWARMFORGE_TERMINAL")
    if env:
        return normalize_backend(env)
    if shutil.which("osascript"):
        if os.environ.get("TERM_PROGRAM") == "iTerm.app":
            return "iterm2"
        return "terminal-app"
    if shutil.which("wt.exe"):
        return "windows-terminal"
    return "none"


def adapter_script(script_dir, working_dir, tmux_socket, backend, command, *args):
    adapter_path = str(Path(script_dir) / "swarm-terminal-adapter.sh")
    quoted = [
        str(script_dir),
        str(working_dir),
        tmux_socket,
        adapter_path,
        backend,
        command,
    ]
    quoted.extend(str(a) for a in args)
    escaped = " ".join(_quote(x) for x in quoted)
    env_block = (
        f"SCRIPT_DIR={_quote(str(script_dir))}\n"
        f"WORKING_DIR={_quote(str(working_dir))}\n"
        f"TMUX_SOCKET={_quote(tmux_socket)}\n"
    )
    cmd_block = (
        f"source {_quote(adapter_path)} && "
        f"load_terminal_backend {_quote(backend)} && "
        f"{command}"
    )
    for a in args:
        cmd_block += " " + _quote(str(a))
    script = env_block + cmd_block
    return ["zsh", "-c", script]


def _quote(value):
    import shlex
    return shlex.quote(str(value))


def terminal_call(script_dir, working_dir, tmux_socket, backend, command, *args):
    return subprocess.run(
        adapter_script(script_dir, working_dir, tmux_socket, backend, command, *args),
        capture_output=True,
        text=True,
    )


def terminal_ok(script_dir, working_dir, tmux_socket, backend, command, *args):
    return (
        terminal_call(script_dir, working_dir, tmux_socket, backend, command, *args).returncode
        == 0
    )


def terminal_out(script_dir, working_dir, tmux_socket, backend, command, *args):
    return (
        terminal_call(script_dir, working_dir, tmux_socket, backend, command, *args)
        .stdout.strip()
    )


def tmux_has_session(socket, session):
    return (
        subprocess.run(
            ["tmux", "-S", socket, "has-session", "-t", session], capture_output=True
        ).returncode
        == 0
    )


def kill_tmux_session(socket, session):
    subprocess.run(
        ["tmux", "-S", socket, "kill-session", "-t", session], capture_output=True
    )
