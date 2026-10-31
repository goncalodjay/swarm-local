"""Unit tests for on-demand role agents: session planning, launch commands,
the scheduler's launch/park rules, and handoff delivery hooks.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python import agent_state, scheduler as sched  # noqa: E402
from swarm_python.config import RoleRow  # noqa: E402
from swarm_python.env import shell_quote  # noqa: E402
from swarm_python.entrypoints import handoffd  # noqa: E402
from swarm_python.launch import build_launch_command  # noqa: E402
from swarm_python.paths import build_context  # noqa: E402
from swarm_python.sessions import RESUME_PROMPT, SessionPlan, plan_session  # noqa: E402

ROLES = ["specifier", "coder", "reviewer", "architect"]


def write_handoff(worktree: Path, box: str, name: str, task: str = "", sender: str = "specifier"):
    target = worktree / ".swarmforge" / "handoffs" / "inbox" / box
    target.mkdir(parents=True, exist_ok=True)
    headers = [f"from: {sender}", "type: git_handoff", "priority: 50"]
    if task:
        headers.append(f"task: {task}")
    (target / name).write_text("\n".join(headers) + "\n\nbody\n", encoding="utf-8")


class FakeHerdr:
    def __init__(self):
        self.status = {}      # pane_id -> herdr agent_status
        self.agent_up = {}    # pane_id -> is an agent in the foreground
        self.stopped = []
        self.woken = []
        self.messages = []
        self.answer = True
        self.stop_works = True   # False: the agent ignores the interrupt

    def pane_statuses(self):
        return dict(self.status)

    def foreground(self, pane_id):
        if not self.answer:
            return None, None
        return (200, 100) if self.agent_up.get(pane_id) else (100, 100)

    def stop(self, pane_id):
        self.stopped.append(pane_id)
        if self.stop_works:
            self.agent_up[pane_id] = False

    def wake(self, pane_id, message=None):
        self.woken.append(pane_id)
        self.messages.append(message)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.rows = [
            RoleRow(role=r, agent="claude", session=f"swarmforge-{r}", display_name=r,
                    worktree_name=r, worktree_path=root / r, receive_mode="task",
                    extra_args=None, workspace_id=f"w{i}", pane_id=f"w{i}:p1")
            for i, r in enumerate(ROLES, start=1)
        ]
        for row in self.rows:
            row.worktree_path.mkdir()
        self.herdr = FakeHerdr()
        self.clock = Clock()
        self.launches = []
        self.logs = []
        self.s = sched.Scheduler(self.rows, self.herdr, self._launch, lambda *parts: self.logs.append(parts), self.clock,
                                sleep=lambda seconds: None)
        self.state = agent_state.initial_state(ROLES)

    def tearDown(self):
        self.tmp.cleanup()

    def _launch(self, index, row, plan):
        self.launches.append((row.role, plan))
        self.herdr.agent_up[row.pane_id] = True
        self.herdr.status[row.pane_id] = "working"

    def row(self, role):
        return next(r for r in self.rows if r.role == role)

    def idle(self, role):
        self.herdr.status[self.row(role).pane_id] = "idle"

    def test_only_the_specifier_starts(self):
        self.s.tick(self.state)
        self.assertEqual([r for r, _ in self.launches], ["specifier"])
        self.assertEqual(self.state["specifier"]["status"], "running")
        self.assertEqual(self.state["coder"]["status"], "parked")

    def test_parked_role_with_mail_is_launched_on_that_feature(self):
        write_handoff(self.row("coder").worktree_path, "new", "a.handoff", task="login")
        self.state["specifier"]["status"] = "parked"
        self.s.tick(self.state)
        role, plan = self.launches[-1]
        self.assertEqual(role, "coder")
        self.assertFalse(plan.resume)
        self.assertEqual(self.state["coder"]["task"], "login")
        self.assertEqual(self.state["coder"]["session_id"], plan.session_id)

    def test_same_feature_resumes_and_new_feature_starts_fresh(self):
        coder = self.state["coder"]
        coder.update(task="login", session_id="sess-1")
        write_handoff(self.row("coder").worktree_path, "new", "a.handoff", task="login")
        self.state["specifier"]["status"] = "parked"
        self.s.tick(self.state)
        self.assertEqual(self.launches[-1][1], SessionPlan("sess-1", resume=True))

        self.state["coder"]["status"] = "parked"
        (self.row("coder").worktree_path / ".swarmforge/handoffs/inbox/new/a.handoff").unlink()
        write_handoff(self.row("coder").worktree_path, "new", "b.handoff", task="search")
        self.s.tick(self.state)
        plan = self.launches[-1][1]
        self.assertFalse(plan.resume)
        self.assertNotEqual(plan.session_id, "sess-1")

    def test_note_without_task_keeps_the_current_feature(self):
        self.state["coder"].update(task="login", session_id="sess-1")
        write_handoff(self.row("coder").worktree_path, "new", "n.handoff")
        self.state["specifier"]["status"] = "parked"
        self.s.tick(self.state)
        self.assertEqual(self.launches[-1][1], SessionPlan("sess-1", resume=True))

    def test_idle_role_without_work_is_parked_after_the_grace(self):
        self.state["specifier"]["status"] = "parked"
        write_handoff(self.row("coder").worktree_path, "new", "a.handoff", task="login")
        self.s.tick(self.state)
        (self.row("coder").worktree_path / ".swarmforge/handoffs/inbox/new/a.handoff").unlink()
        self.clock.now += 30
        self.idle("coder")
        self.s.tick(self.state)
        self.assertEqual(self.state["coder"]["status"], "running")
        self.clock.now += sched.PARK_GRACE_S - 1
        self.s.tick(self.state)
        self.assertEqual(self.state["coder"]["status"], "running")
        self.clock.now += 2
        self.s.tick(self.state)
        self.assertEqual(self.state["coder"]["status"], "parked")
        self.assertEqual(self.herdr.stopped, [self.row("coder").pane_id])

    def test_pending_work_or_activity_prevents_parking(self):
        self.state["specifier"]["status"] = "parked"
        write_handoff(self.row("coder").worktree_path, "in_process", "a.handoff", task="login")
        self.s.tick(self.state)
        self.clock.now += 30
        self.idle("coder")
        for _ in range(3):
            self.s.tick(self.state)
            self.clock.now += sched.PARK_GRACE_S
        self.assertEqual(self.state["coder"]["status"], "running")
        self.assertEqual(self.herdr.stopped, [])

    def test_busy_resets_the_idle_timer(self):
        self.state["specifier"]["status"] = "parked"
        self.state["coder"]["status"] = "wanted"
        self.s.tick(self.state)
        self.clock.now += 30
        self.idle("coder")
        self.s.tick(self.state)
        self.clock.now += sched.PARK_GRACE_S - 1
        self.herdr.status[self.row("coder").pane_id] = "working"
        self.s.tick(self.state)
        self.idle("coder")
        self.clock.now += 2
        self.s.tick(self.state)
        self.assertEqual(self.state["coder"]["status"], "running")

    def test_specifier_is_only_parked_after_it_forwarded_work(self):
        self.s.tick(self.state)
        self.clock.now += 30
        self.idle("specifier")
        for _ in range(3):
            self.s.tick(self.state)
            self.clock.now += sched.PARK_GRACE_S
        self.assertEqual(self.state["specifier"]["status"], "running")

        self.s.on_sent(self.state, "specifier", "login")
        self.assertEqual(self.state["specifier"]["task"], "login")
        self.s.tick(self.state)
        self.clock.now += sched.PARK_GRACE_S + 1
        self.s.tick(self.state)
        self.assertEqual(self.state["specifier"]["status"], "parked")

    def test_agent_that_exits_on_its_own_is_parked(self):
        self.s.tick(self.state)
        self.clock.now += 120
        self.herdr.agent_up[self.row("specifier").pane_id] = False
        self.s.tick(self.state)
        self.assertEqual(self.state["specifier"]["status"], "parked")
        self.assertEqual(len(self.launches), 1)

    def test_pane_still_starting_is_not_read_as_an_exit(self):
        self.herdr.agent_up = {}
        self.s.launcher = lambda i, row, plan: self.launches.append((row.role, plan))
        self.s.tick(self.state)
        self.clock.now += sched.START_GRACE_S - 1
        self.s.tick(self.state)
        self.assertEqual(self.state["specifier"]["status"], "running")
        self.assertEqual(len(self.launches), 1)

    def test_failed_resume_retries_fresh_then_gives_up(self):
        self.state["specifier"]["status"] = "parked"
        self.state["coder"].update(task="login", session_id="gone")
        write_handoff(self.row("coder").worktree_path, "new", "a.handoff", task="login")
        for _ in range(sched.MAX_FAST_EXITS + 2):
            self.s.tick(self.state)
            self.clock.now += sched.START_GRACE_S + 1
            self.herdr.agent_up[self.row("coder").pane_id] = False
            self.s.tick(self.state)
        coder_launches = [p for r, p in self.launches if r == "coder"]
        self.assertTrue(coder_launches[0].resume)
        self.assertFalse(coder_launches[1].resume)
        self.assertEqual(len(coder_launches), sched.MAX_FAST_EXITS)
        self.assertEqual(self.state["coder"]["status"], "failed")

    def start_coder(self):
        self.state["specifier"]["status"] = "parked"
        self.state["coder"]["status"] = "wanted"
        self.s.tick(self.state)
        # Past the boot grace: wakes are not sent to an agent that is still starting.
        self.clock.now += sched.START_GRACE_S + sched.BOOT_MARGIN_S + 1
        return self.row("coder").worktree_path

    def write_raw(self, worktree, box, name, text):
        d = worktree / ".swarmforge" / "handoffs" / "inbox" / box
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    def launches_of(self, role):
        return len([1 for r, _ in self.launches if r == role])

    def test_unread_mail_is_re_sent_with_backoff_until_picked_up(self):
        # Adapted on purpose: the wake used to repeat every NUDGE_AFTER_S only
        # while herdr said idle; it now backs off 15s, 30s, 60s, 120s and the
        # first attempts do not need an idle pane.
        wt = self.start_coder()
        write_handoff(wt, "new", "a.handoff", task="login")
        self.s.tick(self.state)   # first sight of the mail (herdr says "working")
        self.clock.now += sched.WAKE_BACKOFF_S[0] - 1
        self.s.tick(self.state)
        self.assertEqual(self.herdr.woken, [])
        self.clock.now += 2
        self.s.tick(self.state)
        self.assertEqual(self.herdr.woken, [self.row("coder").pane_id])
        self.assertEqual(self.state["coder"]["wake_attempts"], 1)
        self.clock.now += sched.WAKE_BACKOFF_S[1] + 1
        self.s.tick(self.state)   # still "working" and the last wake is younger than 60s: no double submit
        self.assertEqual(len(self.herdr.woken), 1)
        self.clock.now += sched.WAKE_DOUBLE_SUBMIT_S
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.woken), 2)

    def test_second_wake_goes_out_at_the_backoff_when_the_pane_is_not_working(self):
        wt = self.start_coder()
        write_handoff(wt, "new", "a.handoff", task="login")
        self.s.tick(self.state)
        self.clock.now += sched.WAKE_BACKOFF_S[0] + 1
        self.s.tick(self.state)
        self.idle("coder")
        self.clock.now += sched.WAKE_BACKOFF_S[1] + 1
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.woken), 2)

    def test_later_wake_attempts_need_an_idle_pane(self):
        wt = self.start_coder()
        write_handoff(wt, "new", "a.handoff", task="login")
        self.state["coder"]["wake_attempts"] = sched.WAKE_FREE_ATTEMPTS
        self.s.tick(self.state)
        self.clock.now += 200
        self.s.tick(self.state)          # herdr says "working": no more typing
        self.assertEqual(self.herdr.woken, [])
        self.idle("coder")
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.woken), 1)

    def test_picking_the_mail_up_resets_the_wake_state(self):
        wt = self.start_coder()
        write_handoff(wt, "new", "a.handoff", task="login")
        self.s.tick(self.state)
        self.clock.now += 20
        self.s.tick(self.state)
        self.assertEqual(self.state["coder"]["wake_attempts"], 1)
        (wt / ".swarmforge/handoffs/inbox/new/a.handoff").unlink()
        write_handoff(wt, "in_process", "a.handoff", task="login")
        self.s.tick(self.state)
        coder = self.state["coder"]
        self.assertEqual((coder["wake_attempts"], coder["wake_pending_since"], coder["last_wake_at"]),
                         (0, None, None))

    def test_wake_never_types_into_a_shell_prompt(self):
        wt = self.start_coder()
        write_handoff(wt, "new", "a.handoff", task="login")
        self.herdr.agent_up[self.row("coder").pane_id] = False   # the agent died
        self.clock.now += 100
        self.s.tick(self.state)
        self.assertEqual(self.herdr.woken, [])
        self.assertEqual(self.launches_of("coder"), 2)           # relaunched instead

    def test_a_booting_agent_is_not_woken_and_nothing_is_counted(self):
        self.state["specifier"]["status"] = "parked"
        self.state["coder"]["status"] = "wanted"
        self.s.tick(self.state)                                    # launch
        wt = self.row("coder").worktree_path
        write_handoff(wt, "new", "a.handoff", task="login")
        self.clock.now += sched.START_GRACE_S + sched.BOOT_MARGIN_S - 1
        for _ in range(3):
            self.s.tick(self.state)
        self.assertEqual(self.herdr.woken, [])
        self.assertEqual((self.state["coder"]["wake_attempts"], self.state["coder"]["wake_pending_since"]),
                         (0, None))
        self.clock.now += 2
        self.s.tick(self.state)                                    # boot over: the wait starts now
        self.clock.now += sched.WAKE_BACKOFF_S[0] + 1
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.woken), 1)

    def give_up_setup(self):
        wt = self.start_coder()
        write_handoff(wt, "new", "a.handoff", task="login")
        self.state["coder"]["wake_attempts"] = sched.MAX_WAKE_ATTEMPTS
        self.idle("coder")
        return wt

    def test_giving_up_needs_a_long_wait_and_sustained_idle(self):
        # Adapted on purpose: reaching MAX_WAKE_ATTEMPTS alone used to stop the
        # agent; now it also needs WAKE_GIVEUP_S and GIVEUP_IDLE_TICKS idle ticks.
        self.give_up_setup()
        self.s.tick(self.state)
        self.clock.now += 200
        self.s.tick(self.state)
        self.assertEqual(self.herdr.stopped, [])                   # attempts alone are not enough
        self.clock.now += sched.WAKE_GIVEUP_S
        self.herdr.status[self.row("coder").pane_id] = "working"
        self.s.tick(self.state)                                    # long enough, but busy: idle streak resets
        self.assertEqual(self.herdr.stopped, [])
        self.idle("coder")
        for _ in range(sched.GIVEUP_IDLE_TICKS - 1):
            self.s.tick(self.state)
            self.assertEqual(self.herdr.stopped, [])
            self.clock.now += 200
        self.s.tick(self.state)
        self.assertEqual(self.herdr.stopped, [self.row("coder").pane_id])
        self.assertEqual(self.launches_of("coder"), 2)
        self.assertEqual(self.state["coder"]["status"], "running")
        self.assertEqual(self.state["coder"]["wake_attempts"], 0)
        self.assertTrue(any(p[0] == "wake-gave-up" for p in self.logs))

    def spec_with_mail(self):
        self.s.tick(self.state)                                    # launches the specifier
        self.clock.now += sched.START_GRACE_S + sched.BOOT_MARGIN_S + 1
        write_handoff(self.row("specifier").worktree_path, "new", "a.handoff", task="login", sender="architect")
        self.state["specifier"]["wake_attempts"] = sched.MAX_WAKE_ATTEMPTS

    def test_the_human_pane_is_never_stopped_and_only_typed_into_while_idle(self):
        self.spec_with_mail()
        self.idle("specifier")
        for _ in range(sched.GIVEUP_IDLE_TICKS + 2):
            self.s.tick(self.state)
            self.clock.now += sched.WAKE_GIVEUP_S
        self.assertEqual(self.herdr.stopped, [])
        self.assertEqual(self.state["specifier"]["status"], "running")
        self.assertTrue(any(p[0] == "wake-gave-up-human-pane" for p in self.logs))
        self.assertTrue(self.herdr.woken)                          # idle: nudging continues
        woken = len(self.herdr.woken)
        self.herdr.status[self.row("specifier").pane_id] = "working"
        self.state["specifier"]["wake_attempts"] = 0
        self.state["specifier"]["last_wake_at"] = None
        self.clock.now += 500
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.woken), woken)             # busy human pane: no typing, even a first wake

    def test_a_stop_that_does_not_kill_the_agent_never_types_the_launcher(self):
        wt = self.start_coder()
        self.herdr.stop_works = False
        write_handoff(wt, "new", "a.handoff", task="login")
        self.state["coder"]["status"] = "parked"        # a stale "parked" over a live agent
        for _ in range(3):
            self.clock.now += 10
            self.s.tick(self.state)
        self.assertEqual(self.launches_of("coder"), 1)
        self.assertEqual(self.state["coder"]["status"], "running")
        self.assertTrue(self.herdr.stopped)
        self.clock.now += sched.START_GRACE_S + sched.BOOT_MARGIN_S + sched.WAKE_BACKOFF_S[0]
        self.s.tick(self.state)
        self.clock.now += sched.WAKE_BACKOFF_S[0] + 1
        self.s.tick(self.state)
        self.assertEqual(self.herdr.woken, [self.row("coder").pane_id])  # the wake retry takes over
        self.assertTrue(any(p[0] == "park-failed" for p in self.logs))
        # Once the interrupt finally works the launcher is typed.
        self.herdr.stop_works = True
        self.state["coder"]["status"] = "parked"
        self.s.tick(self.state)
        self.assertEqual(self.launches_of("coder"), 2)

    def test_park_is_only_recorded_once_the_shell_is_back(self):
        self.s.tick(self.state)
        self.s.on_sent(self.state, "specifier", "login")
        self.herdr.stop_works = False
        self.clock.now += 30
        self.idle("specifier")
        self.s.tick(self.state)
        self.clock.now += sched.PARK_GRACE_S + 1
        self.s.tick(self.state)
        self.assertEqual(self.state["specifier"]["status"], "running")
        self.assertTrue(any(p[0] == "park-pending" for p in self.logs))
        self.herdr.stop_works = True
        self.clock.now += sched.PARK_GRACE_S + 1
        self.s.tick(self.state)
        self.assertEqual(self.state["specifier"]["status"], "parked")

    def test_mail_arriving_while_the_park_is_decided_cancels_it(self):
        wt = self.start_coder()
        self.idle("coder")
        self.s.tick(self.state)                                    # idle timer starts
        self.clock.now += sched.PARK_GRACE_S + 1
        real, coder_calls = sched.pending_work, []

        def racy(worktree):
            if worktree == wt:
                coder_calls.append(1)
                return len(coder_calls) > 1                        # mail lands right after the first look
            return real(worktree)

        sched.pending_work = racy
        self.addCleanup(setattr, sched, "pending_work", real)
        self.s.tick(self.state)
        self.assertEqual(self.herdr.stopped, [])
        self.assertEqual(self.state["coder"]["status"], "running")

    def test_only_one_agent_is_stopped_per_tick(self):
        self.state["specifier"]["status"] = "parked"
        self.state["coder"]["status"] = "wanted"
        self.state["reviewer"]["status"] = "wanted"
        self.s.tick(self.state)
        self.clock.now += sched.START_GRACE_S + sched.BOOT_MARGIN_S + 1
        self.idle("coder")
        self.idle("reviewer")
        self.s.tick(self.state)
        self.clock.now += sched.PARK_GRACE_S + 1
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.stopped), 1)
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.stopped), 2)
        self.assertEqual((self.state["coder"]["status"], self.state["reviewer"]["status"]), ("parked", "parked"))

    def test_a_role_that_raises_does_not_stop_the_others(self):
        self.state["specifier"]["status"] = "parked"
        self.state["coder"]["status"] = "wanted"
        self.state["architect"]["status"] = "wanted"
        coder_wt = self.row("coder").worktree_path
        real = sched.pending_work

        def broken(worktree):
            if worktree == coder_wt:
                raise OSError("disk hiccup")
            return real(worktree)

        sched.pending_work = broken
        self.addCleanup(setattr, sched, "pending_work", real)
        self.s.tick(self.state)
        self.s.tick(self.state)
        self.assertEqual([r for r, _ in self.launches], ["architect"])
        self.assertEqual(len([p for p in self.logs if p[0] == "tick-error"]), 1)   # throttled

    def test_unknown_foreground_is_logged_and_does_not_block_the_launch_forever(self):
        self.herdr.answer = False
        self.s.tick(self.state)                      # the specifier is "wanted"
        self.assertEqual(self.launches, [])
        self.assertTrue(any(p[0] == "launch-deferred" for p in self.logs))
        self.clock.now += sched.FG_UNKNOWN_LAUNCH_S - 1
        self.s.tick(self.state)
        self.assertEqual(self.launches, [])
        self.clock.now += 2
        self.s.tick(self.state)
        self.assertEqual([r for r, _ in self.launches], ["specifier"])
        self.assertTrue(any(p[0] == "launch-unverified" for p in self.logs))

    def test_the_unknown_window_restarts_once_the_foreground_is_known(self):
        row, st = self.row("coder"), self.state["coder"]
        self.herdr.answer = False
        self.assertFalse(self.s._pane_ready_for_launch(row, st, self.clock.now))
        self.clock.now += sched.FG_UNKNOWN_LAUNCH_S + 5
        self.herdr.answer = True
        self.assertTrue(self.s._pane_ready_for_launch(row, st, self.clock.now))   # known: shell
        self.assertNotIn("coder", self.s._fg_unknown)
        self.herdr.answer = False
        self.assertFalse(self.s._pane_ready_for_launch(row, st, self.clock.now))  # window restarted

    def test_a_recently_launched_running_role_is_not_launched_blind(self):
        row, st = self.row("coder"), self.state["coder"]
        st.update(status="running", launched_at=self.clock.now - 10)
        self.s._fg_unknown["coder"] = self.clock.now - 1000        # unknown for a very long time
        self.herdr.answer = False
        self.assertFalse(self.s._pane_ready_for_launch(row, st, self.clock.now))
        st["launched_at"] = self.clock.now - sched.RECENT_LAUNCH_S - 1
        self.assertTrue(self.s._pane_ready_for_launch(row, st, self.clock.now))

    def test_unknown_foreground_of_a_running_agent_is_logged_once_per_interval(self):
        self.s.tick(self.state)
        self.herdr.answer = False
        for _ in range(5):
            self.clock.now += 5
            self.s.tick(self.state)
        self.assertEqual(len([p for p in self.logs if p[0] == "foreground-unknown"]), 1)

    def test_waiting_mail_is_logged_with_context(self):
        wt = self.start_coder()
        write_handoff(wt, "new", "a.handoff", task="login")
        self.s.tick(self.state)
        self.s.tick(self.state)
        waiting = [p for p in self.logs if p[0] == "mail-waiting"]
        self.assertEqual(len(waiting), 1)    # rate-limited: same state, same minute
        text = " ".join(waiting[0])
        for needle in ("coder", "state=running", "herdr=working", "fg=agent", "in_process=0", "attempt=0"):
            self.assertIn(needle, text)

    def idle_ticks(self, role, count):
        self.idle(role)
        for _ in range(count):
            self.s.tick(self.state)

    def test_stale_in_process_task_is_reminded_once_per_interval(self):
        wt = self.start_coder()
        write_handoff(wt, "in_process", "a.handoff", task="login")
        self.idle_ticks("coder", sched.STALE_IDLE_TICKS)           # one idle sample is not enough
        self.clock.now += sched.STALE_TASK_S - 1
        self.s.tick(self.state)
        self.assertEqual(self.herdr.messages, [])
        self.clock.now += 2
        self.s.tick(self.state)
        self.assertEqual(self.herdr.messages, [sched.STALE_MESSAGE])
        self.assertIn("done_with_current.sh", sched.STALE_MESSAGE)
        self.clock.now += sched.NUDGE_AFTER_S - 1
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.messages), 1)
        self.clock.now += 2
        self.s.tick(self.state)
        self.assertEqual(len(self.herdr.messages), 2)

    def test_a_single_idle_sample_does_not_trigger_the_stale_reminder(self):
        wt = self.start_coder()
        write_handoff(wt, "in_process", "a.handoff", task="login")
        for _ in range(4):
            self.idle("coder")
            self.s.tick(self.state)
            self.herdr.status[self.row("coder").pane_id] = "working"   # busy again before the streak builds
            self.s.tick(self.state)
            self.clock.now += sched.STALE_TASK_S
        self.assertEqual(self.herdr.messages, [])

    def test_stale_reminder_is_skipped_when_a_reply_was_sent(self):
        wt = self.start_coder()
        self.write_raw(wt, "in_process", "a.handoff",
                       "from: reviewer\ntype: git_handoff\ntask: login\ndequeued_at: 2026-01-01T10:00:00Z\n\nb\n")
        sent = wt / ".swarmforge/handoffs/sent"
        sent.mkdir(parents=True, exist_ok=True)
        (sent / "r.handoff").write_text(
            "type: git_handoff\ntask: login\ncreated_at: 2026-01-01T10:05:00Z\n\nb\n", encoding="utf-8")
        self.idle("coder")
        for _ in range(sched.STALE_IDLE_TICKS + 3):
            self.s.tick(self.state)
            self.clock.now += sched.STALE_TASK_S
        self.assertEqual(self.herdr.messages, [])

    def test_stale_reminder_is_skipped_for_notes_and_task_less_files(self):
        wt = self.start_coder()
        self.write_raw(wt, "in_process", "n.handoff", "from: x\ntype: note\nmessage: hi\n\nb\n")
        self.write_raw(wt, "in_process", "t.handoff", "from: x\ntype: git_handoff\n\nb\n")
        self.idle("coder")
        for _ in range(sched.STALE_IDLE_TICKS + 3):
            self.s.tick(self.state)
            self.clock.now += sched.STALE_TASK_S
        self.assertEqual(self.herdr.messages, [])

    def test_stale_reminder_is_skipped_for_the_specifier(self):
        self.s.tick(self.state)
        self.clock.now += sched.START_GRACE_S + sched.BOOT_MARGIN_S + 1
        write_handoff(self.row("specifier").worktree_path, "in_process", "a.handoff", task="login",
                      sender="architect")
        self.idle("specifier")
        for _ in range(sched.STALE_IDLE_TICKS + 3):
            self.s.tick(self.state)
            self.clock.now += sched.STALE_TASK_S
        self.assertEqual(self.herdr.messages, [])

    def test_old_agents_json_without_the_new_fields_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            # "mail_waiting_since" is the legacy key of older versions: it must be tolerated.
            old = {r: {"status": "wanted" if r == "specifier" else "parked", "task": "", "session_id": "",
                       "launched_at": None, "sent_since_launch": False, "idle_since": None,
                       "mail_waiting_since": 5.0, "fast_exits": 0} for r in ROLES}
            (Path(d) / agent_state.STATE_FILE_NAME).write_text(json.dumps(old), encoding="utf-8")
            state = agent_state.load(Path(d), ROLES)
        for role in ROLES:
            self.assertEqual(state[role]["wake_attempts"], 0)
            self.assertEqual(state[role]["idle_ticks"], 0)
            self.assertIsNone(state[role]["wake_pending_since"])
            self.assertIsNone(state[role]["stale_since"])
        self.s.tick(state)       # runs with the migrated record
        self.assertEqual(state["specifier"]["status"], "running")

    def test_idle_agent_with_a_task_in_progress_is_not_nudged(self):
        self.state["specifier"]["status"] = "parked"
        self.state["coder"]["status"] = "wanted"
        self.s.tick(self.state)
        self.clock.now += 30
        write_handoff(self.row("coder").worktree_path, "in_process", "a.handoff", task="login")
        write_handoff(self.row("coder").worktree_path, "new", "b.handoff", task="login")
        self.idle("coder")
        for _ in range(3):
            self.s.tick(self.state)
            self.clock.now += sched.NUDGE_AFTER_S
        self.assertEqual(self.herdr.woken, [])

    def test_no_answer_from_herdr_changes_nothing(self):
        self.s.tick(self.state)
        self.herdr.answer = False
        self.clock.now += 1000
        self.s.tick(self.state)
        self.assertEqual(self.state["specifier"]["status"], "running")
        self.assertEqual(len(self.launches), 1)


class WindowsStopTest(unittest.TestCase):
    def test_stop_sends_ctrl_c_twice_with_a_pause_between(self):
        events = []
        panes = sched.HerdrPanes("sess", sleep=lambda seconds: events.append(("sleep", seconds)))
        with mock.patch("swarm_python.herdr_ops.pane_foreground", return_value=(200, 100)),                 mock.patch("swarm_python.herdr_ops.pane_send_keys",
                           side_effect=lambda session, pane, *keys: events.append(("keys", keys))),                 mock.patch.object(sched, "os", types.SimpleNamespace()):   # no os.killpg: Windows path
            panes.stop("w1:p1")
        self.assertEqual(events, [("keys", ("C-c",)), ("sleep", 1), ("keys", ("C-c",))])


class StateFileTest(unittest.TestCase):
    def test_restart_keeps_sessions_but_nothing_runs(self):
        previous = agent_state.initial_state(ROLES)
        previous["coder"].update(status="running", task="login", session_id="s-1", fast_exits=2)
        state = agent_state.initial_state(ROLES, previous)
        self.assertEqual(state["coder"]["task"], "login")
        self.assertEqual(state["coder"]["session_id"], "s-1")
        self.assertEqual(state["coder"]["status"], "parked")
        self.assertEqual(state["coder"]["fast_exits"], 0)
        self.assertEqual(state["specifier"]["status"], "wanted")

    def test_round_trip_fills_missing_fields(self):
        with tempfile.TemporaryDirectory() as d:
            state = agent_state.initial_state(ROLES)
            state["coder"]["task"] = "login"
            agent_state.save(Path(d), state)
            loaded = agent_state.load(Path(d), ROLES + ["extra"])
            self.assertEqual(loaded["coder"]["task"], "login")
            self.assertEqual(loaded["specifier"]["status"], "wanted")
            self.assertEqual(loaded["extra"], agent_state.blank_role())


class SessionPlanTest(unittest.TestCase):
    def test_backends_without_resume_always_start_fresh(self):
        self.assertIsNone(plan_session("codex", "coder", "login", {"task": "login", "session_id": "x"}))

    def test_hermes_session_names_are_readable_and_unique(self):
        a = plan_session("hermes", "coder", "login", {})
        b = plan_session("hermes", "coder", "login", {})
        self.assertTrue(a.session_id.startswith("swarmforge-coder-login-"))
        self.assertNotEqual(a.session_id, b.session_id)


class LaunchCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / ".swarmforge" / "prompts").mkdir(parents=True)
        self.ctx = build_context(root, SCRIPTS)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, agent, plan):
        row = RoleRow(role="coder", agent=agent, session="swarmforge-coder", display_name="Coder",
                      worktree_name="master", worktree_path=self.root, receive_mode="task",
                      extra_args=None, workspace_id="w2", pane_id="w2:p1")
        return build_launch_command(self.ctx, 1, row, plan)

    def test_agent_panes_export_utf8_environment(self):
        for plan in (None, SessionPlan("u-1", resume=False), SessionPlan("u-1", resume=True)):
            command = self.command("claude", plan)
            self.assertIn("export PYTHONUTF8=1", command)
            self.assertIn("export PYTHONIOENCODING=utf-8", command)

    def test_claude_creates_then_resumes_by_id(self):
        fresh = self.command("claude", SessionPlan("u-1", resume=False))
        self.assertIn("--session-id u-1 ", fresh)
        self.assertIn('"$(<', fresh)
        resumed = self.command("claude", SessionPlan("u-1", resume=True))
        self.assertIn("--resume u-1 ", resumed)
        self.assertIn(RESUME_PROMPT, resumed)
        self.assertNotIn('"$(<', resumed)

    def test_pi_uses_one_flag_for_both(self):
        fresh = self.command("pi", SessionPlan("u-2", resume=False))
        resumed = self.command("pi", SessionPlan("u-2", resume=True))
        self.assertIn("--session-id u-2 ", fresh)
        self.assertIn("--session-id u-2 ", resumed)
        self.assertIn(RESUME_PROMPT, resumed)

    def test_hermes_creates_with_its_route_and_resumes_the_worktree_session(self):
        fresh = self.command("hermes", SessionPlan("swarmforge-coder-x", resume=False))
        self.assertIn("--query-file ", fresh)
        # A named session created with --create-if-missing freezes the profile
        # defaults and then refuses this run's route: hermes owns the identity.
        self.assertNotIn("--continue", fresh)
        self.assertNotIn("--create-if-missing", fresh)
        resumed = self.command("hermes", SessionPlan("swarmforge-coder-x", resume=True))
        self.assertIn("--resume latest ", resumed)
        self.assertIn("-q ", resumed)
        self.assertIn(RESUME_PROMPT, resumed)
        self.assertNotIn("--query-file", resumed)

    def test_hermes_resume_does_not_repeat_the_route(self):
        row = RoleRow(role="coder", agent="hermes", session="swarmforge-coder", display_name="Coder",
                      worktree_name="master", worktree_path=self.root, receive_mode="task",
                      extra_args="--provider opencode-go --model minimax-m3 --reasoning medium --yolo",
                      workspace_id="w2", pane_id="w2:p1")
        fresh = build_launch_command(self.ctx, 1, row, SessionPlan("swarmforge-coder-x", resume=False))
        self.assertIn("--provider opencode-go --model minimax-m3", fresh)
        # The route is frozen when the session is created: repeating it is
        # allowed, changing it is refused, so the resume leaves it out.
        resumed = build_launch_command(self.ctx, 1, row, SessionPlan("swarmforge-coder-x", resume=True))
        self.assertNotIn("--model", resumed)
        self.assertNotIn("--provider", resumed)

    def test_project_root_is_last_on_path_so_agents_can_run_swarm(self):
        cmd = self.command("claude", SessionPlan("u-1", resume=False))
        self.assertIn(f":$PATH:{shell_quote(str(self.root))} &&", cmd)

    def test_prompt_is_read_with_bash_builtin_not_cat(self):
        for agent in ["claude", "codex", "copilot", "grok", "opencode", "kiro-cli"]:
            cmd = self.command(agent, SessionPlan("u-1", resume=False))
            self.assertNotIn("$(cat", cmd, agent)
            self.assertIn('"$(<', cmd, agent)

    def test_fresh_launch_fails_loudly_on_empty_prompt(self):
        fresh = self.command("codex", SessionPlan("u-1", resume=False))
        self.assertIn("if [ ! -s ", fresh)
        self.assertIn("exit 1", fresh)
        resumed = self.command("codex", SessionPlan("u-1", resume=True))
        self.assertNotIn("if [ ! -s ", resumed)

    def test_no_plan_keeps_the_old_command(self):
        cmd = self.command("claude", None)
        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--resume", cmd)


class DeliveryTest(unittest.TestCase):
    def test_only_running_recipients_get_a_wake_up(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            roles = {
                name: {"worktree-path": str(root / name), "pane-id": f"{name}:p1"}
                for name in ("specifier", "coder", "reviewer")
            }
            outbox = root / "specifier" / ".swarmforge" / "handoffs" / "outbox"
            outbox.mkdir(parents=True)
            msg = outbox / "m.handoff"
            msg.write_text("to: coder,reviewer\ntask: login\n\nbody\n", encoding="utf-8")
            woken, sent = [], []
            original = handoffd.notify
            handoffd.notify = lambda session, pane: woken.append(pane)
            try:
                handoffd.deliver(
                    roles, "s", "specifier", msg, root / "log", root / "daemon",
                    is_running=lambda role: role == "reviewer",
                    on_sent=lambda sender, task: sent.append((sender, task)),
                )
            finally:
                handoffd.notify = original
            self.assertEqual(woken, ["reviewer:p1"])
            self.assertEqual(sent, [("specifier", "login")])
            self.assertTrue((root / "coder/.swarmforge/handoffs/inbox/new/m.handoff").exists())


if __name__ == "__main__":
    unittest.main()
