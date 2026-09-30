import json
import subprocess
import time
import zlib
from pathlib import Path


def session_name(working_dir: Path) -> str:
    crc = zlib.crc32(str(working_dir).encode("utf-8"))
    return f"swarmforge-{crc:08x}"


def _run(session, *args):
    return subprocess.run(
        ["herdr", "--session", session, *args],
        capture_output=True,
        text=True,
    )


def _run_json(session, *args):
    result = _run(session, *args)
    if not result.stdout.strip():
        return None, result
    try:
        return json.loads(result.stdout), result
    except json.JSONDecodeError:
        return None, result


def is_session_running(session) -> bool:
    return _run(session, "workspace", "list").returncode == 0


def ensure_session_server(session, timeout_s=10):
    """Start the headless server for a named herdr session if it is not
    already running, then wait for it to answer. herdr's named sessions do
    not lazily start on first command (unlike tmux -S <socket>); the server
    must be launched explicitly before any workspace/pane call will work.
    """
    if is_session_running(session):
        return
    subprocess.Popen(
        ["herdr", "--session", session, "server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_session_running(session):
            return
        time.sleep(0.2)
    raise RuntimeError(f"herdr session '{session}' did not become ready in time")


def workspace_create(session, cwd, label):
    payload, result = _run_json(
        session, "workspace", "create", "--cwd", str(cwd), "--label", label, "--no-focus",
    )
    if not payload or "result" not in payload:
        raise RuntimeError(
            f"herdr workspace create failed for '{label}': "
            f"{result.stdout.strip()} {result.stderr.strip()}"
        )
    r = payload["result"]
    return r["workspace"]["workspace_id"], r["root_pane"]["pane_id"]


def workspace_list(session):
    payload, _ = _run_json(session, "workspace", "list")
    if not payload or "result" not in payload:
        return []
    return payload["result"].get("workspaces", [])


def workspace_exists(session, workspace_id) -> bool:
    return _run(session, "workspace", "get", workspace_id).returncode == 0


def workspace_close(session, workspace_id):
    _run(session, "workspace", "close", workspace_id)


def pane_run(session, pane_id, command):
    _run(session, "pane", "run", pane_id, command)
