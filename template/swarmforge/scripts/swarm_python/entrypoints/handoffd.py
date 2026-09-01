#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os
import signal
import subprocess
import threading
import time

from swarm_python.handoff.timefmt import now_iso

POLL_MS = 1000
WAKE_MESSAGE = "You have new handoff mail. If idle, run ready_for_next.sh."

_stopping = threading.Event()


def log(log_file, daemon_dir, *parts):
    daemon_dir.mkdir(parents=True, exist_ok=True)
    line = f"{now_iso()} {' '.join(str(p) for p in parts)}\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line)


def load_roles(roles_file):
    if not roles_file.exists():
        return {}
    out = {}
    for line in roles_file.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if not fields or not fields[0]:
            continue
        receive_mode = fields[6] if len(fields) > 6 and fields[6] else "task"
        out[fields[0]] = {
            "role": fields[0],
            "worktree-name": fields[1] if len(fields) > 1 else "",
            "worktree-path": fields[2] if len(fields) > 2 else "",
            "session": fields[3] if len(fields) > 3 else "",
            "display": fields[4] if len(fields) > 4 else "",
            "agent": fields[5] if len(fields) > 5 else "",
            "receive-mode": receive_mode,
        }
    return out


def parse_message(path):
    content = path.read_text(encoding="utf-8")
    parts = content.split("\n\n", 1)
    header_text = parts[0]
    body = parts[1] if len(parts) == 2 else ""
    headers = {}
    for line in header_text.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            if k:
                headers[k] = v
    return {"headers": headers, "body": body, "content": content}


def render_message(headers, body):
    preferred = [
        "id", "from", "to", "recipient", "priority", "type", "role", "commit",
        "message", "created_at", "enqueued_at", "dequeued_at", "completed_at",
    ]
    remaining = sorted(k for k in headers if k not in preferred)
    ordered_keys = [k for k in preferred if headers.get(k)] + remaining
    lines = [f"{k}: {headers[k]}" for k in ordered_keys if headers.get(k)]
    return "\n".join(lines) + "\n\n" + body


def target_path(role_info, filename):
    return (
        Path(role_info["worktree-path"])
        / ".swarmforge" / "handoffs" / "inbox" / "new" / filename
    )


def notify(socket, session):
    r1 = subprocess.run(
        ["tmux", "-S", socket, "send-keys", "-t", session, "-l", WAKE_MESSAGE],
        capture_output=True,
    )
    time.sleep(0.15)
    r2 = subprocess.run(
        ["tmux", "-S", socket, "send-keys", "-t", session, "C-m"],
        capture_output=True,
    )
    time.sleep(0.05)
    r3 = subprocess.run(
        ["tmux", "-S", socket, "send-keys", "-t", session, "C-j"],
        capture_output=True,
    )
    if r1.returncode != 0:
        raise RuntimeError("tmux send text failed")
    if r2.returncode != 0:
        raise RuntimeError("tmux send carriage return failed")
    if r3.returncode != 0:
        raise RuntimeError("tmux send line feed failed")


def move_with_collision(source, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    base = source.name
    target = target_dir / base
    if target.exists():
        target = target_dir / f"{now_iso()}_{base}"
    source.rename(target)


def fail_(path, reason, log_file, daemon_dir):
    failed_dir = path.parent.parent / "failed"
    log(log_file, daemon_dir, "failed", str(path), reason)
    error_path = path.parent / (path.name + ".error")
    error_path.write_text(reason + "\n", encoding="utf-8")
    move_with_collision(path, failed_dir)


def deliver(roles, socket, sender_role, path, log_file, daemon_dir):
    filename = path.name
    message = parse_message(path)
    headers = message["headers"]
    to_str = headers.get("to")
    if not to_str:
        fail_(path, "missing to header", log_file, daemon_dir)
        return
    recipients = [r for r in to_str.split(",") if r]
    for recipient in recipients:
        role_info = roles.get(recipient)
        if role_info is None:
            raise RuntimeError(f"unknown recipient {recipient}")
        target = target_path(role_info, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            delivered_headers = dict(headers)
            delivered_headers["recipient"] = recipient
            delivered_headers["enqueued_at"] = now_iso()
            target.write_text(
                render_message(delivered_headers, message["body"]),
                encoding="utf-8",
            )
        notify(socket, role_info["session"])
    sender_info = roles.get(sender_role)
    if sender_info:
        sent_dir = (
            Path(sender_info["worktree-path"])
            / ".swarmforge" / "handoffs" / "sent"
        )
        move_with_collision(path, sent_dir)
    log(log_file, daemon_dir, "delivered", str(path))


def outbox_files(role_info):
    outbox = (
        Path(role_info["worktree-path"])
        / ".swarmforge" / "handoffs" / "outbox"
    )
    if not outbox.exists():
        return []
    files = []
    for entry in sorted(outbox.iterdir(), key=lambda p: p.name):
        if entry.is_file() and entry.name.endswith(".handoff"):
            files.append(entry)
    return files


def should_stop(stop_file):
    return _stopping.is_set() or stop_file.exists()


def sleep_poll(ms, stop_file):
    remaining = ms
    while remaining > 0 and not should_stop(stop_file):
        step = min(remaining, 100)
        time.sleep(step / 1000)
        remaining -= step


def poll_once(state_dir, daemon_dir, stop_file, log_file):
    if should_stop(stop_file):
        return
    roles_file = state_dir / "roles.tsv"
    socket_file = state_dir / "tmux-socket"
    roles = load_roles(roles_file)
    socket = socket_file.read_text(encoding="utf-8").strip()
    for role, role_info in roles.items():
        if should_stop(stop_file):
            break
        for path in outbox_files(role_info):
            if should_stop(stop_file):
                break
            try:
                deliver(roles, socket, role, path, log_file, daemon_dir)
            except Exception as e:
                log(log_file, daemon_dir, "error", str(path), str(e))
                try:
                    fail_(path, str(e), log_file, daemon_dir)
                except Exception as nested:
                    log(
                        log_file,
                        daemon_dir,
                        "failed-to-archive",
                        str(path),
                        str(nested),
                    )


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: handoffd.py <project-root>", file=sys.stderr)
        sys.exit(1)
    project_root = Path(args[0]).resolve()
    state_dir = project_root / ".swarmforge"
    daemon_dir = state_dir / "daemon"
    pid_file = daemon_dir / "handoffd.pid"
    stop_file = daemon_dir / "stop"
    log_file = daemon_dir / "handoffd.log"

    signal.signal(signal.SIGTERM, lambda *_: _stopping.set())
    signal.signal(signal.SIGINT, lambda *_: _stopping.set())

    daemon_dir.mkdir(parents=True, exist_ok=True)
    stop_file.unlink(missing_ok=True)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    log(log_file, daemon_dir, "started")
    try:
        while not should_stop(stop_file):
            poll_once(state_dir, daemon_dir, stop_file, log_file)
            sleep_poll(POLL_MS, stop_file)
    finally:
        pid_file.unlink(missing_ok=True)
        log(log_file, daemon_dir, "stopped")


if __name__ == "__main__":
    main()
