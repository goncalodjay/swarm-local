"""How each backend starts a named session and resumes it later.

A session belongs to one role working on one feature. The scheduler resumes
it while the feature bounces between roles (coder <-> reviewer loops) and
starts a new one when a different feature arrives.

Backends whose resume path is known pick the session identity up front, so
nothing has to be scraped from their output afterwards:
  claude   --session-id <uuid> to create, --resume <uuid> to continue
  pi       --session-id <id> creates it if missing and continues it after
  hermes   --continue <name> --create-if-missing does both

Every other backend is launched fresh each time, as before.
"""

import uuid
from dataclasses import dataclass

RESUMABLE_AGENTS = {"claude", "pi", "hermes"}

RESUME_PROMPT = (
    "You have new handoff mail. Run ready_for_next.sh and follow its output."
)


@dataclass(frozen=True)
class SessionPlan:
    session_id: str
    resume: bool


def supports_resume(agent: str) -> bool:
    return agent in RESUMABLE_AGENTS


def new_session_id(agent: str, role: str, task: str) -> str:
    if agent == "hermes":
        # hermes resolves --continue by session title, so make it readable
        # and unique per session.
        slug = task or "start"
        return f"swarmforge-{role}-{slug}-{uuid.uuid4().hex[:8]}"
    return str(uuid.uuid4())


def plan_session(agent: str, role: str, task: str, role_state: dict) -> SessionPlan | None:
    """Decide whether the next launch of a role resumes its session for this
    feature or starts a new one. Returns None for backends without resume
    support, which always start fresh."""
    if not supports_resume(agent):
        return None
    if role_state.get("session_id") and role_state.get("task") == task:
        return SessionPlan(role_state["session_id"], resume=True)
    return SessionPlan(new_session_id(agent, role, task), resume=False)
