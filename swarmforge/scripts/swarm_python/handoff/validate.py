import re
import subprocess
import sys
from pathlib import Path

from .format import ALLOWED_TYPES, RESERVED_FIELDS, ALLOWED_FIELDS
from .. import plan as planlib
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
verdict: pass|fail          (reviewer only, required: pass -> architect, fail -> coder)

type: note
to: <role>[,<role>...]
priority: NN
message: <one line, max 80 chars>

Optionally, after a blank line, add free-form text: defects, context,
review notes. It is appended to the generated payload verbatim.
"""

USAGE = USAGE_TEXT


# Cycle routing table. Each role may only hand off to its allowed recipients.
# specifier -> coder            (one approved feature of the plan, see plan.py)
# coder     -> reviewer         (every change, whoever sent the work)
# reviewer  -> coder            (verdict fail: rework)
#           -> architect        (verdict pass, or fail once the review round cap is hit)
# architect -> coder            (adjustments)
#           -> specifier        (feature complete)
ROLE_ROUTES = {
    "specifier": frozenset({"coder"}),
    "coder": frozenset({"reviewer"}),
    "reviewer": frozenset({"coder", "architect"}),
    "architect": frozenset({"coder", "specifier"}),
}

# Recipients a single git_handoff may never combine: each of these roles forwards
# to exactly one next role per git_handoff, so the feature status move is unambiguous.
EXCLUSIVE_FORWARDS = {
    "reviewer": frozenset({"coder", "architect"}),
    "architect": frozenset({"coder", "specifier"}),
}

KNOWN_CYCLE_ROLES = frozenset(ROLE_ROUTES)

# Roles that must send a git_handoff of their own for every git_handoff they
# take on: the work only moves forward when they reply (see done_with_current).
# The specifier is excluded: the architect's completion handoff ends the chain
# (the swarm publishes the PR and dispatches the next feature itself).
REPLY_REQUIRED_ROLES = frozenset({"coder", "reviewer", "architect"})


def validate_route(sender, recipients, type_=None):
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
    # Only a git_handoff moves a feature status, so only it must be unambiguous;
    # a note may go to several recipients (e.g. a reviewer note to coder and architect).
    exclusive = EXCLUSIVE_FORWARDS.get(sender, frozenset()) if type_ == "git_handoff" else frozenset()
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


def _commit_format_error(commit):
    if len(commit) != 10:
        return (
            f"Header 'commit' must be 10 characters (git rev-parse --short=10 HEAD), "
            f"got {len(commit)}: {commit}"
        )
    return (
        f"Header 'commit' must be 10 hexadecimal characters "
        f"(git rev-parse --short=10 HEAD), got: {commit}"
    )


def canonical_commit(commit):
    r = subprocess.run(
        ["git", "rev-parse", f"--disambiguate={commit}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
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
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    obj_type = r2.stdout.strip()
    if obj_type != "commit":
        return None, (
            f"Header 'commit' must resolve to a commit; "
            f"'{commit}' resolves to '{obj_type}'."
        )
    r3 = subprocess.run(
        ["git", "rev-parse", "--short=10", obj],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r3.stdout.strip(), None


_VALID_BY_TYPE = {
    ("git_handoff", "type"),
    ("git_handoff", "to"),
    ("git_handoff", "priority"),
    ("git_handoff", "task"),
    ("git_handoff", "commit"),
    ("git_handoff", "verdict"),
    ("note", "type"),
    ("note", "to"),
    ("note", "priority"),
    ("note", "message"),
}


def _verdict_errors(sender, type_, verdict, recipients, task, plan_dir, route_broken):
    """Reviewer verdict: required on its git_handoffs and it decides the route.
    pass -> architect only; fail -> coder only, except once the plan's review
    round cap is reached: then the failure is escalated to the architect only."""
    if sender != "reviewer" or type_ != "git_handoff":
        if verdict and sender is not None and type_ == "git_handoff":
            return ["Header 'verdict' is only allowed on a git_handoff sent by the reviewer."]
        return []
    if not verdict:
        return [
            "Missing required header 'verdict' for a reviewer git_handoff: use 'verdict: pass' "
            "(review passed, send to the architect) or 'verdict: fail' (rework, send to the coder)."
        ]
    if verdict not in ("pass", "fail"):
        return [f"Header 'verdict' must be pass or fail; got '{verdict}'."]
    if not recipients or route_broken:
        return []
    if verdict == "pass":
        expected, why = "architect", (
            "verdict 'pass' must be sent to the architect only (a passing review always goes "
            "to the architect, never to the coder or the specifier)"
        )
    elif planlib.review_cap_reached(plan_dir or planlib.locate_plan_dir(), task):
        expected, why = "architect", (
            "the review round cap for this feature is reached, so a failing review is escalated: "
            "send it to the architect only, not back to the coder"
        )
    else:
        expected, why = "coder", "verdict 'fail' must be sent to the coder only (rework)"
    if recipients != [expected]:
        return [f"Invalid reviewer route: {why}; got '{','.join(recipients)}'."]
    return []


def validate(headers, ordered, sender=None, plan_dir=None):
    type_ = headers.get("type", "")
    to = headers.get("to", "")
    priority = headers.get("priority", "")
    commit = headers.get("commit", "")
    task_name = headers.get("task", "")
    note_message = headers.get("message", "")
    verdict = headers.get("verdict", "")

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
            commit_error = _commit_format_error(commit)
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

    route_errors = validate_route(sender, recipients, type_) if sender else []

    verdict_errors = _verdict_errors(
        sender, type_, verdict, recipients, task_name, plan_dir, bool(route_errors)
    )

    # Specifier -> coder work must be exactly one approved, ready feature.
    plan_errors = []
    if sender == "specifier" and type_ == "git_handoff" and "coder" in recipients:
        plan_errors = planlib.check_specifier_to_coder(
            plan_dir or planlib.locate_plan_dir(), task_name
        )

    # A note from the specifier to the coder must not start work outside the plan.
    if sender == "specifier" and type_ == "note" and "coder" in recipients:
        plan_errors = planlib.note_to_coder_errors(plan_dir or planlib.locate_plan_dir())

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
            + verdict_errors
            + plan_errors
        ),
    }


def error_report(draft_path, errors):
    print(f"HANDOFF INVALID: {draft_path}", file=sys.stderr)
    if errors:
        # The usage block below is long; lead with the actual reason.
        print(f"Reason: {errors[0]}", file=sys.stderr)
    print(file=sys.stderr)
    print("Errors:", file=sys.stderr)
    for e in errors:
        print(f"- {e}", file=sys.stderr)
    print(file=sys.stderr)
    print(USAGE, file=sys.stderr)
