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

- A running agent with unread mail (inbox/new) and no task in progress is
  sent the wake-up again with a backoff (WAKE_BACKOFF_S) until the mail is
  picked up: agents ignore wake-ups while busy, so one can be lost. Only after
  MAX_WAKE_ATTEMPTS, WAKE_GIVEUP_S of waiting and GIVEUP_IDLE_TICKS consecutive
  idle ticks is it stopped (verified) and relaunched. The first role (the
  human-facing specifier) is never stopped and only typed into while idle.
  Mail that arrives while a task is in progress is read when the agent runs
  done_with_current / ready_for_next. A role that must reply to its task
  (REPLY_REQUIRED_ROLES) and sits idle on it for STALE_TASK_S with no reply
  sent is reminded to continue or run done_with_current.
- Agents still booting (START_GRACE_S + BOOT_MARGIN_S) are not woken.
- Stopping an agent is verified: a role is marked parked only when the pane's
  foreground is back at its shell, and the launcher is never typed into a
  pane where an agent is still alive.

Roles overlap briefly by design: the recipient starts as soon as mail
arrives while the sender is still finishing up.
"""

import os
import signal
import subprocess
import time
from pathlib import Path

from .env import env_long
from .handoff.format import header_field
from .handoff.inbox import batch_dirs, handoff_files
from .handoff.validate import REPLY_REQUIRED_ROLES
from .sessions import find_kiro_session, plan_session

PARK_GRACE_S = env_long("SWARMFORGE_PARK_GRACE_MS", 15000) / 1000
# Right after a launch the pane still shows its shell until the wrapper
# execs the agent; do not read that as "the agent exited".
START_GRACE_S = 20
FAST_EXIT_S = 60
MAX_FAST_EXITS = 3
STOP_TIMEOUT_S = 10
# Re-send the wake-up this long after the previous one (the last value repeats).
WAKE_BACKOFF_S = (15, 30, 60, 120)
# The first attempts are typed even if herdr does not report idle (text typed
# into a busy Claude is queued); later ones need an idle pane.
WAKE_FREE_ATTEMPTS = 3
MAX_WAKE_ATTEMPTS = 6
WAKE_GIVEUP_S = env_long("SWARMFORGE_WAKE_GIVEUP_MS", 1800000) / 1000
GIVEUP_IDLE_TICKS = 3
STALE_IDLE_TICKS = 3
BOOT_MARGIN_S = 10
# A fresh kiro-cli launch has its session id looked up this often, this many
# times (each lookup is a bounded subprocess call on the scheduler thread).
KIRO_LOOKUP_EVERY_S = 10
KIRO_LOOKUP_MAX = 6
WAKE_DOUBLE_SUBMIT_S = 60
FG_UNKNOWN_LAUNCH_S = 30
RECENT_LAUNCH_S = 120
STALE_TASK_S = env_long("SWARMFORGE_STALE_TASK_MS", 300000) / 1000
NUDGE_AFTER_S = env_long("SWARMFORGE_NUDGE_AFTER_MS", 60000) / 1000  # stale-task reminder interval
STOP_VERIFY_POLLS = 5
STOP_VERIFY_INTERVAL_S = 1
LOG_REPEAT_S = 60
IDLE_STATUSES = {"idle", "done"}
WAKE_MESSAGE = "You have new handoff mail. If idle, run ready_for_next."
STALE_MESSAGE = (
    "You have a task in progress. Continue the current task or run done_with_current."
)


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


def in_process_count(worktree: Path) -> int:
    inbox = _inbox(worktree)
    return len(handoff_files(inbox / "in_process")) + len(batch_dirs(inbox / "in_process"))


def _reply_sent(handoffs: Path, path: Path) -> bool:
    task = header_field(path, "task")
    since = header_field(path, "dequeued_at") or ""
    for sent in handoff_files(handoffs / "outbox") + handoff_files(handoffs / "sent"):
        if (header_field(sent, "type") == "git_handoff" and header_field(sent, "task") == task
                and (header_field(sent, "created_at") or "") >= since):
            return True
    return False


def has_stale_candidate(worktree: Path) -> bool:
    """A git_handoff with a task sits in in_process, no mail waits, and no reply
    to it was sent. Notes and task-less files expect no reply: never stale."""
    inbox = _inbox(worktree)
    if handoff_files(inbox / "new"):
        return False
    current = handoff_files(inbox / "in_process")
    for batch in batch_dirs(inbox / "in_process"):
        current += handoff_files(batch)
    handoffs = worktree / ".swarmforge" / "handoffs"
    work = [p for p in current if header_field(p, "type") == "git_handoff" and header_field(p, "task")]
    return any(not _reply_sent(handoffs, p) for p in work)


def next_task(worktree: Path, fallback: str) -> str:
    """Feature name of the work the role will pick up next, in the order
    ready_for_next hands it out. Notes carry no task: keep the current."""
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
    def __init__(self, rows, herdr, launcher, log, clock=time.time, sleep=time.sleep,
                 kiro_lookup=find_kiro_session):
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
        self.sleep = sleep
        self.kiro_lookup = kiro_lookup  # (cwd, since_epoch) -> (session_id | None, reason)
        self._logged = {}       # key -> (signature, time) for rate-limited logs
        self._fg_unknown = {}   # role -> clock() since which the foreground has been continuously unknown
        self._stops_this_tick = 0

    def _log_limited(self, key, signature, *parts):
        """Log when `signature` changed or LOG_REPEAT_S passed since the last one."""
        now = self.clock()
        last = self._logged.get(key)
        if last is not None and last[0] == signature and now - last[1] < LOG_REPEAT_S:
            return
        self._logged[key] = (signature, now)
        self.log(*parts)

    def _stop_allowed(self) -> bool:
        """At most one stop (and its verification wait) per tick, so a stubborn
        agent cannot stall handoff delivery that shares the daemon thread."""
        return self._stops_this_tick < 1

    def _stop_and_verify(self, row) -> bool:
        """Stop the pane's agent and confirm its foreground is the shell again.
        Never raises; False means the agent may still be alive (or herdr is mute)."""
        role = row.role
        self._stops_this_tick += 1
        try:
            outcome = self.herdr.stop(row.pane_id)
        except Exception as e:  # noqa: BLE001
            self.log("stop-error", role, repr(e))
            return False
        if outcome == "failed":
            self.log("park-failed", role,
                     "the agent did not exit after the interrupt, /exit and the process tree kill")
        pgid, shell_pid = None, None
        for attempt in range(STOP_VERIFY_POLLS):
            pgid, shell_pid = self.herdr.foreground(row.pane_id)
            if pgid is not None and pgid == shell_pid:
                self.log("stop-result", role, "shell-reached")
                return True
            if attempt < STOP_VERIFY_POLLS - 1:
                self.sleep(STOP_VERIFY_INTERVAL_S)
        outcome = "foreground-unknown" if pgid is None else "agent-still-alive"
        self.log("stop-result", role, outcome, f"fg={pgid} shell={shell_pid}")
        return False

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
        self._stops_this_tick = 0
        statuses = self.herdr.pane_statuses()
        for index, row in enumerate(self.rows):
            try:
                self._tick_role(index, row, state[row.role], statuses, now)
            except Exception as e:  # noqa: BLE001 - one bad role must not starve the others
                self._log_limited(("tick-error", row.role), f"{type(e).__name__}:{e}",
                                  "tick-error", row.role, repr(e))

    def _tick_role(self, index, row, st, statuses, now) -> None:
        pending = pending_work(row.worktree_path)
        if st["status"] == "running":
            if self._check_running(index, row, st, pending, statuses, now):
                return
        if st["status"] == "wanted" or (st["status"] == "parked" and pending):
            self._launch(index, row, st, now)

    def _check_running(self, index, row, st, pending, statuses, now) -> bool:
        """Returns True while the role stays running."""
        pgid, shell_pid = self.herdr.foreground(row.pane_id)
        if pgid is None:
            self._log_limited(("fg-unknown", row.role), "unknown", "foreground-unknown", row.role,
                              "herdr did not report the pane's foreground process")
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
        self._record_kiro_session(row, st, now)
        idle = statuses.get(row.pane_id) in IDLE_STATUSES
        st["idle_ticks"] = st["idle_ticks"] + 1 if idle else 0
        if not self._wake_if_mail_is_waiting(index, row, st, idle, statuses, now):
            return False  # woke it too often in vain: it was stopped, relaunch
        self._remind_stale_task(index, row, st, idle, now)
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
        if pending_work(row.worktree_path):
            st["idle_since"] = None  # mail arrived during the grace period
            return True
        if not self._stop_allowed():
            return True  # another role is being stopped this tick; look again next tick
        self.log("parking", row.role)
        if self._stop_and_verify(row):
            self._mark_parked(st)
        else:
            self.log("park-pending", row.role, "agent still in the foreground; staying running")
            st["idle_since"] = now  # look again after another grace period
        return True

    def _record_kiro_session(self, row, st, now) -> None:
        """kiro-cli picks its own session id. Find the one a fresh launch
        created (by worktree and launch time) and keep it in the role state:
        that stored id is the only one a later launch will resume."""
        if (row.agent != "kiro-cli" or st["session_id"] or st["launched_at"] is None
                or st["kiro_lookups"] >= KIRO_LOOKUP_MAX):
            return
        reference = st["kiro_lookup_at"] if st["kiro_lookup_at"] is not None else st["launched_at"]
        if now - reference < KIRO_LOOKUP_EVERY_S:
            return
        st["kiro_lookup_at"] = now
        st["kiro_lookups"] += 1
        found, reason = self.kiro_lookup(row.worktree_path, st["launched_at"])
        if found:
            st["session_id"] = found
            self.log("kiro-session-recorded", row.role, found)
            return
        self.log("kiro-session-lookup", row.role, f"attempt={st['kiro_lookups']}", reason)
        if st["kiro_lookups"] >= KIRO_LOOKUP_MAX:
            self.log("kiro-session-unrecorded", row.role,
                     "gave up; the next launch for this feature starts fresh")

    @staticmethod
    def _booting(st, now) -> bool:
        return now - (st["launched_at"] or 0) <= START_GRACE_S + BOOT_MARGIN_S

    def _wake_if_mail_is_waiting(self, index, row, st, idle, statuses, now) -> bool:
        """Re-send the wake-up (with backoff) while mail sits in inbox/new and
        nothing is in progress. Returns False when the agent was stopped for
        ignoring the wake-ups for a long time (the caller relaunches it)."""
        if not unread_mail_only(row.worktree_path):
            st["wake_pending_since"] = None
            st["wake_attempts"] = 0
            st["last_wake_at"] = None
            return True
        if self._booting(st, now):
            return True  # still starting up: not a missed wake-up, not counted
        if st["wake_pending_since"] is None:
            st["wake_pending_since"] = now
        attempts = st["wake_attempts"]
        herdr_status = statuses.get(row.pane_id, "unknown")
        self._log_limited(
            ("mail", row.role), (st["status"], herdr_status, attempts),
            "mail-waiting", row.role, f"state={st['status']}", f"herdr={herdr_status}", "fg=agent",
            f"in_process={in_process_count(row.worktree_path)}", f"attempt={attempts}")
        reference = st["last_wake_at"] if st["last_wake_at"] is not None else st["wake_pending_since"]
        delay = WAKE_BACKOFF_S[min(attempts, len(WAKE_BACKOFF_S) - 1)]
        if now - reference < delay:
            return True
        if attempts >= MAX_WAKE_ATTEMPTS:
            if index == 0:
                # The human-facing pane is never stopped; keep nudging while idle.
                self._log_limited(("human-pane", row.role), "x", "wake-gave-up-human-pane", row.role,
                                  f"attempts={attempts}", "not stopping the human-facing pane")
            elif (now - st["wake_pending_since"] >= WAKE_GIVEUP_S
                    and st["idle_ticks"] >= GIVEUP_IDLE_TICKS and self._stop_allowed()):
                self.log("wake-gave-up", row.role, f"attempts={attempts}", "stopping the agent to relaunch it")
                if self._stop_and_verify(row):
                    st["wake_attempts"] = 0
                    self._mark_parked(st)
                    return False
                st["last_wake_at"] = now  # could not stop it; try again after the backoff
                return True
        if index == 0 and not idle:
            return True  # never type into the human's pane while it is busy
        if attempts >= WAKE_FREE_ATTEMPTS and not idle:
            return True
        if (attempts in (1, 2) and herdr_status == "working" and st["last_wake_at"] is not None
                and now - st["last_wake_at"] < WAKE_DOUBLE_SUBMIT_S):
            return True  # the previous wake is probably still queued behind its work
        self.log("nudging", row.role, f"attempt={attempts + 1}")
        self.herdr.wake(row.pane_id)
        st["wake_attempts"] = attempts + 1
        st["last_wake_at"] = now
        return True

    def _remind_stale_task(self, index, row, st, idle, now) -> None:
        """A task has sat in in_process while the agent was idle for several
        ticks and no reply was sent: remind it, once per NUDGE_AFTER_S. Only for
        roles that must reply, never for the human-facing first role."""
        eligible = (index > 0 and row.role in REPLY_REQUIRED_ROLES and idle
                    and st["idle_ticks"] >= STALE_IDLE_TICKS and not self._booting(st, now)
                    and has_stale_candidate(row.worktree_path))
        if not eligible:
            st["stale_since"] = None
            return
        if st["stale_since"] is None:
            st["stale_since"] = now
            return
        if now - st["stale_since"] < STALE_TASK_S:
            return
        last = st["last_stale_nudge_at"]
        if last is not None and now - last < NUDGE_AFTER_S:
            return
        self.log("stale-task-nudge", row.role)
        self.herdr.wake(row.pane_id, STALE_MESSAGE)
        st["last_stale_nudge_at"] = now

    def _launch(self, index, row, st, now) -> None:
        if st["fast_exits"] >= MAX_FAST_EXITS:
            if st["status"] != "failed":
                self.log("not-relaunching", row.role, f"fast_exits={st['fast_exits']}")
                st["status"] = "failed"
            return
        if not self._pane_ready_for_launch(row, st, now):
            return
        task = next_task(row.worktree_path, st["task"])
        plan = plan_session(row.agent, row.role, task, st)
        if row.agent == "kiro-cli" and plan and not plan.resume and st["task"] == task and task:
            self.log("kiro-resume-fallback", row.role, f"task={task}",
                     "no recorded session id for this feature; starting fresh")
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
            wake_pending_since=None,
            wake_attempts=0,
            last_wake_at=None,
            stale_since=None,
            last_stale_nudge_at=None,
            idle_ticks=0,
            kiro_lookups=0,
            kiro_lookup_at=None,
        )
        self._fg_unknown.pop(row.role, None)

    def _pane_ready_for_launch(self, row, st, now) -> bool:
        """Never type the launcher into a pane where an agent is still alive:
        that would run the launcher inside it and start a second session."""
        role = row.role
        pgid, shell_pid = self.herdr.foreground(row.pane_id)
        if pgid is None:
            since = self._fg_unknown.setdefault(role, now)
            just_launched = (st["status"] == "running" and st["launched_at"] is not None
                             and now - st["launched_at"] < RECENT_LAUNCH_S)
            if just_launched or now - since < FG_UNKNOWN_LAUNCH_S:
                self._log_limited(("fg-unknown-launch", role), "x", "launch-deferred", role,
                                  "foreground unknown", f"for={int(now - since)}s")
                return False
            self.log("launch-unverified", role, "foreground unknown for a while; launching anyway")
            return True
        self._fg_unknown.pop(role, None)  # known again: the unknown window restarts
        if pgid == shell_pid:
            return True
        if not self._stop_allowed():
            return False  # one stop per tick; try again next tick
        self.log("launch-deferred", role, "an agent is still running in the pane; stopping it first")
        if self._stop_and_verify(row):
            return True
        self.log("park-failed", role, "agent did not leave the foreground; staying running")
        if st["status"] != "running":
            st["status"] = "running"
            st["launched_at"] = now
        st["idle_since"] = None
        return False

    @staticmethod
    def _mark_parked(st) -> None:
        st["status"] = "parked"
        st["idle_since"] = None


class HerdrPanes:
    """The Scheduler's view of one herdr session."""

    def __init__(self, session, sleep=time.sleep):
        self.session = session
        self.sleep = sleep

    def pane_statuses(self):
        from .herdr_ops import pane_statuses
        return pane_statuses(self.session)

    def foreground(self, pane_id):
        from .herdr_ops import pane_foreground
        return pane_foreground(self.session, pane_id)

    def wake(self, pane_id, message=None):
        from .herdr_ops import pane_run
        pane_run(self.session, pane_id, message or WAKE_MESSAGE)

    def stop(self, pane_id):
        """Stop whatever runs in the pane (agent plus its launch wrapper)
        and leave the pane at its shell. Stopping the wrapper too matters:
        the specifier's wrapper tears the whole swarm down when its agent
        exits, which must only happen when a human quits it.

        Returns 'stopped' (the pane is back at its shell), 'already-stopped'
        (nothing was running) or 'failed' (the agent is still alive). The
        caller must not mark the role parked unless the answer is not
        'failed': an agent that survived the stop would receive the next
        wake-up inside its own prompt."""
        from .herdr_ops import pane_foreground
        pgid, shell_pid = pane_foreground(self.session, pane_id)
        if pgid is None or pgid == shell_pid:
            return "already-stopped"
        if not hasattr(os, "killpg"):
            return self._stop_windows(pane_id, pgid)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                return "stopped"
            deadline = time.monotonic() + STOP_TIMEOUT_S
            while time.monotonic() < deadline:
                current, _ = pane_foreground(self.session, pane_id)
                if current != pgid:
                    return "stopped"
                time.sleep(0.2)
        return "failed"

    # -- Windows: no process groups to signal -------------------------------------

    def _stop_windows(self, pane_id, pgid):
        """Interrupt, ask the agent to quit and then kill its process tree.

        A busy agent can ignore a Ctrl-C, and a role that the scheduler
        believes is parked while its agent is still alive is a trap: the next
        handoff would be typed into that live agent. So the ladder goes all
        the way to a tree kill, and says so when even that does not work."""
        self._interrupt(pane_id)
        if self._agent_gone(pane_id):
            return "stopped"
        self._exit_command(pane_id)
        if self._agent_gone(pane_id):
            return "stopped"
        self._kill_tree(pgid)
        if self._agent_gone(pane_id):
            return "stopped"
        return "failed"

    def _interrupt(self, pane_id):
        """Two Ctrl-Cs with a pause: the first one may be swallowed while the
        agent is busy."""
        from .herdr_ops import pane_send_keys
        pane_send_keys(self.session, pane_id, "C-c")
        self.sleep(1)
        pane_send_keys(self.session, pane_id, "C-c")
        self.sleep(1)

    def _exit_command(self, pane_id):
        """The agent's own exit command, for the agents that ignore Ctrl-C.

        `pane run` types the line and presses Enter, so the command is really
        submitted (a key sequence alone would only fill the prompt)."""
        from .herdr_ops import pane_run
        pane_run(self.session, pane_id, "/exit")
        self.sleep(2)

    def _kill_tree(self, pgid):
        """taskkill /T /F on the agent's process tree (Windows only)."""
        pid = self._pid_of(pgid)
        if not pid:
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    @staticmethod
    def _pid_of(pgid):
        try:
            pid = int(pgid)
        except (TypeError, ValueError):
            return None
        return pid if pid > 0 else None

    def _agent_gone(self, pane_id):
        """True when the pane is back at its shell (or herdr cannot tell)."""
        from .herdr_ops import pane_foreground
        current, shell_pid = pane_foreground(self.session, pane_id)
        return current is None or current == shell_pid
