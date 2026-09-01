import os
import subprocess

from .deps import command_exists


def _uname():
    r = subprocess.run(["uname", "-s"], capture_output=True, text=True)
    return r.stdout.strip()


def _linux_systemd_running():
    r = subprocess.run(
        ["systemctl", "is-system-running"], capture_output=True, text=True
    )
    state = r.stdout.strip()
    return state in ("running", "degraded")


def prefix():
    if os.environ.get("SWARMFORGE_PREVENT_SLEEP") == "0":
        return []
    system = _uname()
    if system == "Darwin":
        if command_exists("caffeinate"):
            return ["caffeinate", "-dims"]
        return []
    if system == "Linux":
        inhibitor = [
            "systemd-inhibit",
            "--what=sleep:idle",
            "--who=SwarmForge",
            "--why=SwarmForge swarm is active",
        ]
        if not command_exists("systemd-inhibit"):
            return []
        if not command_exists("systemctl"):
            return []
        if not _linux_systemd_running():
            return []
        probe = subprocess.run(inhibitor + ["true"], capture_output=True)
        if probe.returncode != 0:
            return []
        return inhibitor
    return []
