"""Unit tests for on-demand role agents: session planning, launch commands,
the scheduler's launch/park rules, and handoff delivery hooks.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python import agent_state, scheduler as sched  # noqa: E402
from swarm_python.config import RoleRow  # noqa: E402
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
        self.answer = True

    def pane_statuses(self):
        return dict(self.status)

    def foreground(self, pane_id):
        if not self.answer:
            return None, None
        return (200, 100) if self.agent_up.get(pane_id) else (100, 100)

    def stop(self, pane_id):
        self.stopped.append(pane_id)
        self.agent_up[pane_id] = False


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
        self.s = sched.Scheduler(self.rows, self.herdr, self._launch, lambda *parts: self.logs.append(parts), self.clock)
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

    def test_no_answer_from_herdr_changes_nothing(self):
        self.s.tick(self.state)
        self.herdr.answer = False
        self.clock.now += 1000
        self.s.tick(self.state)
        self.assertEqual(self.state["specifier"]["status"], "running")
        self.assertEqual(len(self.launches), 1)


class StateFileTest(unittest.TestCase):
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

    def test_claude_creates_then_resumes_by_id(self):
        fresh = self.command("claude", SessionPlan("u-1", resume=False))
        self.assertIn("--session-id u-1 ", fresh)
        self.assertIn('"$(cat ', fresh)
        resumed = self.command("claude", SessionPlan("u-1", resume=True))
        self.assertIn("--resume u-1 ", resumed)
        self.assertIn(RESUME_PROMPT, resumed)
        self.assertNotIn('"$(cat ', resumed)

    def test_pi_uses_one_flag_for_both(self):
        fresh = self.command("pi", SessionPlan("u-2", resume=False))
        resumed = self.command("pi", SessionPlan("u-2", resume=True))
        self.assertIn("--session-id u-2 ", fresh)
        self.assertIn("--session-id u-2 ", resumed)
        self.assertIn(RESUME_PROMPT, resumed)

    def test_hermes_continues_a_named_session(self):
        fresh = self.command("hermes", SessionPlan("swarmforge-coder-x", resume=False))
        self.assertIn("--continue swarmforge-coder-x --create-if-missing ", fresh)
        self.assertIn("--query-file ", fresh)
        resumed = self.command("hermes", SessionPlan("swarmforge-coder-x", resume=True))
        self.assertIn("-q ", resumed)
        self.assertNotIn("--query-file", resumed)

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
