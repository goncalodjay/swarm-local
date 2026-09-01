#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os

from swarm_python.handoff.format import header_value, set_header
from swarm_python.handoff.inbox import (
    batch_dirs,
    handoff_files,
    new_batch_dir,
    print_batch,
    print_task,
)
from swarm_python.handoff.project import inbox_dir
from swarm_python.handoff.roles import role_known, role_receive_mode
from swarm_python.handoff.timefmt import now_iso


def fail(status, *lines):
    for line in lines:
        print(line, file=sys.stderr)
    sys.exit(status)


def ensure_inbox_dirs():
    inbox = inbox_dir()
    new_dir = inbox / "new"
    in_process_dir = inbox / "in_process"
    completed_dir = inbox / "completed"
    for d in (new_dir, in_process_dir, completed_dir):
        d.mkdir(parents=True, exist_ok=True)
    return new_dir, in_process_dir


def run_task_mode():
    new_dir, in_process_dir = ensure_inbox_dirs()
    in_process_batches = batch_dirs(in_process_dir)
    in_process_files = handoff_files(in_process_dir)

    if in_process_batches:
        fail(
            2,
            "TASK_IN_PROCESS_IS_BATCH: use ready_for_next.sh or done_with_current.sh.",
            "\n".join(f"- {b}" for b in in_process_batches),
        )

    if len(in_process_files) > 1:
        fail(
            2,
            "AMBIGUOUS_TASK_STATE: multiple tasks are already in process.",
            "\n".join(f"- {f}" for f in in_process_files),
        )

    if len(in_process_files) == 1:
        print_task(in_process_files[0])
        return

    new_files = handoff_files(new_dir)
    if not new_files:
        print("NO_TASK")
        return

    source = new_files[0]
    target = in_process_dir / source.name
    if target.exists():
        fail(
            2,
            f"AMBIGUOUS_TASK_STATE: target in-process file already exists: {target}",
        )

    source.rename(target)
    set_header(target, "dequeued_at", now_iso())
    print_task(target)


def run_batch_mode():
    new_dir, in_process_dir = ensure_inbox_dirs()
    in_process_batches = batch_dirs(in_process_dir)
    in_process_files = handoff_files(in_process_dir)

    if in_process_files:
        fail(
            2,
            "TASK_IN_PROCESS_IS_SINGLE: use ready_for_next.sh or done_with_current.sh.",
            "\n".join(f"- {f}" for f in in_process_files),
        )

    if len(in_process_batches) > 1:
        fail(
            2,
            "AMBIGUOUS_TASK_STATE: multiple batches are already in process.",
            "\n".join(f"- {b}" for b in in_process_batches),
        )

    if len(in_process_batches) == 1:
        print_batch(in_process_batches[0])
        return

    new_files = handoff_files(new_dir)
    if not new_files:
        print("NO_TASK")
        return

    batch_priority = header_value(new_files[0], "priority", "50")
    batch_dir = new_batch_dir(in_process_dir)
    selected = [
        f for f in new_files if header_value(f, "priority", "50") == batch_priority
    ]

    batch_dir.mkdir(parents=True, exist_ok=False)
    for src in selected:
        tgt = batch_dir / src.name
        if tgt.exists():
            fail(
                2,
                f"AMBIGUOUS_TASK_STATE: target batch file already exists: {tgt}",
            )
        src.rename(tgt)
        set_header(tgt, "dequeued_at", now_iso())

    if not selected:
        fail(
            2,
            f"AMBIGUOUS_TASK_STATE: no tasks selected for batch priority {batch_priority}.",
        )

    print_batch(batch_dir)


def main():
    role = os.environ.get("SWARMFORGE_ROLE")
    if not role:
        fail(1, "Set SWARMFORGE_ROLE.")

    if not role_known(role):
        fail(1, f"Unknown role: {role}")

    mode = role_receive_mode(role)
    if mode == "task":
        run_task_mode()
    elif mode == "batch":
        run_batch_mode()
    else:
        fail(2, f"INVALID_RECEIVE_MODE: {mode} for role {role}")


if __name__ == "__main__":
    main()
