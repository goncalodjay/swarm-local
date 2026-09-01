#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import subprocess
import time

from swarm_python.terminal import (
    kill_tmux_session,
    terminal_ok,
    terminal_out,
    tmux_has_session,
)

MISSING_THRESHOLD = 3
STOP_HELP_DAEMON = Path(__file__).resolve().parent / "stop_handoff_daemon.py"


def _read_rows(window_state_file: Path) -> list[dict]:
    if not window_state_file.exists():
        return []
    out = []
    for line in window_state_file.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) < 4:
            continue
        out.append(
            {
                "index": fields[0],
                "window-id": fields[1],
                "session": fields[2],
                "title": fields[3],
            }
        )
    return out


def _write_rows(window_state_file: Path, window_ids_file: Path, rows: list[dict]):
    state_lines = []
    id_lines = []
    for r in rows:
        state_lines.append(
            f"{r['index']}\t{r['window-id']}\t{r['session']}\t{r['title']}\n"
        )
        if r["window-id"]:
            id_lines.append(f"{r['window-id']}\n")
    window_state_file.write_text("".join(state_lines), encoding="utf-8")
    window_ids_file.write_text("".join(id_lines), encoding="utf-8")


def rewrite_window_id(
    window_state_file: Path, window_ids_file: Path, target_index: str, replacement_id: str
):
    rows = _read_rows(window_state_file)
    for r in rows:
        if r["index"] == target_index:
            r["window-id"] = replacement_id
    _write_rows(window_state_file, window_ids_file, rows)


def _stop_handoff_daemon(script_dir: Path, working_dir: str):
    subprocess.run(
        [sys.executable, str(STOP_HELP_DAEMON), working_dir], capture_output=True
    )


def _kill_all_sessions(
    script_dir: Path,
    window_state_file: Path,
    working_dir: str,
    tmux_socket: str,
    backend: str,
):
    _stop_handoff_daemon(script_dir, working_dir)
    rows = _read_rows(window_state_file)
    for r in rows:
        if r["session"]:
            kill_tmux_session(tmux_socket, r["session"])
    for r in rows:
        if r["window-id"]:
            terminal_ok(
                script_dir,
                working_dir,
                tmux_socket,
                backend,
                "terminal_close_window",
                r["window-id"],
            )


def _check_and_reopen(
    script_dir: Path,
    window_state_file: Path,
    window_ids_file: Path,
    working_dir: str,
    tmux_socket: str,
    backend: str,
    rows: list[dict],
    missing_counts: dict,
    cleanup_owner_index: str,
    cleanup_window_id: str,
) -> dict:
    new_counts = dict(missing_counts)
    for r in rows:
        if r["index"] == cleanup_owner_index:
            continue
        if not tmux_has_session(tmux_socket, r["session"]):
            continue
        if terminal_ok(
            script_dir,
            working_dir,
            tmux_socket,
            backend,
            "terminal_window_exists",
            r["window-id"],
        ):
            new_counts[r["index"]] = 0
            continue
        count = new_counts.get(r["index"], 0) + 1
        if count < MISSING_THRESHOLD:
            new_counts[r["index"]] = count
            continue
        new_id = terminal_out(
            script_dir,
            working_dir,
            tmux_socket,
            backend,
            "terminal_open_session",
            r["session"],
            r["title"],
            cleanup_window_id,
        )
        if new_id:
            rewrite_window_id(
                window_state_file, window_ids_file, r["index"], new_id
            )
        new_counts[r["index"]] = 0
    return new_counts


def main():
    args = sys.argv[1:]

    if args and args[0] == "--rewrite-window-id":
        _, state, ids, target, replacement = args
        rewrite_window_id(Path(state), Path(ids), target, replacement)
        sys.exit(0)

    if len(args) < 5:
        print(
            "Usage: window_watchdog.py <window-state-file> <window-ids-file> "
            "<cleanup-owner-index> <tmux-socket> <working-dir> [backend]",
            file=sys.stderr,
        )
        sys.exit(1)

    window_state_file = Path(args[0])
    window_ids_file = Path(args[1])
    cleanup_owner_index = args[2]
    tmux_socket = args[3]
    working_dir = args[4]
    backend = args[5] if len(args) > 5 else "terminal-app"
    script_dir = Path(__file__).resolve().parent.parent.parent

    missing_counts: dict = {}
    while window_state_file.exists():
        current_rows = _read_rows(window_state_file)
        cleanup_row = next(
            (r for r in current_rows if r["index"] == cleanup_owner_index), None
        )
        if not cleanup_row or not tmux_has_session(tmux_socket, cleanup_row["session"]):
            time.sleep(2)
            continue

        cleanup_window_id = cleanup_row["window-id"]
        if terminal_ok(
            script_dir,
            working_dir,
            tmux_socket,
            backend,
            "terminal_window_exists",
            cleanup_window_id,
        ):
            missing_counts[cleanup_owner_index] = 0
            missing_counts = _check_and_reopen(
                script_dir,
                window_state_file,
                window_ids_file,
                working_dir,
                tmux_socket,
                backend,
                current_rows,
                missing_counts,
                cleanup_owner_index,
                cleanup_window_id,
            )
            time.sleep(2)
        else:
            count = missing_counts.get(cleanup_owner_index, 0) + 1
            if count >= MISSING_THRESHOLD:
                _kill_all_sessions(
                    script_dir,
                    window_state_file,
                    working_dir,
                    tmux_socket,
                    backend,
                )
            else:
                missing_counts[cleanup_owner_index] = count
                time.sleep(2)


if __name__ == "__main__":
    main()
