#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os
import subprocess

from swarm_python.handoff.format import set_header
from swarm_python.handoff.inbox import batch_dirs, handoff_files
from swarm_python.handoff.project import inbox_dir
from swarm_python.handoff.roles import role_known, role_receive_mode
from swarm_python.handoff.timefmt import now_iso

READY_FOR_NEXT = Path(__file__).resolve().parent / "ready_for_next.py"


def fail(status, *lines):
    for line in lines:
        print(line, file=sys.stderr)
    sys.exit(status)


def ensure_inbox_dirs():
    inbox = inbox_dir()
    in_process_dir = inbox / "in_process"
    completed_dir = inbox / "completed"
    for d in (in_process_dir, completed_dir):
        d.mkdir(parents=True, exist_ok=True)
    return in_process_dir, completed_dir


def run_ready():
    subprocess.run([sys.executable, str(READY_FOR_NEXT)])


def run_task_mode():
    in_process_dir, completed_dir = ensure_inbox_dirs()
    in_process_batches = batch_dirs(in_process_dir)
    in_process_files = handoff_files(in_process_dir)

    if in_process_batches:
        fail(
            2,
            "CURRENT_WORK_IS_BATCH: use done_with_current.sh.",
            "\n".join(f"- {b}" for b in in_process_batches),
        )

    if not in_process_files:
        fail(1, "NO_CURRENT_TASK")

    if len(in_process_files) > 1:
        fail(
            2,
            "AMBIGUOUS_TASK_STATE: multiple tasks are in process.",
            "\n".join(f"- {f}" for f in in_process_files),
        )

    source = in_process_files[0]
    target = completed_dir / source.name
    if target.exists():
        fail(
            2,
            f"AMBIGUOUS_TASK_STATE: completed file already exists: {target}",
        )
    set_header(source, "completed_at", now_iso())
    source.rename(target)
    print(f"COMPLETED: {target}")
    run_ready()


def run_batch_mode():
    in_process_dir, completed_dir = ensure_inbox_dirs()
    in_process_batches = batch_dirs(in_process_dir)
    in_process_files = handoff_files(in_process_dir)

    if in_process_files:
        fail(
            2,
            "CURRENT_WORK_IS_SINGLE_TASK: use done_with_current.sh.",
            "\n".join(f"- {f}" for f in in_process_files),
        )

    if not in_process_batches:
        fail(1, "NO_CURRENT_BATCH")

    if len(in_process_batches) > 1:
        fail(
            2,
            "AMBIGUOUS_TASK_STATE: multiple batches are in process.",
            "\n".join(f"- {b}" for b in in_process_batches),
        )

    source_dir = in_process_batches[0]
    batch_files = handoff_files(source_dir)
    target_dir = completed_dir / source_dir.name

    if not batch_files:
        fail(
            2,
            f"AMBIGUOUS_TASK_STATE: batch contains no tasks: {source_dir}",
        )

    if target_dir.exists():
        fail(
            2,
            f"AMBIGUOUS_TASK_STATE: completed batch already exists: {target_dir}",
        )

    completed_at = now_iso()
    target_dir.mkdir(parents=True, exist_ok=False)
    for src in batch_files:
        set_header(src, "completed_at", completed_at)
        tgt = target_dir / src.name
        if tgt.exists():
            fail(
                2,
                f"AMBIGUOUS_TASK_STATE: completed batch file already exists: {tgt}",
            )
        src.rename(tgt)
        print(f"COMPLETED: {tgt}")

    source_dir.rmdir()
    print(f"COMPLETED_BATCH: {target_dir}")
    run_ready()


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
