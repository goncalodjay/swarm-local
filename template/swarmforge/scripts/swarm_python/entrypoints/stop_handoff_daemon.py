#!/usr/bin/env python3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT_MS = 5000
POLL_MS = 100


def process_alive(pid):
    return subprocess.run(["kill", "-0", pid], capture_output=True).returncode == 0


def stop(project_root, timeout_ms=DEFAULT_TIMEOUT_MS):
    daemon_dir = Path(project_root) / ".swarmforge" / "daemon"
    pid_file = daemon_dir / "handoffd.pid"
    stop_file = daemon_dir / "stop"
    daemon_dir.mkdir(parents=True, exist_ok=True)
    if not stop_file.exists():
        stop_file.write_text("")
    if pid_file.exists():
        pid = pid_file.read_text(encoding="utf-8").strip()
        if pid.isdigit():
            if process_alive(pid):
                subprocess.run(["kill", "-TERM", pid], capture_output=True)
                waited = 0
                while waited < timeout_ms and process_alive(pid):
                    time.sleep(POLL_MS / 1000)
                    waited += POLL_MS
                if process_alive(pid):
                    subprocess.run(["kill", "-KILL", pid], capture_output=True)
                    time.sleep(POLL_MS / 1000)
        pid_file.unlink(missing_ok=True)
    stop_file.unlink(missing_ok=True)


def usage():
    print("Usage: stop_handoff_daemon.py <project-root>", file=sys.stderr)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        usage()
    stop(args[0])
    sys.exit(0)


if __name__ == "__main__":
    main()
