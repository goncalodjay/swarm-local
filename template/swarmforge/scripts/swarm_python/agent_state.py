"""Per-swarm record of which role agents are running and which agent
session belongs to which feature.

Only the handoff daemon writes this file once the swarm is up; `./swarm`
writes the initial copy before starting the daemon. The TUI and humans may
read it.

Per role:
  status          "wanted"  start it on the next scheduler tick
                  "running" an agent process is live in the role's pane
                  "parked"  no agent process; the pane is an idle shell
                  "failed"  its agent kept exiting right after launch; left
                            alone until the swarm is restarted
  task            feature (handoff `task:` header) the session belongs to
  session_id      backend session identity for that task ("" = none yet);
                  a launch creates it, later launches for the same task
                  resume it
  launched_at     epoch seconds of the last launch
  sent_since_launch  the role forwarded a handoff since its last launch
  idle_since      epoch seconds herdr first reported it idle with no
                  pending work (None while busy)
  fast_exits      consecutive launches whose agent exited right away while
                  work was pending; the scheduler stops retrying at a limit
"""

import json
import os
from pathlib import Path

STATE_FILE_NAME = "agents.json"


def state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILE_NAME


def blank_role(status: str = "parked") -> dict:
    return {
        "status": status,
        "task": "",
        "session_id": "",
        "launched_at": None,
        "sent_since_launch": False,
        "idle_since": None,
        "fast_exits": 0,
    }


def initial_state(roles: list[str]) -> dict:
    """Fresh swarm: the first role (the specifier) is wanted, every other
    role starts parked and is launched on demand when work reaches it."""
    return {
        role: blank_role("wanted" if index == 0 else "parked")
        for index, role in enumerate(roles)
    }


def load(state_dir: Path, roles: list[str]) -> dict:
    path = state_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {role: {**blank_role(), **data.get(role, {})} for role in roles}


def save(state_dir: Path, state: dict) -> None:
    path = state_path(state_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
