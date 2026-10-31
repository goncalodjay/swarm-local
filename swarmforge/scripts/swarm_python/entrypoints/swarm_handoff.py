#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os
import subprocess

from swarm_python import plan as planlib
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
verdict: pass|fail          (reviewer only, required)

type: note
to: <role>[,<role>...]
priority: NN
message: <one line, max 80 chars>

Optionally, after a blank line, add free-form text: defects, context,
review notes. It is appended to the generated payload verbatim.
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


def body_text(type_, sender, canonical_commit, note_message, extra_body):
    if type_ == "git_handoff":
        text = f"Re-read your role and constitution.\n\nmerge_and_process {sender} {canonical_commit}"
    elif type_ == "note":
        text = f"Re-read your role and constitution.\n\n{note_message}"
    else:
        return ""
    if extra_body:
        text += f"\n\n{extra_body}"
    return text


def write_handoff(headers, recipients, canonical_commit, sender, extra_body=""):
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
        if headers.get("verdict"):
            lines.append(f"verdict: {headers['verdict']}")
    elif type_ == "note":
        lines.append(f"message: {headers.get('message', '')}")

    lines.append(f"created_at: {created_at}")
    lines.append("")
    lines.append(body_text(type_, sender, canonical_commit, headers.get("message", ""), extra_body))

    for d in (tmp_dir, outbox_dir, state_dir() / "sent", state_dir() / "failed"):
        d.mkdir(parents=True, exist_ok=True)

    tmp_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp_file.rename(outbox_file)
    return outbox_file


def _is_feature_handoff(sender, headers, recipients):
    return (sender == "specifier" and headers.get("type") == "git_handoff"
            and "coder" in recipients)


def _loud_transition_failure(task, why, fix):
    bar = "!" * 72
    print(bar, file=sys.stderr)
    print(
        f"WARNING: the handoff of feature '{task}' is being queued, but the plan could "
        f"NOT be updated ({why}).",
        file=sys.stderr,
    )
    print(
        f"Specifier / human: run `{fix}` NOW, otherwise the plan status will be wrong.",
        file=sys.stderr,
    )
    print(bar, file=sys.stderr)


def _head_commit():
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def mark_feature_coding(sender, headers, recipients, fallback_commit=None):
    """Reserve the handed-off feature: re-check the gate and move it to
    `coding` under the plan lock, recording the base commit. Returns
    (snapshot, errors); `snapshot` is None when nothing was reserved.

    Ordering (validate -> mark coding -> write outbox -> remove draft) is the
    safest of the options: if the process dies after marking but before the
    outbox write, the worst case is a feature stuck in `coding` with no
    handoff (visible, fixed with `swarm plan set-status <id> pending`, and we
    roll back on ordinary failures). The opposite order could leave a queued
    handoff with a `pending` feature, which lets the specifier hand the same
    feature to the coder twice. Marking here (not in handoffd.deliver) also
    closes the outbox -> daemon-tick window. If the transition itself cannot
    be written (e.g. Windows file lock), the handoff is still queued but the
    failure is reported loudly.
    """
    if not _is_feature_handoff(sender, headers, recipients):
        return None, []
    task = headers.get("task", "")
    base = _head_commit() or fallback_commit
    try:
        errors, snapshot = planlib.reserve_feature(planlib.locate_plan_dir(), task, base)
    except (planlib.PlanError, OSError) as e:
        _loud_transition_failure(task, e, f"swarm plan set-status {task} coding")
        return None, []
    return snapshot, errors


def _coder_metrics(plan_dir, task, commit):
    """Advisory size/scope numbers for coder -> reviewer, or None (stderr note)."""
    try:
        feature = planlib.feature_by_id(planlib.load(plan_dir), task)
    except planlib.PlanError:
        return None
    if feature is None:
        return None
    base = feature.get("base_commit")
    if not base or not commit:
        print("Plan check skipped: no base commit recorded for this feature.", file=sys.stderr)
        return None
    metrics = planlib.compute_metrics(base, commit, feature.get("allowed_paths") or [])
    if metrics is None:
        print("Plan check skipped: git could not compute the diff.", file=sys.stderr)
    return metrics


ESCALATION_TEXT = (
    "ESCALATION: the review round cap was reached and the reviewer still reports defects "
    "(verdict fail). The feature is NOT going back to the coder. Either fix the remaining "
    "defects yourself with minimal, contained changes, or accept them and list them in your "
    "report to the specifier."
)


def apply_feature_transition(sender, headers, recipients, canonical_commit, extra_body):
    """Status move for coder/reviewer/architect handoffs of a plan feature.
    Returns (transition, extra_body); transition is None for legacy tasks.
    Same ordering and rollback as the reservation: transition first, outbox
    second, snapshot restored if the outbox write fails."""
    task = headers.get("task", "")
    if headers.get("type") != "git_handoff" or not task:
        return None, extra_body
    plan_dir = planlib.locate_plan_dir()
    metrics = None
    if sender == "coder" and recipients == ["reviewer"] and planlib.exists(plan_dir):
        try:
            metrics = _coder_metrics(plan_dir, task, canonical_commit)
        except Exception as e:  # advisory only: never block the handoff
            print(f"Plan check skipped: {e}", file=sys.stderr)
            metrics = None
    try:
        transition = planlib.apply_handoff_transition(
            plan_dir, sender, recipients, task, headers.get("verdict"), metrics,
        )
    except (planlib.PlanError, OSError) as e:
        move = planlib.planned_transition(sender, recipients, headers.get("verdict"))
        target = move[1] if move else "<status>"
        _loud_transition_failure(task, e, f"swarm plan set-status {task} {target} --force")
        return None, extra_body
    if transition is None:
        return None, extra_body
    if transition.skipped:
        print(f"Plan status note: {transition.skipped}.", file=sys.stderr)
    elif metrics is not None:
        check = planlib.plan_check_text(metrics)
        print(check, file=sys.stderr)
        extra_body = f"{extra_body}\n\n{check}" if extra_body else check
    if transition.escalated:
        # The architect prompt says the body STARTS with ESCALATION: lead with it.
        extra_body = f"{ESCALATION_TEXT}\n\n{extra_body}" if extra_body else ESCALATION_TEXT
    return transition, extra_body


def rollback_feature(headers, snapshot):
    """Restore a feature to its pre-transition state after the outbox write failed."""
    task = headers.get("task", "")
    try:
        planlib.restore_feature(planlib.locate_plan_dir(), task, snapshot)
    except (planlib.PlanError, OSError) as e:
        print(
            f"WARNING: the handoff failed and feature '{task}' could not be rolled back "
            f"({e}). Check it with `swarm plan status` and fix it with "
            f"`swarm plan set-status {task} <status> --force`.",
            file=sys.stderr,
        )


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

    if _is_feature_handoff(sender, parsed["headers"], validation["recipients"]):
        stale_errors, stale_warnings = planlib.base_freshness(
            planlib.locate_plan_dir(), validation["canonical-commit"],
        )
        for warning in stale_warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        if stale_errors:
            error_report(draft, stale_errors)
            sys.exit(2)

    snapshot, gate_errors = mark_feature_coding(
        sender, parsed["headers"], validation["recipients"], validation["canonical-commit"],
    )
    if gate_errors:
        error_report(draft, gate_errors)
        sys.exit(2)
    body = parsed["body"]
    if snapshot is None:
        transition, body = apply_feature_transition(
            sender, parsed["headers"], validation["recipients"],
            validation["canonical-commit"], body,
        )
        snapshot = transition.snapshot if transition else None
    try:
        outbox_file = write_handoff(
            parsed["headers"],
            validation["recipients"],
            validation["canonical-commit"],
            sender,
            body,
        )
    except BaseException:
        if snapshot is not None:
            rollback_feature(parsed["headers"], snapshot)
        raise
    draft.unlink()
    print(f"HANDOFF QUEUED: {outbox_file}")


if __name__ == "__main__":
    main()
