#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os
import subprocess

from swarm_python.handoff.format import header_field, set_header
from swarm_python.handoff.inbox import batch_dirs, handoff_files
from swarm_python.handoff.project import inbox_dir, state_dir
from swarm_python.handoff.roles import role_known, role_receive_mode
from swarm_python.handoff.timefmt import now_iso
from swarm_python.handoff.validate import REPLY_REQUIRED_ROLES, ROLE_ROUTES

READY_FOR_NEXT = Path(__file__).resolve().parent / "ready_for_next.py"

NO_REPLY_FLAG = "--no-reply"

USAGE = f"""\
Usage: done_with_current.sh [{NO_REPLY_FLAG}]

Marks the handoff you are working on as completed and picks up the next one.

For the coder, reviewer and architect, the handoff must have been answered
first: send your own git_handoff with swarm_handoff.sh, then run this script.
It refuses to complete the work otherwise, because nothing would move the
feature forward.

  {NO_REPLY_FLAG}   complete the current handoff without having sent a reply.
                    Only for a step that legitimately ends without one.
"""


def fail(status, *lines):
    for line in lines:
        print(line, file=sys.stderr)
    sys.exit(status)


def _sent_handoffs():
    """git_handoffs this role has queued (outbox) or already had delivered (sent)."""
    out = []
    for sub in ("outbox", "sent"):
        out += handoff_files(state_dir() / sub)
    return out


def _unanswered_tasks(role, current_files):
    """Tasks among `current_files` (handoffs being completed) for which `role`
    must reply but has sent nothing since it picked the handoff up. Matches by
    `task` and by `created_at >= dequeued_at`, so a reply sent in an earlier
    review round does not count."""
    if role not in REPLY_REQUIRED_ROLES or not ROLE_ROUTES.get(role):
        return []
    sent = [
        (header_field(p, "task"), header_field(p, "created_at") or "")
        for p in _sent_handoffs()
        if header_field(p, "type") == "git_handoff"
    ]
    missing = []
    for path in current_files:
        task = header_field(path, "task")
        if header_field(path, "type") != "git_handoff" or not task:
            continue  # notes and task-less mail expect no reply
        since = header_field(path, "dequeued_at") or ""
        if not any(t == task and created >= since for t, created in sent):
            if task not in missing:
                missing.append(task)
    return missing


def require_reply(role, current_files, no_reply):
    if no_reply:
        return
    missing = _unanswered_tasks(role, current_files)
    if not missing:
        return
    targets = " or ".join(sorted(ROLE_ROUTES[role]))
    fail(
        2,
        "REPLY_REQUIRED: you have not sent a handoff for "
        + ", ".join(f"'{t}'" for t in missing)
        + " since you picked it up. Nothing was completed.",
        f"Send your git_handoff (to {targets}) with swarm_handoff.sh first, "
        "then run done_with_current.sh again.",
        f"If this step legitimately ends without a reply, run: done_with_current.sh {NO_REPLY_FLAG}",
    )


def ensure_inbox_dirs():
    inbox = inbox_dir()
    in_process_dir = inbox / "in_process"
    completed_dir = inbox / "completed"
    for d in (in_process_dir, completed_dir):
        d.mkdir(parents=True, exist_ok=True)
    return in_process_dir, completed_dir


def run_ready():
    subprocess.run([sys.executable, str(READY_FOR_NEXT)])


def run_task_mode(role, no_reply=False):
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
    require_reply(role, [source], no_reply)
    set_header(source, "completed_at", now_iso())
    source.rename(target)
    print(f"COMPLETED: {target}")
    run_ready()


def run_batch_mode(role, no_reply=False):
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

    require_reply(role, batch_files, no_reply)
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


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if any(a in ("-h", "--help") for a in args):
        print(USAGE)
        return
    unknown = [a for a in args if a != NO_REPLY_FLAG]
    if unknown:
        fail(1, f"Unknown argument: {unknown[0]}", USAGE)
    no_reply = NO_REPLY_FLAG in args

    role = os.environ.get("SWARMFORGE_ROLE")
    if not role:
        fail(1, "Set SWARMFORGE_ROLE.")

    if not role_known(role):
        fail(1, f"Unknown role: {role}")

    mode = role_receive_mode(role)
    if mode == "task":
        run_task_mode(role, no_reply)
    elif mode == "batch":
        run_batch_mode(role, no_reply)
    else:
        fail(2, f"INVALID_RECEIVE_MODE: {mode} for role {role}")


if __name__ == "__main__":
    main()
