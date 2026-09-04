import re
import subprocess
import sys
from pathlib import Path

from .format import ALLOWED_TYPES, RESERVED_FIELDS, ALLOWED_FIELDS
from .roles import role_known
from .timefmt import now_iso

USAGE_TEXT = """\
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

USAGE = USAGE_TEXT


# Cycle routing table. Each role may only hand off to its allowed recipients.
# specifier -> coder            (whole phased spec)
# coder     -> reviewer         (every change, whoever sent the work)
# reviewer  -> coder            (rework)
#           -> architect | specifier (exactly one, when the review passes)
# architect -> coder            (adjustments)
#           -> specifier        (feature complete)
ROLE_ROUTES = {
    "specifier": frozenset({"coder"}),
    "coder": frozenset({"reviewer"}),
    "reviewer": frozenset({"coder", "architect", "specifier"}),
    "architect": frozenset({"coder", "specifier"}),
}

# Recipients a single reviewer handoff may never combine: a passing review goes
# forward to exactly one of them.
EXCLUSIVE_FORWARDS = {"reviewer": frozenset({"architect", "specifier"})}

KNOWN_CYCLE_ROLES = frozenset(ROLE_ROUTES)


def validate_route(sender, recipients):
    """Reject handoffs that leave the cycle defined by ROLE_ROUTES.

    Roles outside the standard cycle are not constrained, so projects that add
    their own roles keep working.
    """
    if sender not in ROLE_ROUTES:
        return []
    allowed = ROLE_ROUTES[sender]
    errors = []
    for r in recipients:
        if not r or r not in KNOWN_CYCLE_ROLES:
            continue
        if r == sender:
            errors.append(f"Role '{sender}' must not hand off to itself.")
        elif r not in allowed:
            errors.append(
                f"Role '{sender}' may not hand off to '{r}'; "
                f"allowed recipients are {', '.join(sorted(allowed))}."
            )
    exclusive = EXCLUSIVE_FORWARDS.get(sender, frozenset())
    chosen = exclusive.intersection(recipients)
    if len(chosen) > 1:
        errors.append(
            f"Role '{sender}' must forward to exactly one of "
            f"{', '.join(sorted(exclusive))}; got {', '.join(sorted(chosen))}."
        )
    return errors


def validate_recipients(to):
    if not to:
        return [], []
    recipients = to.split(",")
    seen = set()
    errors = []
    for r in recipients:
        if not r:
            errors.append("Header 'to' contains an empty recipient.")
        elif "_" in r:
            errors.append(
                f"Recipient role '{r}' is invalid; role names may not contain underscores."
            )
        elif r in seen:
            errors.append(f"Duplicate recipient '{r}'.")
        elif not role_known(r):
            errors.append(f"Unknown recipient role '{r}'.")
        seen.add(r)
    return recipients, errors


def canonical_commit(commit):
    r = subprocess.run(
        ["git", "rev-parse", f"--disambiguate={commit}"],
        capture_output=True, text=True,
    )
    matches = [line for line in r.stdout.splitlines() if line]
    if len(matches) != 1:
        return None, (
            f"Header 'commit' must resolve to exactly one Git object; "
            f"'{commit}' matched {len(matches)}."
        )
    obj = matches[0]
    r2 = subprocess.run(
        ["git", "cat-file", "-t", obj],
        capture_output=True, text=True,
    )
    obj_type = r2.stdout.strip()
    if obj_type != "commit":
        return None, (
            f"Header 'commit' must resolve to a commit; "
            f"'{commit}' resolves to '{obj_type}'."
        )
    r3 = subprocess.run(
        ["git", "rev-parse", "--short=10", obj],
        capture_output=True, text=True,
    )
    return r3.stdout.strip(), None


_VALID_BY_TYPE = {
    ("git_handoff", "type"),
    ("git_handoff", "to"),
    ("git_handoff", "priority"),
    ("git_handoff", "task"),
    ("git_handoff", "commit"),
    ("note", "type"),
    ("note", "to"),
    ("note", "priority"),
    ("note", "message"),
}


def validate(headers, ordered, sender=None):
    type_ = headers.get("type", "")
    to = headers.get("to", "")
    priority = headers.get("priority", "")
    commit = headers.get("commit", "")
    task_name = headers.get("task", "")
    note_message = headers.get("message", "")

    recipients, recipient_errors = validate_recipients(to)

    field_errors = []
    if type_:
        for field in ordered:
            if (type_, field) not in _VALID_BY_TYPE:
                field_errors.append(
                    f"Header '{field}' is not allowed for type '{type_}'."
                )

    base_errors = []
    if not type_:
        base_errors.append("Missing required header 'type'.")
    if not to:
        base_errors.append("Missing required header 'to'.")
    if not priority:
        base_errors.append("Missing required header 'priority'.")
    if type_ and type_ not in ALLOWED_TYPES:
        base_errors.append(
            f"Header 'type' must be one of git_handoff or note; got '{type_}'."
        )
    if priority and not re.fullmatch(r"[0-9][0-9]", priority):
        base_errors.append(
            f"Header 'priority' must be two digits from 00 to 99; got '{priority}'."
        )

    canonical = None
    commit_error = None
    if type_ == "git_handoff":
        if not commit:
            commit_error = "Missing required header 'commit' for git_handoff."
        elif not re.fullmatch(r"[0-9a-fA-F]{10}", commit):
            commit_error = (
                f"Header 'commit' must be exactly 10 hexadecimal characters; "
                f"got '{commit}'."
            )
        else:
            canonical, commit_error = canonical_commit(commit)

    git_errors = []
    if type_ == "git_handoff":
        if not task_name:
            git_errors.append("Missing required header 'task' for git_handoff.")
        elif len(task_name) > 80:
            git_errors.append(
                f"Header 'task' must be no longer than 80 characters; "
                f"got {len(task_name)}."
            )
    if type_ != "git_handoff" and commit:
        git_errors.append("Header 'commit' is only allowed for git_handoff.")
    if type_ != "git_handoff" and task_name:
        git_errors.append("Header 'task' is only allowed for git_handoff.")
    if commit_error:
        git_errors.append(commit_error)

    note_errors = []
    if type_ == "note":
        if not note_message:
            note_errors.append("Missing required header 'message' for note.")
        elif len(note_message) > 80:
            note_errors.append(
                f"Header 'message' must be no longer than 80 characters; "
                f"got {len(note_message)}."
            )
    if type_ != "note" and note_message:
        note_errors.append("Header 'message' is only allowed for note.")

    route_errors = validate_route(sender, recipients) if sender else []

    return {
        "recipients": recipients,
        "canonical-commit": canonical,
        "errors": (
            base_errors
            + recipient_errors
            + field_errors
            + git_errors
            + note_errors
            + route_errors
        ),
    }


def error_report(draft_path, errors):
    print(f"HANDOFF INVALID: {draft_path}", file=sys.stderr)
    print(file=sys.stderr)
    print("Errors:", file=sys.stderr)
    for e in errors:
        print(f"- {e}", file=sys.stderr)
    print(file=sys.stderr)
    print(USAGE, file=sys.stderr)
