"""How each backend starts a named session and resumes it later.

A session belongs to one role working on one feature. The scheduler resumes
it while the feature bounces between roles (coder <-> reviewer loops) and
starts a new one when a different feature arrives.

Backends whose resume path is known pick the session identity up front, so
nothing has to be scraped from their output afterwards:
  claude   --session-id <uuid> to create, --resume <uuid> to continue
  pi       --session-id <id> creates it if missing and continues it after
  hermes   owns the session identity: a fresh launch creates it with the
           run's route and a resume continues the worktree's own session
           (`--in` scopes hermes' lookup to that worktree)
  kiro-cli owns the session identity too (it rejects a caller-chosen id).
           The scheduler records the id kiro created for a fresh launch
           (find_kiro_session, by worktree and launch time) in the role state;
           only that stored id is ever resumed, with `chat --resume-id`,
           re-applying the configured extra args (kiro does not persist model
           or trust in the session). With no stored id the launch is fresh.

Every other backend is launched fresh each time, as before.
"""

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

RESUMABLE_AGENTS = {"claude", "pi", "hermes", "kiro-cli"}

RESUME_PROMPT = (
    "You have new handoff mail. Run ready_for_next and follow its output."
)


@dataclass(frozen=True)
class SessionPlan:
    session_id: str
    resume: bool


def supports_resume(agent: str) -> bool:
    return agent in RESUMABLE_AGENTS


def new_session_id(agent: str, role: str, task: str) -> str:
    if agent == "kiro-cli":
        return ""  # kiro names its own session; the scheduler records it later
    if agent == "hermes":
        # Hermes picks the session itself (the worktree's latest), so this id
        # is only a marker in the swarm state: it tells a fresh launch from a
        # resume of the same feature. Keep it readable for the state file.
        slug = task or "start"
        return f"swarmforge-{role}-{slug}-{uuid.uuid4().hex[:8]}"
    return str(uuid.uuid4())


def plan_session(agent: str, role: str, task: str, role_state: dict) -> SessionPlan | None:
    """Decide whether the next launch of a role resumes its session for this
    feature or starts a new one. Returns None for backends without resume
    support, which always start fresh."""
    if not supports_resume(agent):
        return None
    # For kiro-cli session_id is only ever a real, recorded kiro id (or ""),
    # so the same check below never resumes anything it did not create.
    if role_state.get("session_id") and role_state.get("task") == task:
        return SessionPlan(role_state["session_id"], resume=True)
    return SessionPlan(new_session_id(agent, role, task), resume=False)


KIRO_LIST_TIMEOUT_S = 5
# kiro stamps a session a moment before the scheduler's launch clock is read
# on some hosts; tolerate that much skew when matching against launch time.
KIRO_CLOCK_SKEW_S = 2


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def find_kiro_session(cwd: Path, since_epoch: float, binary: str | None = None) -> tuple[str | None, str]:
    """The kiro-cli session a fresh launch created in `cwd`: the newest one
    saved there that was updated at or after `since_epoch` (the launch time).
    Returns (session_id, "") or (None, reason). Timestamps are compared as
    datetimes, never as strings; a session older than the launch belongs to an
    earlier run (maybe another feature) and is never returned."""
    binary = binary or shutil.which("kiro-cli") or "kiro-cli"
    try:
        out = subprocess.run(
            [binary, "chat", "--list-sessions", "--format", "json"],
            cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=KIRO_LIST_TIMEOUT_S, check=True,
        ).stdout
        envelopes = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return None, f"lookup failed: {type(e).__name__}: {e}"
    floor = datetime.fromtimestamp(since_epoch, tz=timezone.utc) - timedelta(seconds=KIRO_CLOCK_SKEW_S)
    candidates = []
    for env in envelopes if isinstance(envelopes, list) else []:
        for s in env.get("sessions", []) if isinstance(env, dict) else []:
            if not isinstance(s, dict) or not s.get("sessionId"):
                continue
            stamp = _parse_time(s.get("updatedAt"))
            if stamp is not None and stamp >= floor:
                candidates.append((stamp, s["sessionId"]))
    if not candidates:
        return None, "no session saved at or after the launch time"
    return max(candidates)[1], ""
