"""Tests for the Windows-found robustness fixes: UTF-8 subprocess decoding, the
resilient handoff daemon, the done_with_current reply guard, handoff validation
messages and the stale-commit check against the base branch.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import ast
import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python import herdr_ops  # noqa: E402
from swarm_python import plan as planlib  # noqa: E402
from swarm_python.entrypoints import done_with_current, handoffd, swarm_cli, swarm_handoff  # noqa: E402
from swarm_python.env import UTF8_ENV, utf8_environ  # noqa: E402
from swarm_python.handoff.validate import validate  # noqa: E402
from test_plan import decisions_md, feature, feature_md, make_plan, write_plan  # noqa: E402


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


# --- 1a/1b: subprocess decoding ------------------------------------------------

# deps._run_probe passes **popen_kwargs (only start_new_session) and sends every
# stream to DEVNULL: there is no text to decode, so no encoding is needed.
STARRED_ALLOWLIST = {"deps.py"}


class SubprocessEncodingGuardTests(unittest.TestCase):
    def test_every_text_mode_subprocess_call_pins_utf8(self):
        """Without encoding=, text=True uses the locale codec (cp1252 on Windows),
        which dies on bytes it cannot map."""
        offenders = []
        scanned = sorted((SCRIPTS / "swarm_python").rglob("*.py")) + sorted(
            (SCRIPTS.parents[1].parent / "swarmforge" / "scripts" / "swarm_python").rglob("*.py"))
        self.assertGreater(len(scanned), 20)
        for path in scanned:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                        and node.func.attr in ("run", "Popen", "check_output", "check_call")):
                    continue
                kw = {k.arg: k.value for k in node.keywords}
                text_mode = any(
                    isinstance(kw.get(name), ast.Constant) and kw[name].value is True
                    for name in ("text", "universal_newlines"))
                if text_mode and "encoding" not in kw:
                    offenders.append(f"{path.parent.name}/{path.name}:{node.lineno}")
                # **kwargs may smuggle in text mode: demand an explicit encoding too,
                # except for the allowlisted binary-only call.
                starred = any(k.arg is None for k in node.keywords)
                if starred and "encoding" not in kw and path.name not in STARRED_ALLOWLIST:
                    offenders.append(f"{path.parent.name}/{path.name}:{node.lineno} (**kwargs)")
        self.assertEqual(offenders, [])


FAKE_HERDR = (
    "import sys\n"
    "sys.stdout.buffer.write(sys.argv[1].encode('latin-1'))\n"
)


class HerdrOutputTests(unittest.TestCase):
    def run_with_output(self, raw: bytes):
        """Run the REAL subprocess.run against a fake `herdr` that prints raw bytes."""
        real_run = subprocess.run
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = Path(tmp.name) / "fake_herdr.py"
        script.write_text(FAKE_HERDR, encoding="utf-8")

        def fake(argv, **kwargs):
            self.assertEqual(argv[0], "herdr")
            return real_run([sys.executable, str(script), raw.decode("latin-1")], **kwargs)

        return mock.patch.object(herdr_ops.subprocess, "run", side_effect=fake)

    def test_undecodable_bytes_do_not_crash_run_json(self):
        # 0x90 has no cp1252 mapping; 0xff and 0xfe are invalid UTF-8.
        with self.run_with_output(b"\x90\xff\xfe not json"):
            payload, result = herdr_ops._run_json("s", "pane", "list")
        self.assertIsNone(payload)
        self.assertIsInstance(result.stdout, str)

    def test_json_with_a_bad_byte_inside_a_value_still_parses(self):
        raw = b'{"result": {"workspaces": [{"label": "a\x90b", "workspace_id": "w1"}]}}'
        with self.run_with_output(raw):
            workspaces = herdr_ops.workspace_list("s")
        self.assertEqual([w["workspace_id"] for w in workspaces], ["w1"])

    def test_none_stdout_is_treated_as_empty(self):
        dead_reader = subprocess.CompletedProcess(["herdr"], 0, stdout=None, stderr=None)
        with mock.patch.object(herdr_ops, "_run", return_value=dead_reader):
            payload, result = herdr_ops._run_json("s", "pane", "list")
            self.assertIsNone(payload)
            self.assertEqual(herdr_ops.pane_statuses("s"), {})
            self.assertEqual(herdr_ops.workspace_list("s"), [])
            self.assertEqual(herdr_ops.pane_foreground("s", "p"), (None, None))

    def test_run_passes_utf8_and_replace(self):
        with mock.patch.object(herdr_ops.subprocess, "run") as run:
            herdr_ops._run("s", "workspace", "list")
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs["encoding"], kwargs["errors"]), ("utf-8", "replace"))


# --- 1c: resilient daemon -------------------------------------------------------

class ErrorThrottleTests(unittest.TestCase):
    def test_first_logged_then_every_nth_repeat_with_a_counter(self):
        t = handoffd.ErrorThrottle(every=3)
        e = ValueError("boom")
        self.assertEqual([t.register("x", e) for _ in range(7)], [
            (True, 1), (False, 2), (True, 3), (False, 4), (False, 5), (True, 6), (False, 7)])

    def test_a_different_error_or_a_clear_restarts_the_count(self):
        t = handoffd.ErrorThrottle(every=100)
        self.assertEqual(t.register("x", ValueError("a")), (True, 1))
        self.assertEqual(t.register("x", ValueError("a")), (False, 2))
        self.assertEqual(t.register("x", ValueError("b")), (True, 1))
        t.clear("x")
        self.assertEqual(t.register("x", ValueError("b")), (True, 1))

    def test_varying_messages_from_the_same_raise_site_coalesce(self):
        t = handoffd.ErrorThrottle(every=100)

        def boom(n):
            raise ValueError(f"job {n} failed")

        results = []
        for n in range(3):
            try:
                boom(n)
            except ValueError as e:
                results.append(t.register("x", e))
        self.assertEqual(results, [(True, 1), (False, 2), (False, 3)])

    def test_the_same_message_from_another_raise_site_is_a_new_error(self):
        t = handoffd.ErrorThrottle(every=100)
        outcomes = []
        for site in (1, 2):
            try:
                if site == 1:
                    raise ValueError("same")
                raise ValueError("same")
            except ValueError as e:
                outcomes.append(t.register("x", e))
        self.assertEqual(outcomes, [(True, 1), (True, 1)])

    def test_labels_are_independent(self):
        t = handoffd.ErrorThrottle(every=100)
        self.assertEqual(t.register("x", ValueError("a")), (True, 1))
        self.assertEqual(t.register("y", ValueError("a")), (True, 1))


class DaemonLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state_dir = self.root / ".swarmforge"
        self.daemon_dir = self.state_dir / "daemon"
        self.daemon_dir.mkdir(parents=True)
        self.stop_file = self.daemon_dir / "stop"
        self.log_file = self.daemon_dir / "handoffd.log"
        (self.state_dir / "roles.tsv").write_text("", encoding="utf-8")
        (self.state_dir / "herdr-session").write_text("s\n", encoding="utf-8")
        handoffd.ERRORS = handoffd.ErrorThrottle()
        self.addCleanup(setattr, handoffd, "ERRORS", handoffd.ErrorThrottle())

    def log_text(self):
        return self.log_file.read_text(encoding="utf-8") if self.log_file.exists() else ""

    def failing_scheduler(self, error):
        scheduler = mock.Mock()
        scheduler.rows = []
        scheduler.tick.side_effect = error
        return scheduler

    def poll(self, scheduler):
        handoffd.poll_once(self.state_dir, self.daemon_dir, self.stop_file, self.log_file, scheduler)

    def test_a_failing_tick_is_logged_with_a_traceback_and_does_not_raise(self):
        scheduler = self.failing_scheduler(AttributeError("'NoneType' object has no attribute 'strip'"))
        self.poll(scheduler)
        text = self.log_text()
        self.assertIn("scheduler-error", text)
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("AttributeError", text)

    def test_the_next_poll_still_ticks_and_a_persistent_error_does_not_flood(self):
        scheduler = self.failing_scheduler(AttributeError("same every time"))
        for _ in range(10):
            self.poll(scheduler)
        self.assertEqual(scheduler.tick.call_count, 10)
        self.assertEqual(self.log_text().count("scheduler-error"), 1)

    def test_recovery_resets_the_throttle(self):
        scheduler = self.failing_scheduler(ValueError("x"))
        self.poll(scheduler)
        scheduler.tick.side_effect = None
        self.poll(scheduler)
        scheduler.tick.side_effect = ValueError("x")
        self.poll(scheduler)
        self.assertEqual(self.log_text().count("scheduler-error"), 2)

    def test_the_main_loop_survives_a_poll_that_raises(self):
        calls = []

        def poll_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("poll exploded")
            self.stop_file.write_text("", encoding="utf-8")

        flow = mock.Mock()
        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.getsignal(sig)
            self.addCleanup(signal.signal, sig, previous[sig])
        with mock.patch.object(handoffd, "poll_once", poll_once), \
                mock.patch.object(handoffd, "build_scheduler", return_value=mock.Mock()), \
                mock.patch.object(handoffd, "PlanFlow", return_value=flow), \
                mock.patch.object(handoffd, "sleep_poll"), \
                mock.patch.object(sys, "argv", ["handoffd.py", str(self.root)]):
            handoffd.main()
        self.assertEqual(len(calls), 2)
        self.assertIn("poll-error", self.log_text())
        self.assertIn("poll exploded", self.log_text())
        self.assertIn("stopped", self.log_text())
        flow.close.assert_called_once()


# --- 1d: UTF-8 environment ------------------------------------------------------

class Utf8EnvironmentTests(unittest.TestCase):
    def test_utf8_environ_sets_both_variables_without_touching_os_environ(self):
        env = utf8_environ({"A": "1", "PYTHONUTF8": "0"})
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["A"], "1")
        self.assertEqual(UTF8_ENV, {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    def test_the_daemon_is_started_with_utf8_in_its_environment(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        daemon_dir = Path(tmp.name)
        ctx = types.SimpleNamespace(
            daemon_dir=daemon_dir, working_dir=daemon_dir, handoff_daemon_log=daemon_dir / "d.log")
        with mock.patch.object(swarm_cli, "sleep_inhibit_prefix", return_value=[]), \
                mock.patch.object(swarm_cli.subprocess, "Popen") as popen, \
                contextlib.redirect_stdout(io.StringIO()):
            swarm_cli.start_handoff_daemon(ctx)
        env = popen.call_args.kwargs["env"]
        self.assertEqual((env["PYTHONUTF8"], env["PYTHONIOENCODING"]), ("1", "utf-8"))


# --- 4: done_with_current ---------------------------------------------------------

class DoneWithCurrentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.handoffs = self.root / ".swarmforge" / "handoffs"
        self.handoffs.mkdir(parents=True)
        (self.root / ".swarmforge" / "roles.tsv").write_text(
            "".join(f"{r}\t{r}\t{self.root}\ts\t{r}\tclaude\ttask\n"
                    for r in ("specifier", "coder", "reviewer", "architect")),
            encoding="utf-8")
        for sub in ("inbox/in_process", "inbox/completed", "outbox", "sent"):
            (self.handoffs / sub).mkdir(parents=True)

    def handoff(self, directory, name, **headers):
        fields = {"id": name, "from": "x", "to": "y", "priority": "50"}
        fields.update(headers)
        text = "".join(f"{k}: {v}\n" for k, v in fields.items()) + "\nbody\n"
        path = self.handoffs / directory / f"{name}.handoff"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def current(self, task="a", type_="git_handoff", dequeued_at="2026-01-01T10:00:00Z", name="in1"):
        extra = {"task": task, "commit": "abc1234567"} if type_ == "git_handoff" else {"message": "hi"}
        return self.handoff("inbox/in_process", name, type=type_, dequeued_at=dequeued_at, **extra)

    def run_done(self, role, *args):
        err = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with mock.patch.dict(os.environ, {"SWARMFORGE_ROLE": role}), \
                    mock.patch.object(done_with_current, "run_ready"), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                try:
                    done_with_current.main(list(args))
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            os.chdir(cwd)
        return code, err.getvalue()

    def completed(self):
        return sorted(p.name for p in (self.handoffs / "inbox" / "completed").glob("*.handoff"))

    def reply(self, where="sent", task="a", created_at="2026-01-01T10:05:00Z", type_="git_handoff"):
        extra = {"task": task, "commit": "abc1234567"} if type_ == "git_handoff" else {"message": "hi"}
        return self.handoff(where, "out1", type=type_, created_at=created_at, **extra)

    def test_refuses_without_a_reply_and_completes_nothing(self):
        current = self.current()
        code, err = self.run_done("reviewer")
        self.assertEqual(code, 2)
        self.assertIn("REPLY_REQUIRED", err)
        self.assertIn("'a'", err)
        self.assertIn("swarm_handoff.sh", err)
        self.assertIn("--no-reply", err)
        self.assertTrue(current.exists())
        self.assertEqual(self.completed(), [])

    def test_a_delivered_reply_lets_it_complete(self):
        self.current()
        self.reply("sent")
        self.assertEqual(self.run_done("reviewer")[0], 0)
        self.assertEqual(self.completed(), ["in1.handoff"])

    def test_a_queued_reply_in_the_outbox_counts(self):
        self.current()
        self.reply("outbox")
        self.assertEqual(self.run_done("coder")[0], 0)

    def test_a_reply_from_an_earlier_round_does_not_count(self):
        self.current(dequeued_at="2026-01-01T10:00:00Z")
        self.reply("sent", created_at="2026-01-01T09:00:00Z")
        code, err = self.run_done("coder")
        self.assertEqual(code, 2)
        self.assertIn("REPLY_REQUIRED", err)

    def test_a_reply_for_another_task_or_a_note_does_not_count(self):
        self.current()
        self.reply("sent", task="other")
        self.handoff("sent", "n1", type="note", message="hi", created_at="2026-01-01T10:06:00Z")
        self.assertEqual(self.run_done("architect")[0], 2)

    def test_no_reply_flag_overrides(self):
        self.current()
        self.assertEqual(self.run_done("reviewer", "--no-reply")[0], 0)
        self.assertEqual(self.completed(), ["in1.handoff"])

    def test_a_note_expects_no_reply(self):
        self.current(type_="note")
        self.assertEqual(self.run_done("coder")[0], 0)

    def test_the_specifier_ends_the_chain_without_a_reply(self):
        self.current()
        self.assertEqual(self.run_done("specifier")[0], 0)

    def test_unknown_arguments_and_help(self):
        self.current()
        code, err = self.run_done("coder", "--bogus")
        self.assertEqual(code, 1)
        self.assertIn("Unknown argument", err)
        self.assertEqual(self.run_done("coder", "--help")[0], 0)
        self.assertEqual(self.completed(), [])

    def test_usage_documents_the_override(self):
        self.assertIn("--no-reply", done_with_current.USAGE)

    def test_batch_mode_requires_a_reply_per_task(self):
        (self.root / ".swarmforge" / "roles.tsv").write_text(
            f"coder\tcoder\t{self.root}\ts\tCoder\tclaude\tbatch\n", encoding="utf-8")
        batch = self.handoffs / "inbox" / "in_process" / "batch_1"
        batch.mkdir()
        for name, task in (("b1", "a"), ("b2", "b")):
            (batch / f"{name}.handoff").write_text(
                f"id: {name}\ntype: git_handoff\ntask: {task}\ncommit: abc1234567\n"
                "dequeued_at: 2026-01-01T10:00:00Z\n\nbody\n", encoding="utf-8")
        self.reply("sent", task="a")
        code, err = self.run_done("coder")
        self.assertEqual(code, 2)
        self.assertIn("'b'", err)
        self.assertNotIn("'a'", err)
        self.assertTrue(batch.exists())
        self.assertEqual(self.run_done("coder", "--no-reply")[0], 0)


# --- 5: handoff validation messages -----------------------------------------------

class CommitMessageTests(unittest.TestCase):
    def errors(self, commit):
        headers = {"type": "git_handoff", "to": "coder", "priority": "50", "task": "a", "commit": commit}
        with mock.patch("swarm_python.handoff.validate.role_known", return_value=True):
            return validate(headers, list(headers), None)["errors"]

    def test_a_short_abbreviation_gets_a_specific_message(self):
        (error,) = self.errors("abc1234")
        self.assertIn("10 characters", error)
        self.assertIn("got 7: abc1234", error)
        self.assertIn("git rev-parse --short=10 HEAD", error)

    def test_ten_non_hex_characters_are_reported_as_such(self):
        (error,) = self.errors("zzzzzzzzzz")
        self.assertIn("hexadecimal", error)
        self.assertIn("zzzzzzzzzz", error)

    def test_error_report_leads_with_the_reason_before_the_usage_text(self):
        from swarm_python.handoff.validate import error_report
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            error_report("draft.txt", ["commit must be 10 characters, got 7: abc1234"])
        text = err.getvalue()
        self.assertLess(text.index("Reason: commit must be 10"), text.index("Usage:"))


class BoundedGitTests(unittest.TestCase):
    def test_a_hung_command_is_killed_and_returns_none(self):
        import time
        start = time.monotonic()
        result = planlib._run_git_bounded(
            [sys.executable, "-c", "import time; time.sleep(60)"], Path.cwd(), 1)
        self.assertIsNone(result)
        self.assertLess(time.monotonic() - start, 20)

    def test_a_child_holding_the_pipes_does_not_block_forever(self):
        import time
        code = "\n".join([
            "import subprocess, sys, time",
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
            "time.sleep(60)",
        ])
        start = time.monotonic()
        self.assertIsNone(planlib._run_git_bounded([sys.executable, "-c", code], Path.cwd(), 1))
        self.assertLess(time.monotonic() - start, 20)

    def test_normal_output_is_returned(self):
        result = planlib._run_git_bounded([sys.executable, "-c", "print('hi')"], Path.cwd(), 20)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "hi"))


# --- 7c: stale commit check ---------------------------------------------------------

class BaseRepoCase(unittest.TestCase):
    """A project with a real bare `origin`, a plan and an approved first feature."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        patch = mock.patch.dict(os.environ, {"SWARMFORGE_APPROVALS_DIR": str(tmp / "approvals")})
        patch.start()
        self.addCleanup(patch.stop)
        # patch.dict (started above) restores os.environ on cleanup, popped keys included.
        os.environ.pop(planlib.SKIP_BASE_CHECK_ENV, None)
        self.origin = tmp / "origin.git"
        git(tmp, "init", "-q", "--bare", "-b", "master", str(self.origin))
        self.root = tmp / "proj"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "master")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        self.commit_file("f.txt", "x\n", "init")
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "-q", "-u", "origin", "master")
        self.plan_dir = self.root / ".swarmforge" / "plan"
        write_plan(self.plan_dir, dict(make_plan(), base_branch="master"))

    def commit_file(self, name, text, message, cwd=None):
        cwd = cwd or self.root
        (Path(cwd) / name).write_text(text, encoding="utf-8")
        git(cwd, "add", name)
        git(cwd, "commit", "-q", "-m", message)
        return git(cwd, "rev-parse", "HEAD")

    def push_from_another_clone(self, branch="master"):
        other = Path(self._tmp.name) / f"other-{branch}"
        git(Path(self._tmp.name), "clone", "-q", str(self.origin), str(other))
        git(other, "config", "user.email", "t@example.com")
        git(other, "config", "user.name", "t")
        if branch != "master":
            git(other, "checkout", "-q", "-B", branch)
        tip = self.commit_file(f"{branch}.txt", "new\n", f"merged on {branch}", cwd=other)
        git(other, "push", "-q", "origin", branch)
        return tip

    def head(self):
        return git(self.root, "rev-parse", "--short=10", "HEAD")


class BaseFreshnessTests(BaseRepoCase):
    def test_current_checkout_passes(self):
        self.assertEqual(planlib.base_freshness(self.plan_dir, self.head()), ([], []))

    def test_commit_behind_origin_is_rejected_with_instructions(self):
        old = self.head()
        tip = self.push_from_another_clone()
        errors, warnings = planlib.base_freshness(self.plan_dir, old)
        self.assertEqual(warnings, [])
        (error,) = errors
        self.assertIn("git fetch origin --prune", error)
        self.assertIn("git pull --ff-only", error)
        self.assertIn(tip[:10], error)

    def test_local_commits_on_top_of_the_tip_are_fine(self):
        self.commit_file("g.txt", "y\n", "local feature files")
        self.assertEqual(planlib.base_freshness(self.plan_dir, self.head()), ([], []))

    def test_after_pulling_the_commit_passes(self):
        old = self.head()
        self.push_from_another_clone()
        git(self.root, "pull", "-q", "--ff-only")
        self.assertEqual(planlib.base_freshness(self.plan_dir, self.head()), ([], []))
        self.assertNotEqual(old, self.head())

    def test_no_remote_compares_with_the_local_base(self):
        git(self.root, "remote", "remove", "origin")
        old = self.head()
        self.assertEqual(planlib.base_freshness(self.plan_dir, old), ([], []))
        self.commit_file("g.txt", "y\n", "second")
        git(self.root, "checkout", "-q", "-b", "side", old)  # commit made on a stale branch
        errors, _ = planlib.base_freshness(self.plan_dir, old)
        self.assertEqual(len(errors), 1)

    def test_fetch_failure_fails_open_with_a_warning(self):
        old = self.head()
        self.push_from_another_clone()
        git(self.root, "remote", "set-url", "origin", str(Path(self._tmp.name) / "missing.git"))
        errors, warnings = planlib.base_freshness(self.plan_dir, old)
        self.assertEqual(errors, [])
        self.assertIn("git fetch origin failed", warnings[0])

    def test_unknown_remote_branch_falls_back_to_the_local_base(self):
        git(self.root, "branch", "-m", "master", "trunk")
        write_plan(self.plan_dir, dict(make_plan(), base_branch="trunk"))
        self.assertEqual(planlib.base_freshness(self.plan_dir, self.head()), ([], []))

    def test_respects_the_plans_base_branch(self):
        write_plan(self.plan_dir, dict(make_plan(), base_branch="develop"))
        old = self.head()
        git(self.root, "push", "-q", "origin", "master:develop")
        git(self.root, "checkout", "-q", "-b", "develop")
        self.push_from_another_clone("develop")
        errors, _ = planlib.base_freshness(self.plan_dir, old)
        self.assertEqual(len(errors), 1)
        self.assertIn("origin/develop", errors[0])

    def test_env_var_disables_the_check(self):
        old = self.head()
        self.push_from_another_clone()
        with mock.patch.dict(os.environ, {planlib.SKIP_BASE_CHECK_ENV: "1"}):
            self.assertEqual(planlib.base_freshness(self.plan_dir, old), ([], []))

    def test_diverged_local_base_warns_and_does_not_block(self):
        # e.g. after a squash merge: unpushed local commits and a new origin tip.
        self.commit_file("g.txt", "y\n", "unpushed specifier commit")
        local = self.head()
        self.push_from_another_clone()
        errors, warnings = planlib.base_freshness(self.plan_dir, local)
        self.assertEqual(errors, [])
        self.assertIn("diverged", warnings[0])
        self.assertIn("reconcile by hand", warnings[0])

    def test_strictly_behind_error_mentions_the_skip_variable(self):
        old = self.head()
        self.push_from_another_clone()
        (error,) = planlib.base_freshness(self.plan_dir, old)[0]
        self.assertIn(planlib.SKIP_BASE_CHECK_ENV, error)

    def test_missing_plan_is_a_no_op(self):
        self.assertEqual(planlib.base_freshness(Path(self._tmp.name) / "nowhere" / "plan", self.head()), ([], []))


@mock.patch("swarm_python.entrypoints.swarm_handoff.role_known", return_value=True)
@mock.patch("swarm_python.handoff.validate.role_known", return_value=True)
class StaleHandoffTests(BaseRepoCase):
    def send(self, role="specifier", to="coder", type_="git_handoff", commit=None, task="a"):
        draft = self.root / "draft.txt"
        if type_ == "git_handoff":
            text = f"type: git_handoff\nto: {to}\npriority: 50\ntask: {task}\ncommit: {commit or self.head()}\n"
        else:
            text = f"type: note\nto: {to}\npriority: 50\nmessage: hello\n"
        draft.write_text(text, encoding="utf-8")
        env = {"SWARMFORGE_ROLE": role, "SWARMFORGE_PLAN_DIR": str(self.plan_dir)}
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["swarm_handoff", str(draft)]), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    swarm_handoff.main()
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def status(self):
        return planlib.feature_by_id(planlib.load(self.plan_dir), "a")["status"]

    def test_stale_specifier_handoff_is_rejected_and_nothing_is_reserved(self, _a, _b):
        old = self.head()
        self.push_from_another_clone()
        code, _, err = self.send(commit=old)
        self.assertEqual(code, 2)
        self.assertIn("git fetch origin --prune", err)
        self.assertIn("git pull --ff-only", err)
        self.assertEqual(self.status(), "pending")
        self.assertEqual(list((self.root / ".swarmforge" / "handoffs" / "outbox").glob("*.handoff")), [])

    def test_current_specifier_handoff_goes_through(self, _a, _b):
        code, out, err = self.send()
        self.assertEqual(code, 0, err)
        self.assertIn("HANDOFF QUEUED", out)
        self.assertEqual(self.status(), "coding")

    def test_offline_fails_open_with_a_warning(self, _a, _b):
        git(self.root, "remote", "set-url", "origin", str(Path(self._tmp.name) / "missing.git"))
        code, out, err = self.send()
        self.assertEqual(code, 0, err)
        self.assertIn("WARNING: Stale-commit check skipped", err)

    def test_notes_and_other_roles_are_not_checked(self, _a, _b):
        self.push_from_another_clone()
        with mock.patch.object(planlib, "base_freshness", side_effect=AssertionError("must not run")):
            self.assertEqual(self.send(role="coder", to="reviewer", type_="note")[0], 0)
            self.assertEqual(self.send(role="coder", to="reviewer", task="a")[0], 0)


if __name__ == "__main__":
    unittest.main()
