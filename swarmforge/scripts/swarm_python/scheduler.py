"""Keeps as few role agents running as the work needs.

The swarm is a cycle (specifier -> coder -> reviewer -> architect/specifier),
so at any moment usually only one role has work. Instead of keeping every
role's agent alive and idle, the handoff daemon calls `tick` every poll:

- A parked role with pending inbox work is launched (resuming its session
  when the work belongs to the same feature, see sessions.py).
- A running role that herdr reports idle, with no pending work, for
  PARK_GRACE seconds is parked: its agent process group is stopped and the
  pane drops back to an idle shell. The first role (the specifier) talks to
  the human, so it is only parked after it has forwarded work since it was
  launched; the others are parked as soon as they are idle and empty.
- A running role whose agent exited on its own is marked parked; if it
  still has work it is relaunched, with a fresh session when it died right
  after launch (a resume that failed), up to MAX_FAST_EXITS times.

- A running agent that sits idle with unread mail (inbox/new) and no task
  in progress is sent the wake-up again every NUDGE_AFTER seconds: agents
  ignore wake-ups while busy, so one can be lost. An idle agent WITH a
  task in progress is left alone; it is usually waiting for the human.

Roles overlap briefly by design: the recipient starts as soon as mail
arrives while the sender is still finishing up.
"""

import os
import signal
import time
from pathlib import Path

from .env import env_long
from .handoff.format import header_field
from .handoff.inbox import batch_dirs, handoff_files
from .sessions import plan_session

PARK_GRACE_S = env_long("SWARMFORGE_PARK_GRACE_MS", 15000) / 1000
# Right after a launch the pane still shows its shell until the wrapper
# execs the agent; do not read that as "the agent exited".
START_GRACE_S = 20
FAST_EXIT_S = 60
MAX_FAST_EXITS = 3
STOP_TIMEOUT_S = 10
NUDGE_AFTER_S = env_long("SWARMFORGE_NUDGE_AFTER_MS", 60000) / 1000
IDLE_STATUSES = {"idle", "done"}
WAKE_MESSAGE = "You have new handoff mail. If idle, run ready_for_next.sh."


def _inbox(worktree: Path) -> Path:
    return worktree / ".swarmforge" / "handoffs" / "inbox"


def pending_work(worktree: Path) -> bool:
    inbox = _inbox(worktree)
    return bool(
        handoff_files(inbox / "new")
        or handoff_files(inbox / "in_process")
        or batch_dirs(inbox / "in_process")
    )


def unread_mail_only(worktree: Path) -> bool:
    """Mail waiting in inbox/new while nothing is in progress."""
    inbox = _inbox(worktree)
    return bool(handoff_files(inbox / "new")) and not (
        handoff_files(inbox / "in_process") or batch_dirs(inbox / "in_process")
    )


def next_task(worktree: Path, fallback: str) -> str:
    """Feature name of the work the role will pick up next, in the order
    ready_for_next.sh hands it out. Notes carry no task: keep the current."""
    inbox = _inbox(worktree)
    candidates = handoff_files(inbox / "in_process")
    for batch in batch_dirs(inbox / "in_process"):
        candidates += handoff_files(batch)
    candidates += handoff_files(inbox / "new")
    for path in candidates:
        task = header_field(path, "task")
        if task:
            return task
    return fallback


class Scheduler:
    def __init__(self, rows, herdr, launcher, log, clock=time.time):
        """rows: RoleRow list in config order (index 0 is the specifier).
        herdr: object with pane_statuses() -> {pane_id: status},
               foreground(pane_id) -> (pgid, shell_pid),
               stop(pane_id) -> None and wake(pane_id) -> None.
        launcher: callable(index, row, SessionPlan | None) starting the
                  role's agent in its pane."""
        self.rows = rows
        self.herdr = herdr
        self.launcher = launcher
        self.log = log
        self.clock = clock

    def on_sent(self, state: dict, sender: str, task: str) -> None:
        """A role forwarded a handoff. Its session now belongs to that task,
        so a later completion of the same feature resumes it."""
        st = state.get(sender)
        if st is None:
            return
        st["sent_since_launch"] = True
        if task:
            st["task"] = task

    def is_running(self, state: dict, role: str) -> bool:
        return state.get(role, {}).get("status") == "running"

    def tick(self, state: dict) -> None:
        now = self.clock()
        statuses = self.herdr.pane_statuses()
        for index, row in enumerate(self.rows):
            st = state[row.role]
            pending = pending_work(row.worktree_path)
            if st["status"] == "running":
                if self._check_running(index, row, st, pending, statuses, now):
                    continue
            if st["status"] == "wanted" or (st["status"] == "parked" and pending):
                self._launch(index, row, st, now)

    def _check_running(self, index, row, st, pending, statuses, now) -> bool:
        """Returns True while the role stays running."""
        pgid, shell_pid = self.herdr.foreground(row.pane_id)
        if pgid is None:
            return True  # herdr did not answer; decide next tick
        since_launch = now - (st["launched_at"] or 0)
        if pgid == shell_pid:
            if since_launch < START_GRACE_S:
                return True
            if pending and since_launch < FAST_EXIT_S:
                st["fast_exits"] += 1
                st["session_id"] = ""  # the resume failed: start fresh next
            self.log("agent-exited", row.role, f"pending={pending}")
            self._mark_parked(st)
            return False

        if since_launch >= FAST_EXIT_S:
            st["fast_exits"] = 0
        idle = statuses.get(row.pane_id) in IDLE_STATUSES
        self._nudge_if_mail_is_waiting(row, st, idle, now)
        parkable = (
            not pending
            and idle
            and (index > 0 or st["sent_since_launch"])
        )
        if not parkable:
            st["idle_since"] = None
            return True
        if st["idle_since"] is None:
            st["idle_since"] = now
            return True
        if now - st["idle_since"] < PARK_GRACE_S:
            return True
        self.log("parking", row.role)
        self.herdr.stop(row.pane_id)
        self._mark_parked(st)
        return True  # just parked; nothing is pending, so no relaunch

    def _nudge_if_mail_is_waiting(self, row, st, idle, now) -> None:
        if not (idle and unread_mail_only(row.worktree_path)):
            st["mail_waiting_since"] = None
            return
        if st["mail_waiting_since"] is None:
            st["mail_waiting_since"] = now
        elif now - st["mail_waiting_since"] >= NUDGE_AFTER_S:
            self.log("nudging", row.role)
            self.herdr.wake(row.pane_id)
            st["mail_waiting_since"] = now

    def _launch(self, index, row, st, now) -> None:
        if st["fast_exits"] >= MAX_FAST_EXITS:
            if st["status"] != "failed":
                self.log("not-relaunching", row.role, f"fast_exits={st['fast_exits']}")
                st["status"] = "failed"
            return
        task = next_task(row.worktree_path, st["task"])
        plan = plan_session(row.agent, row.role, task, st)
        self.log(
            "launching", row.role, f"task={task or '-'}",
            "resume" if plan and plan.resume else "fresh",
        )
        self.launcher(index, row, plan)
        st.update(
            status="running",
            task=task,
            session_id=plan.session_id if plan else "",
            launched_at=now,
            sent_since_launch=False,
            idle_since=None,
            mail_waiting_since=None,
        )

    @staticmethod
    def _mark_parked(st) -> None:
        st["status"] = "parked"
        st["idle_since"] = None


class HerdrPanes:
    """The Scheduler's view of one herdr session."""

    def __init__(self, session):
        self.session = session

    def pane_statuses(self):
        from .herdr_ops import pane_statuses
        return pane_statuses(self.session)

    def foreground(self, pane_id):
        from .herdr_ops import pane_foreground
        return pane_foreground(self.session, pane_id)

    def wake(self, pane_id):
        from .herdr_ops import pane_run
        pane_run(self.session, pane_id, WAKE_MESSAGE)

    def stop(self, pane_id):
        """Stop whatever runs in the pane (agent plus its launch wrapper)
        and leave the pane at its shell. Stopping the wrapper too matters:
        the specifier's wrapper tears the whole swarm down when its agent
        exits, which must only happen when a human quits it."""
        from .herdr_ops import pane_foreground, pane_send_keys
        pgid, shell_pid = pane_foreground(self.session, pane_id)
        if pgid is None or pgid == shell_pid:
            return
        if not hasattr(os, "killpg"):
            # Windows: no process groups to signal; interrupt the agent.
            pane_send_keys(self.session, pane_id, "C-c")
            pane_send_keys(self.session, pane_id, "C-c")
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + STOP_TIMEOUT_S
            while time.monotonic() < deadline:
                current, _ = pane_foreground(self.session, pane_id)
                if current != pgid:
                    return
                time.sleep(0.2)
