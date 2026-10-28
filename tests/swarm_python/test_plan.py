"""Tests for the feature plan: schema, queries, the specifier -> coder gate
and the human `swarm plan` commands.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python import plan as planlib  # noqa: E402
from swarm_python import plan_cli  # noqa: E402
from swarm_python.entrypoints import swarm_handoff  # noqa: E402
from swarm_python.handoff import inbox  # noqa: E402
from swarm_python.handoff.format import header_field  # noqa: E402
from swarm_python.handoff.validate import validate  # noqa: E402


def feature(fid, depends_on=(), status="pending", **extra):
    f = {
        "id": fid,
        "title": fid.title(),
        "summary": f"Summary of {fid}",
        "depends_on": list(depends_on),
        "allowed_paths": [f"src/{fid}/**"],
        "acceptance": [f"{fid} works"],
        "est_lines": 300,
        "status": status,
    }
    f.update(extra)
    return f


def make_plan(features=None, status="approved"):
    return {
        "id": "mod",
        "title": "Module",
        "status": status,
        "general_file": "general.md",
        "features": features if features is not None else [
            feature("a"), feature("b", ["a"]), feature("c", ["b"]),
        ],
    }


def write_plan(plan_dir, plan, with_files=True, general_lines=5):
    plan_dir = Path(plan_dir)
    (plan_dir / "features").mkdir(parents=True, exist_ok=True)
    if with_files:
        (plan_dir / "general.md").write_text("\n".join(["overview"] * general_lines) + "\n", encoding="utf-8")
        for f in plan["features"]:
            (plan_dir / "features" / f"{f['id']}.md").write_text(f"Details of {f['id']}\n", encoding="utf-8")
    planlib.save(plan_dir, plan)
    if plan.get("status") == "approved":
        planlib.record_approval(plan_dir)


class PlanTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "plan"
        env = mock.patch.dict(os.environ, {"SWARMFORGE_APPROVALS_DIR": str(Path(self._tmp.name) / "approvals")})
        env.start()
        self.addCleanup(env.stop)


class SchemaTests(PlanTestCase):
    def test_valid_plan_has_no_errors_or_warnings(self):
        write_plan(self.dir, make_plan())
        self.assertEqual(planlib.validate(planlib.load(self.dir), self.dir), ([], []))

    def test_save_is_atomic_and_round_trips(self):
        write_plan(self.dir, make_plan())
        self.assertFalse((self.dir / "plan.json.tmp").exists())
        self.assertEqual(planlib.load(self.dir)["features"][0]["id"], "a")

    def test_missing_fields_are_errors(self):
        plan = make_plan([{"id": "a"}])
        write_plan(self.dir, plan)
        errors, _ = planlib.validate(plan, self.dir)
        for field in ("title", "summary", "depends_on", "allowed_paths", "acceptance", "est_lines", "status"):
            self.assertTrue(any(f"missing field '{field}'" in e for e in errors), field)

    def test_duplicate_ids_unknown_dependency_and_bad_status(self):
        plan = make_plan([feature("a"), feature("a"), feature("b", ["ghost"], status="bogus")])
        write_plan(self.dir, plan)
        errors, _ = planlib.validate(plan, self.dir)
        self.assertTrue(any("Duplicate feature id 'a'" in e for e in errors))
        self.assertTrue(any("unknown feature 'ghost'" in e for e in errors))
        self.assertTrue(any("invalid status 'bogus'" in e for e in errors))

    def test_dependency_cycle_is_an_error(self):
        plan = make_plan([feature("a", ["c"]), feature("b", ["a"]), feature("c", ["b"])])
        write_plan(self.dir, plan)
        errors, _ = planlib.validate(plan, self.dir)
        self.assertTrue(any(e.startswith("Dependency cycle") for e in errors))

    def test_empty_acceptance_is_an_error(self):
        plan = make_plan([feature("a", acceptance=[])])
        write_plan(self.dir, plan)
        errors, _ = planlib.validate(plan, self.dir)
        self.assertTrue(any("acceptance" in e for e in errors))

    def test_missing_feature_file_is_an_error(self):
        plan = make_plan()
        write_plan(self.dir, plan)
        (self.dir / "features" / "b.md").unlink()
        errors, _ = planlib.validate(plan, self.dir)
        self.assertTrue(any("missing its file" in e and "b.md" in e for e in errors))

    def test_size_problems_only_warn(self):
        plan = make_plan([feature("a", est_lines=5000)])
        write_plan(self.dir, plan, general_lines=450)
        (self.dir / "features" / "a.md").write_text("x\n" * 450, encoding="utf-8")
        errors, warnings = planlib.validate(plan, self.dir)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 3)

    def test_est_lines_near_the_guide_does_not_warn(self):
        plan = make_plan([feature("a", est_lines=550)])
        write_plan(self.dir, plan)
        self.assertEqual(planlib.validate(plan, self.dir), ([], []))


class QueryTests(PlanTestCase):
    def test_next_ready_feature_follows_dependencies(self):
        plan = make_plan()
        self.assertEqual(planlib.next_ready_feature(plan)["id"], "a")
        plan["features"][0]["status"] = "merged"
        self.assertEqual(planlib.next_ready_feature(plan)["id"], "b")

    def test_next_ready_feature_waits_while_one_is_in_progress(self):
        for busy in planlib.IN_PROGRESS:
            plan = make_plan([feature("a", status=busy), feature("b")])
            self.assertIsNone(planlib.next_ready_feature(plan), busy)

    def test_next_ready_feature_none_when_all_merged(self):
        plan = make_plan([feature("a", status="merged")])
        self.assertIsNone(planlib.next_ready_feature(plan))

    def test_slice_for_has_overview_and_only_its_feature(self):
        plan = make_plan()
        write_plan(self.dir, plan)
        text = planlib.slice_for(plan, "b", self.dir)
        self.assertIn("overview", text)
        self.assertIn("Details of b", text)
        self.assertIn("b works", text)
        self.assertIn("src/b/**", text)
        self.assertIn("2 of 3", text)
        self.assertNotIn("Details of a", text)
        self.assertNotIn("Details of c", text)

    def test_coder_context_only_for_coder_and_known_features(self):
        write_plan(self.dir, make_plan())
        self.assertIn("Details of a", planlib.coder_context("a", self.dir, role="coder"))
        self.assertEqual(planlib.coder_context("a", self.dir, role="reviewer"), "")
        self.assertEqual(planlib.coder_context("zzz", self.dir, role="coder"), "")
        self.assertIn("(plan not found at", planlib.coder_context("a", self.dir / "nope", role="coder"))
        self.assertEqual(planlib.coder_context("Not A Slug", self.dir / "nope", role="coder"), "")


@mock.patch("swarm_python.handoff.validate.role_known", return_value=True)
class GateTests(PlanTestCase):
    def handoff(self, task="a"):
        head = subprocess.run(
            ["git", "rev-parse", "--short=10", "HEAD"], capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        ).stdout.strip()
        headers = {"type": "git_handoff", "to": "coder", "priority": "50", "task": task, "commit": head}
        return headers, ["type", "to", "priority", "task", "commit"]

    def errors(self, task="a", sender="specifier"):
        headers, ordered = self.handoff(task)
        cwd = os.getcwd()
        os.chdir(Path(__file__).resolve().parents[2])
        try:
            return validate(headers, ordered, sender, plan_dir=self.dir)["errors"]
        finally:
            os.chdir(cwd)

    def test_no_plan_is_rejected(self, _role_known):
        errors = self.errors()
        self.assertEqual(len(errors), 1)
        self.assertIn("No feature plan found", errors[0])

    def test_draft_plan_is_rejected(self, _role_known):
        write_plan(self.dir, make_plan(status="draft"))
        self.assertTrue(any("not approved" in e for e in self.errors()))

    def test_task_must_be_a_feature_id(self, _role_known):
        write_plan(self.dir, make_plan())
        self.assertTrue(any("must equal a feature id" in e for e in self.errors("login")))

    def test_unmerged_dependency_is_rejected(self, _role_known):
        write_plan(self.dir, make_plan())
        self.assertTrue(any("not merged yet: a" in e for e in self.errors("b")))

    def test_another_feature_in_progress_is_rejected(self, _role_known):
        write_plan(self.dir, make_plan([feature("a", status="review"), feature("b")]))
        self.assertTrue(any("'a' is still in progress" in e for e in self.errors("b")))

    def test_non_pending_feature_is_rejected(self, _role_known):
        write_plan(self.dir, make_plan([feature("a", status="merged")]))
        self.assertTrue(any("not 'pending'" in e for e in self.errors("a")))

    def test_ready_feature_passes(self, _role_known):
        write_plan(self.dir, make_plan())
        self.assertEqual(self.errors("a"), [])

    def test_other_routes_are_not_gated(self, _role_known):
        # No plan exists here, yet coder -> reviewer must still validate.
        headers, ordered = self.handoff("a")
        headers["to"] = "reviewer"
        self.assertEqual(validate(headers, ordered, "coder", plan_dir=self.dir)["errors"], [])


class CliTests(PlanTestCase):
    def run_cli(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = plan_cli.main(argv, plan_dir=self.dir, env=env or {})
        return code, out.getvalue(), err.getvalue()

    def test_approve_sets_status_and_timestamp(self):
        write_plan(self.dir, make_plan(status="draft"))
        code, _, _ = self.run_cli(["approve"])
        self.assertEqual(code, 0)
        plan = planlib.load(self.dir)
        self.assertEqual(plan["status"], "approved")
        self.assertTrue(plan["approved_at"])

    def test_approve_refuses_invalid_plan(self):
        write_plan(self.dir, make_plan([feature("a", acceptance=[])], status="draft"))
        code, _, _ = self.run_cli(["approve"])
        self.assertEqual(code, 2)
        self.assertEqual(planlib.load(self.dir)["status"], "draft")

    def test_approve_refuses_inside_an_agent(self):
        write_plan(self.dir, make_plan(status="draft"))
        code, _, err = self.run_cli(["approve"], env={"SWARMFORGE_ROLE": "specifier"})
        self.assertEqual(code, 3)
        self.assertIn("Solo una persona", err)
        self.assertEqual(planlib.load(self.dir)["status"], "draft")

    def test_approve_refuses_inside_an_agent_via_real_cli(self):
        write_plan(self.dir, make_plan(status="draft"))
        cli = SCRIPTS / "swarm_python" / "entrypoints" / "swarm_cli.py"
        env = dict(os.environ, SWARMFORGE_ROLE="specifier", SWARMFORGE_PLAN_DIR=str(self.dir))
        r = subprocess.run([sys.executable, str(cli), "plan", "approve"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(planlib.load(self.dir)["status"], "draft")

    def test_set_status_updates_and_rejects_bad_input(self):
        write_plan(self.dir, make_plan())
        self.assertEqual(self.run_cli(["set-status", "a", "merged"])[0], 1)
        self.assertEqual(self.run_cli(["set-status", "a", "coding"])[0], 0)
        self.assertEqual(self.run_cli(["set-status", "a", "merged", "--force"])[0], 0)
        self.assertEqual(planlib.load(self.dir)["features"][0]["status"], "merged")
        self.assertEqual(self.run_cli(["set-status", "a", "nope"])[0], 1)
        self.assertEqual(self.run_cli(["set-status", "zzz", "merged"])[0], 1)
        self.assertEqual(self.run_cli(["set-status", "b", "merged"], env={"SWARMFORGE_ROLE": "specifier"})[0], 3)

    def test_validate_show_status(self):
        write_plan(self.dir, make_plan())
        code, out, _ = self.run_cli(["validate"])
        self.assertEqual((code, "PLAN OK" in out), (0, True))
        self.assertEqual(self.run_cli(["show"])[0], 0)
        code, out, _ = self.run_cli(["status"])
        self.assertIn("a", out.splitlines()[-1])

    def test_validate_reports_errors_with_nonzero_exit(self):
        write_plan(self.dir, make_plan([feature("a", ["a"])]))
        code, _, err = self.run_cli(["validate"])
        self.assertEqual(code, 2)
        self.assertIn("depends on itself", err)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def init_repo(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "f.txt")
    git(root, "commit", "-q", "-m", "init")
    return git(root, "rev-parse", "--short=10", "HEAD")


class MalformedPlanTests(PlanTestCase):
    def test_malformed_depends_on_is_an_error_not_a_traceback(self):
        for bad in (None, "a", [["a"]], [1], {"x": 1}, [None]):
            with self.subTest(bad=bad):
                plan = make_plan([feature("a"), feature("b", depends_on=[])])
                plan["features"][1]["depends_on"] = bad
                write_plan(self.dir, plan)
                errors, _ = planlib.validate(planlib.load(self.dir), self.dir)
                self.assertTrue(any("'depends_on' must be a list of strings" in e for e in errors), errors)

    def test_feature_without_id_does_not_crash_queries(self):
        plan = make_plan([{"title": "x"}, feature("a")])
        write_plan(self.dir, plan, with_files=False)
        errors, _ = planlib.validate(plan, self.dir)
        self.assertTrue(errors)
        planlib.in_progress_features(plan)
        self.assertEqual(planlib.check_specifier_to_coder(self.dir, "a")[0], "The approved plan is now invalid:")


@mock.patch("swarm_python.handoff.validate.role_known", return_value=True)
class ApprovalTests(PlanTestCase):
    def setUp(self):
        super().setUp()
        write_plan(self.dir, make_plan())

    def gate(self, task="a"):
        return planlib.check_specifier_to_coder(self.dir, task)

    def test_unchanged_approved_plan_passes(self, _r):
        self.assertEqual(self.gate(), [])

    def test_feature_markdown_change_is_rejected(self, _r):
        (self.dir / "features" / "a.md").write_text("sneaky edit\n", encoding="utf-8")
        self.assertIn("plan content changed since approval", self.gate()[0])

    def test_general_markdown_change_is_rejected(self, _r):
        (self.dir / "general.md").write_text("different\n", encoding="utf-8")
        self.assertIn("plan content changed since approval", self.gate()[0])

    def test_plan_content_change_is_rejected(self, _r):
        plan = planlib.load(self.dir)
        plan["features"][0]["allowed_paths"] = ["**"]
        planlib.save(self.dir, plan)
        self.assertIn("plan content changed since approval", self.gate()[0])

    def test_status_only_changes_do_not_invalidate(self, _r):
        planlib.set_feature_status(self.dir, "c", "merged", force=True)
        self.assertEqual(self.gate(), [])

    def test_reapproving_records_the_new_content(self, _r):
        (self.dir / "features" / "a.md").write_text("revised\n", encoding="utf-8")
        self.assertTrue(self.gate())
        code = plan_cli.main(["approve"], plan_dir=self.dir, env={})
        self.assertEqual(code, 0)
        self.assertEqual(self.gate(), [])

    def test_missing_approval_record_is_rejected(self, _r):
        planlib.approval_file(self.dir).unlink()
        self.assertIn("approve", self.gate()[0])

    def test_approved_plan_edited_into_invalid_state_lists_errors(self, _r):
        plan = planlib.load(self.dir)
        plan["features"][1]["depends_on"] = None
        planlib.save(self.dir, plan)
        errors = self.gate()
        self.assertEqual(errors[0], "The approved plan is now invalid:")
        self.assertTrue(any("depends_on" in e for e in errors))


class SaveAndLockTests(PlanTestCase):
    def setUp(self):
        super().setUp()
        for name, value in (("SAVE_RETRY_DELAY", 0), ("LOCK_POLL", 0.01)):
            p = mock.patch.object(planlib, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_save_retries_a_locked_file(self):
        write_plan(self.dir, make_plan(status="draft"))
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("locked")
            return real(src, dst)

        plan = planlib.load(self.dir)
        plan["title"] = "New"
        with mock.patch.object(planlib.os, "replace", flaky):
            planlib.save(self.dir, plan)
        self.assertEqual(len(calls), 3)
        self.assertEqual(planlib.load(self.dir)["title"], "New")
        self.assertEqual([p.name for p in self.dir.glob("*.tmp")], [])

    def test_save_failure_is_a_plan_error_and_leaves_no_tmp(self):
        write_plan(self.dir, make_plan(status="draft"))
        with mock.patch.object(planlib.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(planlib.PlanError):
                planlib.save(self.dir, planlib.load(self.dir))
        self.assertEqual([p.name for p in self.dir.glob("*.tmp")], [])

    def test_lock_is_exclusive_and_released(self):
        self.dir.mkdir(parents=True)
        with mock.patch.object(planlib, "LOCK_WAIT_SECONDS", 0.2):
            with planlib.plan_lock(self.dir):
                self.assertTrue((self.dir / "plan.lock").exists())
                with self.assertRaises(planlib.PlanError):
                    with planlib.plan_lock(self.dir):
                        pass
            self.assertFalse((self.dir / "plan.lock").exists())
            with planlib.plan_lock(self.dir):
                pass

    def test_stale_lock_is_taken_over(self):
        self.dir.mkdir(parents=True)
        lock = self.dir / "plan.lock"
        lock.write_text("999", encoding="utf-8")
        old = lock.stat().st_mtime - planlib.LOCK_STALE_SECONDS - 5
        os.utime(lock, (old, old))
        with planlib.plan_lock(self.dir):
            pass
        self.assertFalse(lock.exists())


class LocateAndContextTests(PlanTestCase):
    def test_worktree_resolves_to_the_master_plan(self):
        root = Path(self._tmp.name) / "repo"
        init_repo(root)
        write_plan(root / ".swarmforge" / "plan", make_plan(), with_files=True)
        wt = Path(self._tmp.name) / "wt"
        git(root, "worktree", "add", "-q", str(wt), "-b", "side")
        expected = (root / ".swarmforge" / "plan").resolve()
        with mock.patch.dict(os.environ):
            os.environ.pop("SWARMFORGE_PLAN_DIR", None)
            self.assertEqual(planlib.locate_plan_dir(wt).resolve(), expected)
            # Same answer through the `git worktree list` fallback.
            real_run = subprocess.run

            def no_common_dir(cmd, *a, **k):
                if "--git-common-dir" in cmd:
                    return subprocess.CompletedProcess(cmd, 1, "", "")
                return real_run(cmd, *a, **k)

            with mock.patch.object(planlib.subprocess, "run", no_common_dir):
                self.assertEqual(planlib.locate_plan_dir(wt).resolve(), expected)

    def test_print_task_notes_a_missing_plan(self):
        draft = Path(self._tmp.name) / "t.handoff"
        draft.write_text("from: specifier\ntype: git_handoff\ntask: feat-a\n\nbody\n", encoding="utf-8")
        missing = Path(self._tmp.name) / "nowhere"
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"SWARMFORGE_ROLE": "coder", "SWARMFORGE_PLAN_DIR": str(missing)}):
            with contextlib.redirect_stdout(out):
                inbox.print_task(draft)
        text = out.getvalue()
        self.assertIn("PLAN_CONTEXT:\n(plan not found at", text)
        self.assertIn("PAYLOAD:", text)

    def test_crlf_task_header(self):
        draft = Path(self._tmp.name) / "crlf.handoff"
        draft.write_bytes(b"from: specifier\r\ntask: feat-a  \r\ntype: git_handoff\r\n\r\nbody\r\n")
        self.assertEqual(header_field(draft, "task"), "feat-a")


@mock.patch("swarm_python.entrypoints.swarm_handoff.role_known", return_value=True)
@mock.patch("swarm_python.handoff.validate.role_known", return_value=True)
class HandoffMainTests(PlanTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "proj"
        self.head = init_repo(self.root)
        self.dir = self.root / ".swarmforge" / "plan"
        write_plan(self.dir, make_plan())
        self.draft = self.root / "draft.txt"
        self.write_draft()

    def write_draft(self):
        self.draft.write_text(
            f"type: git_handoff\nto: coder\npriority: 50\ntask: a\ncommit: {self.head}\n",
            encoding="utf-8",
        )

    def run_main(self):
        env = {"SWARMFORGE_ROLE": "specifier", "SWARMFORGE_PLAN_DIR": str(self.dir)}
        cwd = os.getcwd()
        out, err = io.StringIO(), io.StringIO()
        os.chdir(self.root)
        try:
            with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["swarm_handoff", str(self.draft)]):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        swarm_handoff.main()
                        code = 0
                    except SystemExit as e:
                        code = e.code
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def statuses(self):
        return {f["id"]: f["status"] for f in planlib.load(self.dir)["features"]}

    def test_main_marks_the_feature_coding_and_queues(self, _a, _b):
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.statuses(), {"a": "coding", "b": "pending", "c": "pending"})
        self.assertIn("HANDOFF QUEUED", out)
        self.assertFalse(self.draft.exists())
        self.assertEqual(len(list((self.root / ".swarmforge" / "handoffs" / "outbox").glob("*.handoff"))), 1)

    def test_second_handoff_of_the_same_feature_is_rejected(self, _a, _b):
        self.assertEqual(self.run_main()[0], 0)
        self.write_draft()
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("HANDOFF INVALID", err)

    def test_outbox_failure_rolls_the_feature_back_and_keeps_the_draft(self, _a, _b):
        with mock.patch.object(swarm_handoff, "write_handoff", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_main()
        self.assertEqual(self.statuses()["a"], "pending")
        self.assertTrue(self.draft.exists())

    def test_unwritable_plan_is_reported_loudly_but_queues(self, _a, _b):
        real = os.replace

        def deny_plan(src, dst):
            if str(dst).endswith("plan.json"):
                raise PermissionError("locked by TUI")
            return real(src, dst)

        with mock.patch.object(planlib, "SAVE_RETRY_DELAY", 0), \
                mock.patch.object(planlib.os, "replace", deny_plan):
            code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("HANDOFF QUEUED", out)
        self.assertIn("swarm plan set-status a coding", err)
        self.assertEqual(self.statuses()["a"], "pending")


@mock.patch("swarm_python.entrypoints.swarm_handoff.role_known", return_value=True)
@mock.patch("swarm_python.handoff.validate.role_known", return_value=True)
class LifecycleTests(PlanTestCase):
    """Status lifecycle, reviewer verdict routing, round cap, advisory metrics."""

    def setUp(self):
        super().setUp()
        self.root = Path(self._tmp.name) / "proj"
        self.head = init_repo(self.root)
        self.dir = self.root / ".swarmforge" / "plan"
        write_plan(self.dir, make_plan())
        self.draft = self.root / "draft.txt"

    def send(self, role, to, task="a", verdict=None, commit=None, type_="git_handoff", message=None):
        lines = [f"type: {type_}", f"to: {to}", "priority: 50"]
        if type_ == "git_handoff":
            lines += [f"task: {task}", f"commit: {commit or self.head}"]
        else:
            lines.append(f"message: {message or 'hello'}")
        if verdict:
            lines.append(f"verdict: {verdict}")
        self.draft.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env = {"SWARMFORGE_ROLE": role, "SWARMFORGE_PLAN_DIR": str(self.dir)}
        cwd = os.getcwd()
        out, err = io.StringIO(), io.StringIO()
        os.chdir(self.root)
        try:
            with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["swarm_handoff", str(self.draft)]):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        swarm_handoff.main()
                        code = 0
                    except SystemExit as e:
                        code = e.code
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def feat(self, fid="a"):
        return planlib.feature_by_id(planlib.load(self.dir), fid)

    def force(self, status, fid="a", **fields):
        plan = planlib.load(self.dir)
        f = planlib.feature_by_id(plan, fid)
        f["status"] = status
        f.update(fields)
        planlib.save(self.dir, plan)

    def outbox(self):
        return sorted((self.root / ".swarmforge" / "handoffs" / "outbox").glob("*.handoff"))

    # --- verdict -------------------------------------------------------------

    def test_reviewer_verdict_is_required(self, _a, _b):
        self.force("review")
        code, _, err = self.send("reviewer", "architect")
        self.assertEqual(code, 2)
        self.assertIn("Missing required header 'verdict'", err)
        self.assertEqual(self.feat()["status"], "review")

    def test_verdict_must_be_pass_or_fail(self, _a, _b):
        self.force("review")
        code, _, err = self.send("reviewer", "architect", verdict="maybe")
        self.assertEqual(code, 2)
        self.assertIn("must be pass or fail", err)

    def test_verdict_pass_goes_only_to_the_architect(self, _a, _b):
        self.force("review")
        code, _, err = self.send("reviewer", "coder", verdict="pass")
        self.assertEqual(code, 2)
        self.assertIn("verdict 'pass' must be sent to the architect only", err)
        code, _, err = self.send("reviewer", "coder,architect", verdict="pass")
        self.assertEqual(code, 2)
        self.assertEqual(self.feat()["status"], "review")
        self.assertEqual(self.send("reviewer", "architect", verdict="pass")[0], 0)
        self.assertEqual(self.feat()["status"], "arch")

    def test_verdict_fail_goes_only_to_the_coder(self, _a, _b):
        self.force("review")
        code, _, err = self.send("reviewer", "architect", verdict="fail")
        self.assertEqual(code, 2)
        self.assertIn("verdict 'fail' must be sent to the coder only", err)
        self.assertEqual(self.send("reviewer", "coder", verdict="fail")[0], 0)
        self.assertEqual(self.feat()["status"], "coding")

    def test_verdict_from_another_role_is_rejected(self, _a, _b):
        self.force("coding")
        code, _, err = self.send("coder", "reviewer", verdict="pass")
        self.assertEqual(code, 2)
        self.assertIn("only allowed on a git_handoff sent by the reviewer", err)

    def test_verdict_is_written_to_the_delivered_handoff(self, _a, _b):
        self.force("review")
        self.send("reviewer", "architect", verdict="pass")
        self.assertEqual(header_field(self.outbox()[0], "verdict"), "pass")

    # --- lifecycle -------------------------------------------------------------

    def test_full_cycle_moves_the_status_and_counts_rounds(self, _a, _b):
        self.assertEqual(self.send("specifier", "coder")[0], 0)
        self.assertEqual(self.feat()["status"], "coding")
        self.assertEqual(len(self.feat()["base_commit"]), 40)
        self.assertEqual(self.send("coder", "reviewer")[0], 0)
        self.assertEqual(self.feat()["status"], "review")
        self.assertEqual(self.send("reviewer", "coder", verdict="fail")[0], 0)
        self.assertEqual((self.feat()["status"], self.feat()["review_rounds"]), ("coding", 1))
        self.assertEqual(self.send("coder", "reviewer")[0], 0)
        self.assertEqual(self.send("reviewer", "architect", verdict="pass")[0], 0)
        self.assertEqual((self.feat()["status"], self.feat()["review_rounds"]), ("arch", 1))
        self.assertEqual(self.send("architect", "coder")[0], 0)
        self.assertEqual(self.feat()["status"], "coding")
        self.assertEqual(self.send("coder", "reviewer")[0], 0)
        self.assertEqual(self.send("reviewer", "architect", verdict="pass")[0], 0)
        self.assertEqual(self.send("architect", "specifier")[0], 0)
        self.assertEqual(self.feat()["status"], "awaiting_merge")
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertEqual(self.feat("c")["status"], "pending")

    def test_runtime_fields_do_not_invalidate_the_approval(self, _a, _b):
        self.send("specifier", "coder")
        self.send("coder", "reviewer")
        self.send("reviewer", "coder", verdict="fail")
        self.assertIn("review_rounds", self.feat())
        self.assertEqual(planlib.approval_errors(self.dir), [])

    def test_architect_cannot_forward_to_two_roles(self, _a, _b):
        self.force("arch")
        code, _, err = self.send("architect", "coder,specifier")
        self.assertEqual(code, 2)
        self.assertIn("must forward to exactly one of", err)

    def test_outbox_failure_rolls_each_transition_back(self, _a, _b):
        cases = [
            ("coder", "reviewer", None, "coding", {}),
            ("reviewer", "coder", "fail", "review", {"review_rounds": 1}),
            ("reviewer", "architect", "pass", "review", {"review_rounds": 1}),
            ("architect", "specifier", None, "arch", {}),
            ("architect", "coder", None, "arch", {}),
        ]
        for role, to, verdict, status, extra in cases:
            with self.subTest(role=role, to=to):
                self.force(status, **extra)
                before = self.feat()
                with mock.patch.object(swarm_handoff, "write_handoff", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        self.send(role, to, verdict=verdict)
                self.assertEqual(self.feat(), before)
                self.assertTrue(self.draft.exists())

    def test_wrong_current_status_is_left_alone_with_a_note(self, _a, _b):
        self.force("arch")
        code, _, err = self.send("coder", "reviewer")
        self.assertEqual(code, 0)
        self.assertEqual(self.feat()["status"], "arch")
        self.assertIn("status left unchanged", err)

    def test_legacy_task_is_not_touched_or_rejected(self, _a, _b):
        before = planlib.load(self.dir)
        self.assertEqual(self.send("coder", "reviewer", task="legacy-x")[0], 0)
        self.assertEqual(self.send("reviewer", "architect", task="legacy-x", verdict="pass")[0], 0)
        self.assertEqual(self.send("reviewer", "coder", task="legacy-x", verdict="fail")[0], 0)
        self.assertEqual(self.send("architect", "specifier", task="legacy-x")[0], 0)
        self.assertEqual(planlib.load(self.dir), before)

    def test_no_plan_at_all_is_not_gated(self, _a, _b):
        (self.dir / "plan.json").unlink()
        self.assertEqual(self.send("coder", "reviewer", task="whatever")[0], 0)

    # --- review round cap ------------------------------------------------------

    def test_cap_must_be_a_positive_integer(self, _a, _b):
        for bad in (0, -1, True, "3", 1.5):
            plan = make_plan()
            plan["max_review_rounds"] = bad
            errors, _ = planlib.validate(plan, self.dir)
            self.assertTrue(any("max_review_rounds" in e for e in errors), bad)
        plan = make_plan()
        plan["max_review_rounds"] = 2
        self.assertEqual(planlib.validate(plan, self.dir)[0], [])
        self.assertEqual(planlib.max_review_rounds({}), 3)
        self.assertEqual(planlib.max_review_rounds(plan), 2)

    def test_default_cap_bounces_until_three_rounds(self, _a, _b):
        self.force("review", review_rounds=2)
        self.assertEqual(self.send("reviewer", "coder", verdict="fail")[0], 0)
        self.assertEqual(self.feat()["review_rounds"], 3)
        self.force("review", review_rounds=3)
        code, _, err = self.send("reviewer", "coder", verdict="fail")
        self.assertEqual(code, 2)
        self.assertIn("escalated", err)

    def test_cap_reached_escalates_to_the_architect(self, _a, _b):
        plan = planlib.load(self.dir)
        plan["max_review_rounds"] = 2
        planlib.save(self.dir, plan)
        self.force("review", review_rounds=2)
        code, _, err = self.send("reviewer", "architect", verdict="fail")
        self.assertEqual(code, 0, err)
        f = self.feat()
        self.assertEqual((f["status"], f["review_rounds"], f["escalated"]), ("arch", 2, True))
        self.assertIn("ESCALATION", self.outbox()[0].read_text(encoding="utf-8"))

    def test_cap_escalation_rolls_back_on_outbox_failure(self, _a, _b):
        self.force("review", review_rounds=3)
        with mock.patch.object(swarm_handoff, "write_handoff", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.send("reviewer", "architect", verdict="fail")
        f = self.feat()
        self.assertEqual((f["status"], "escalated" in f), ("review", False))

    # --- human set-status ------------------------------------------------------

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = plan_cli.main(argv, plan_dir=self.dir, env={})
        return code, err.getvalue()

    def test_set_status_rejects_illegal_jumps_unless_forced(self, _a, _b):
        code, err = self.run_cli(["set-status", "a", "merged"])
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        self.assertEqual(self.feat()["status"], "pending")
        self.assertEqual(self.run_cli(["set-status", "a", "merged", "--force"])[0], 0)
        self.assertEqual(self.feat()["status"], "merged")

    def test_merged_is_reachable_only_from_awaiting_merge(self, _a, _b):
        for status in ("pending", "coding", "review", "arch"):
            self.force(status)
            self.assertEqual(self.run_cli(["set-status", "a", "merged"])[0], 1, status)
        self.force("awaiting_merge")
        self.assertEqual(self.run_cli(["set-status", "a", "merged"])[0], 0)

    def test_two_features_cannot_be_in_progress(self, _a, _b):
        self.force("coding", "a")
        code, err = self.run_cli(["set-status", "b", "coding"])
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertEqual(self.run_cli(["set-status", "b", "coding", "--force"])[0], 0)

    # --- note bypass -----------------------------------------------------------

    def test_specifier_note_to_coder_needs_a_feature_in_progress(self, _a, _b):
        code, _, err = self.send("specifier", "coder", type_="note")
        self.assertEqual(code, 2)
        self.assertIn("only accepted while a plan feature is in progress", err)
        self.assertEqual(self.outbox(), [])
        for status in ("coding", "review", "arch"):
            self.force(status)
            self.assertEqual(self.send("specifier", "coder", type_="note")[0], 0, status)
        self.force("awaiting_merge")
        self.assertEqual(self.send("specifier", "coder", type_="note")[0], 2)

    def test_note_without_a_plan_is_still_accepted(self, _a, _b):
        (self.dir / "plan.json").unlink()
        self.assertEqual(self.send("specifier", "coder", type_="note")[0], 0)

    # --- advisory metrics --------------------------------------------------------

    def commit_files(self, files):
        for rel, n in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("line\n" * n, encoding="utf-8")
            git(self.root, "add", rel)
        git(self.root, "commit", "-q", "-m", "work")
        return git(self.root, "rev-parse", "--short=10", "HEAD")

    def test_metrics_count_prod_lines_and_flag_files_outside_allowed(self, _a, _b):
        self.send("specifier", "coder")
        commit = self.commit_files({
            "src/a/x.py": 10, "src/other/y.py": 5, "tests/test_x.py": 100,
            "package-lock.json": 50, "docs/g.md": 20, "src/a/x_test.py": 7,
        })
        code, _, err = self.send("coder", "reviewer", commit=commit)
        self.assertEqual(code, 0, err)
        m = self.feat()["metrics"]
        self.assertEqual((m["prod_lines"], m["files_total"]), (15, 6))
        self.assertEqual(m["outside_allowed"], ["src/other/y.py"])
        self.assertIn("Plan check: ~15 prod lines", err)
        self.assertIn("src/other/y.py", err)
        self.assertIn("Plan check:", self.outbox()[-1].read_text(encoding="utf-8"))

    def test_oversize_only_warns(self, _a, _b):
        self.send("specifier", "coder")
        commit = self.commit_files({"src/a/big.py": 900})
        code, _, err = self.send("coder", "reviewer", commit=commit)
        self.assertEqual(code, 0)
        self.assertIn("over target", err)
        self.assertEqual(self.feat()["status"], "review")

    def test_git_failure_skips_metrics_silently(self, _a, _b):
        self.send("specifier", "coder")
        with mock.patch.object(planlib, "compute_metrics", return_value=None):
            code, _, err = self.send("coder", "reviewer")
        self.assertEqual(code, 0)
        self.assertIn("Plan check skipped", err)
        self.assertEqual(self.feat()["status"], "review")
        self.assertNotIn("metrics", self.feat())

    def test_missing_base_commit_skips_metrics(self, _a, _b):
        self.force("coding")
        code, _, err = self.send("coder", "reviewer")
        self.assertEqual(code, 0)
        self.assertIn("no base commit", err)

    def test_escalation_text_leads_the_body(self, _a, _b):
        self.force("review", review_rounds=3)
        with mock.patch.object(swarm_handoff.planlib, "locate_plan_dir", return_value=self.dir):
            _, body = swarm_handoff.apply_feature_transition(
                "reviewer", {"type": "git_handoff", "task": "a", "verdict": "fail"},
                ["architect"], self.head, "Reviewer findings here",
            )
        self.assertTrue(body.startswith("ESCALATION"), body)
        self.assertTrue(body.endswith("Reviewer findings here"))

    def test_reserve_feature_resets_stale_runtime_state(self, _a, _b):
        self.force("pending", review_rounds=2, escalated=True, metrics={"prod_lines": 1}, base_commit="old")
        self.assertEqual(self.send("specifier", "coder")[0], 0)
        f = self.feat()
        self.assertEqual((f["status"], f["review_rounds"], f["escalated"]), ("coding", 0, False))
        self.assertNotIn("metrics", f)
        self.assertNotEqual(f["base_commit"], "old")

    def test_reopening_awaiting_merge_resets_but_arch_rework_keeps(self, _a, _b):
        self.force("arch", review_rounds=2, escalated=True)
        self.assertEqual(self.run_cli(["set-status", "a", "coding"])[0], 0)
        f = self.feat()
        self.assertEqual((f["review_rounds"], f["escalated"]), (2, True))
        self.force("awaiting_merge", review_rounds=2, escalated=True, metrics={"prod_lines": 9})
        self.assertEqual(self.run_cli(["set-status", "a", "coding"])[0], 0)
        f = self.feat()
        self.assertEqual((f["status"], f["review_rounds"], f["escalated"]), ("coding", 0, False))
        self.assertNotIn("metrics", f)

    def test_non_numeric_review_rounds_does_not_crash_the_handoff(self, _a, _b):
        for bad in ("two", None, -3, True, 1.5):
            self.force("review", review_rounds=bad)
            self.assertFalse(planlib.review_cap_reached(self.dir, "a"))
            code, _, err = self.send("reviewer", "coder", verdict="fail")
            self.assertEqual(code, 0, (bad, err))
            self.assertEqual(self.feat()["review_rounds"], 1)

    def test_validate_flags_bad_runtime_fields(self, _a, _b):
        plan = make_plan([feature("a", review_rounds="x", base_commit=5, metrics=[1])])
        write_plan(self.dir, plan)
        errors, _ = planlib.validate(plan, self.dir)
        text = " ".join(errors)
        for field in ("review_rounds", "base_commit", "metrics"):
            self.assertIn(field, text)
        plan = make_plan([feature("a", review_rounds=-1)])
        self.assertTrue(any("review_rounds" in e for e in planlib.validate(plan, self.dir)[0]))

    def test_approve_resets_pre_seeded_runtime_fields(self, _a, _b):
        plan = make_plan([feature("a", review_rounds=9, escalated=True, metrics={"x": 1}, base_commit="zzz")], status="draft")
        write_plan(self.dir, plan)
        planlib.approve(self.dir, "2026-01-01T00:00:00Z")
        f = self.feat()
        self.assertEqual((f["review_rounds"], f["escalated"]), (0, False))
        self.assertNotIn("metrics", f)
        self.assertNotIn("base_commit", f)

    def test_hung_git_times_out_and_skips_metrics(self, _a, _b):
        boom = subprocess.TimeoutExpired(["git"], 30)
        with mock.patch.object(planlib.subprocess, "run", side_effect=boom) as run:
            self.assertIsNone(planlib.compute_metrics("a", "b", []))
        self.assertEqual(run.call_args.kwargs.get("timeout"), 30)
        with mock.patch.object(planlib.subprocess, "run", side_effect=OSError("nope")):
            self.assertIsNone(planlib.compute_metrics("a", "b", []))

    def test_metrics_crash_never_blocks_the_coder_handoff_and_clears_stale(self, _a, _b):
        self.send("specifier", "coder")
        self.force("coding", metrics={"prod_lines": 999, "files_total": 1, "outside_allowed": []})
        with mock.patch.object(planlib, "compute_metrics", side_effect=RuntimeError("boom")):
            code, _, err = self.send("coder", "reviewer")
        self.assertEqual(code, 0, err)
        self.assertIn("Plan check skipped", err)
        f = self.feat()
        self.assertEqual(f["status"], "review")
        self.assertNotIn("metrics", f)

    def test_reviewer_note_to_several_recipients_is_accepted(self, _a, _b):
        self.force("review")
        code, _, err = self.send("reviewer", "coder,architect", type_="note")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.feat()["status"], "review")
        self.assertEqual(self.send("reviewer", "coder,architect", verdict="fail")[0], 2)

    def test_glob_and_noise_helpers(self, _a, _b):
        self.assertEqual(planlib.outside_allowed(["src/a/b/c.py", "lib/z.py"], ["src/a/**"]), ["lib/z.py"])
        self.assertEqual(planlib.outside_allowed(["src/a/c.py"], ["src/a"]), [])
        self.assertEqual(planlib.outside_allowed(["x/y.py"], []), [])
        self.assertEqual(planlib.outside_allowed(["a/b/c.py", "c.py"], ["**/c.py"]), [])
        for noise in ("tests/a.py", "pkg/test_a.py", "a_test.go", "a.spec.ts", "yarn.lock", "docs/x.md"):
            self.assertTrue(planlib.is_non_production(noise), noise)
        self.assertFalse(planlib.is_non_production("src/docs/x.md"))
        self.assertFalse(planlib.is_non_production("src/a.py"))


if __name__ == "__main__":
    unittest.main()
