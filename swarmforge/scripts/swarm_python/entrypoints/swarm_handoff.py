#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os

from swarm_python.handoff.format import parse_draft
from swarm_python.handoff.project import roles_file, state_dir
from swarm_python.handoff.roles import role_known
from swarm_python.handoff.sequence import next_sequence
from swarm_python.handoff.timefmt import now_iso, now_id_ts
from swarm_python.handoff.validate import error_report, validate

USAGE = """\
Usage: swarm_handoff.sh <draft-file>

Draft formats:

type: git_handoff
to: <role>[,<role>...]
priority: NN
task: <short-stable-task-name>
commit: <10-char-commit-abbrev>

type: note
to: <role>[,<role>...]
priority: NN
message: <one line, max 80 chars>
"""


def usage():
    print(USAGE, file=sys.stderr)
    sys.exit(1)


def exit_with(status, message):
    if message:
        print(message, file=sys.stderr)
    sys.exit(status)


def sender_role():
    role = os.environ.get("SWARMFORGE_ROLE")
    if not role:
        exit_with(1, "Set SWARMFORGE_ROLE.")
    return role


def body_text(type_, sender, canonical_commit, note_message):
    if type_ == "git_handoff":
        return f"Re-read your role and constitution.\n\nmerge_and_process {sender} {canonical_commit}"
    if type_ == "note":
        return f"Re-read your role and constitution.\n\n{note_message}"
    return ""


def write_handoff(headers, recipients, canonical_commit, sender):
    timestamp_id = now_id_ts()
    created_at = now_iso()
    sequence = next_sequence()
    handoff_id = f"{timestamp_id}_{sequence}_from_{sender}"
    recipient_slug = "_".join(recipients)
    priority = headers.get("priority", "")
    type_ = headers.get("type", "")
    filename = f"{priority}_{timestamp_id}_{sequence}_from_{sender}_to_{recipient_slug}.handoff"
    outbox_dir = state_dir() / "outbox"
    tmp_dir = outbox_dir / "tmp"
    tmp_file = tmp_dir / f"{filename}.tmp"
    outbox_file = outbox_dir / filename

    lines = [
        f"id: {handoff_id}",
        f"from: {sender}",
        f"to: {','.join(recipients)}",
        f"priority: {priority}",
        f"type: {type_}",
    ]
    if type_ == "git_handoff":
        lines.append(f"role: {sender}")
        lines.append(f"task: {headers.get('task', '')}")
        lines.append(f"commit: {canonical_commit}")
    elif type_ == "note":
        lines.append(f"message: {headers.get('message', '')}")

    lines.append(f"created_at: {created_at}")
    lines.append("")
    lines.append(body_text(type_, sender, canonical_commit, headers.get("message", "")))

    for d in (tmp_dir, outbox_dir, state_dir() / "sent", state_dir() / "failed"):
        d.mkdir(parents=True, exist_ok=True)

    tmp_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp_file.rename(outbox_file)
    return outbox_file


def main():
    args = sys.argv[1:]
    if len(args) != 1:
        usage()

    draft = Path(args[0])
    if not draft.is_file():
        exit_with(1, f"Draft file not found: {draft}")

    sender = sender_role()
    if not role_known(sender):
        exit_with(1, f"Unknown sender role: {sender}")

    parsed = parse_draft(draft)
    validation = validate(parsed["headers"], parsed["ordered"], sender)
    all_errors = list(parsed["errors"]) + list(validation["errors"])

    if all_errors:
        error_report(draft, all_errors)
        sys.exit(2)

    outbox_file = write_handoff(
        parsed["headers"],
        validation["recipients"],
        validation["canonical-commit"],
        sender,
    )
    draft.unlink()
    print(f"HANDOFF QUEUED: {outbox_file}")


if __name__ == "__main__":
    main()
