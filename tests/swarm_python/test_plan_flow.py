"""Tests for the plan delivery flow: publish as a PR, merge detection, sync of
the checkout and worktrees, and automatic dispatch of the next feature.

Everything runs against temp dirs with real git and a LOCAL bare repository as
`origin`; `gh` is a fake runner. Nothing touches the network or GitHub.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python import plan as planlib  # noqa: E402
from swarm_python import plan_cli  # noqa: E402
from swarm_python import plan_flow  # noqa: E402
from swarm_python.entrypoints import handoffd  # noqa: E402
from swarm_python.plan_flow import PlanFlow, Result  # noqa: E402


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


def feature(fid, depends_on=(), status="pending", **extra):
    f = {
        "id": fid, "title": fid.title(), "summary": f"Summary of {fid}",
        "depends_on": list(depends_on), "allowed_paths": ["src/**"],
        "acceptance": [f"{fid} works"], "est_lines": 300, "status": status,
    }
    f.update(extra)
    return f


FIELDS = "state,baseRefName,headRefName,headRefOid,mergeCommit"


class FakeGh:
    """Stands in for the `gh` CLI. Records every call, asserts the exact argument
    shapes and records any unexpected call in `errors` (checked at cleanup)."""

    def __init__(self):
        self.calls = []
        self.bodies = []
        self.errors = []
        self.oid = ""
        self.create = Result(0, "https://example.test/o/r/pull/7", "")
        self.view = None     # set by the test case once the final commit is known
        self.listing = Result(0, "[]", "")

    def pr(self, state="OPEN", base="master", head="swarm/mod/a", oid=None, merge=None,
           url="https://example.test/o/r/pull/7"):
        return {"state": state, "baseRefName": base, "headRefName": head,
                "headRefOid": oid or self.oid, "mergeCommit": {"oid": merge} if merge else None,
                "url": url}

    def set_view(self, **kw):
        self.view = Result(0, json.dumps(self.pr(**kw)), "")

    def merged(self, oid="abc123"):
        self.set_view(state="MERGED", merge=oid)

    def __call__(self, args, cwd=None, timeout=0):
        self.calls.append(list(args))
        if args[:2] == ["pr", "create"]:
            keys = args[2::2]
            if len(args) != 10 or keys != ["--base", "--head", "--title", "--body-file"]:
                self.errors.append(f"bad pr create shape: {args}")
                return Result(1, "", "bad shape")
            path = args[9]
            try:
                self.bodies.append(Path(path).read_text(encoding="utf-8"))
            except OSError as e:
                self.errors.append(f"body file unreadable: {e}")
            return self.create
        if args[:2] == ["pr", "view"] and len(args) == 5 and args[3] == "--json" and (
                args[4] == FIELDS or args[4] == "url," + FIELDS):
            return self.view
        if (args[:4] == ["pr", "list", "--state", "merged"] and args[4] == "--head" and args[6] == "--base"
                and args[8:] == ["--json", FIELDS]):
            return self.listing
        self.errors.append(f"unexpected gh call: {args}")
        return Result(1, "", "unexpected")


class FlowCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        patch = mock.patch.dict(os.environ, {"SWARMFORGE_APPROVALS_DIR": str(tmp / "approvals")})
        patch.start()
        self.addCleanup(patch.stop)

        self.origin = tmp / "origin.git"
        git(tmp, "init", "-q", "--bare", "-b", "master", str(self.origin))
        self.root = tmp / "proj"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "master")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        (self.root / "f.txt").write_text("x\n", encoding="utf-8")
        git(self.root, "add", "f.txt")
        git(self.root, "commit", "-q", "-m", "init")
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.write_text(".swarmforge/\n.worktrees/\n", encoding="utf-8")
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "-q", "-u", "origin", "master")
        self.base_head = git(self.root, "rev-parse", "HEAD")

        self.roles = {"specifier": {"worktree-name": "master", "worktree-path": str(self.root)}}
        for name in ("coder", "reviewer", "architect"):
            path = self.root / ".worktrees" / name
            git(self.root, "worktree", "add", "-q", "-B", f"swarmforge-{name}", str(path), "HEAD")
            self.roles[name] = {"worktree-name": name, "worktree-path": str(path)}
            (path / ".swarmforge" / "handoffs" / "outbox").mkdir(parents=True)

        # The architect's finished work: one commit on its worktree branch.
        arch = Path(self.roles["architect"]["worktree-path"])
        (arch / "src").mkdir()
        (arch / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
        git(arch, "add", "src/a.py")
        git(arch, "commit", "-q", "-m", "feature a")
        self.final = git(arch, "rev-parse", "HEAD")

        self.plan_dir = self.root / ".swarmforge" / "plan"
        self.write_plan([feature("a", status="awaiting_merge", base_commit=self.base_head, review_rounds=1),
                         feature("b", ["a"]), feature("c", ["b"])])
        self.gh = FakeGh()
        self.gh.oid = self.final
        self.gh.set_view()
        self.addCleanup(lambda: self.assertEqual(self.gh.errors, []))
        self.now = 0.0
        self.flow = PlanFlow(self.root, gh=self.gh, clock=lambda: self.now, poll_interval=30)

    def write_plan(self, features, **extra):
        plan = {"id": "mod", "title": "Module", "status": "approved", "general_file": "general.md",
                "base_branch": "master", "features": features}
        plan.update(extra)
        plan.setdefault("flow_version", planlib.FLOW_VERSION)
        (self.plan_dir / "features").mkdir(parents=True, exist_ok=True)
        (self.plan_dir / "general.md").write_text("overview\n", encoding="utf-8")
        for f in features:
            (self.plan_dir / "features" / f"{f['id']}.md").write_text(f"{f['id']}\n", encoding="utf-8")
        planlib.save(self.plan_dir, plan)
        planlib.record_approval(self.plan_dir)

    # helpers
    def feat(self, fid):
        return planlib.feature_by_id(planlib.load(self.plan_dir), fid)

    def deliver_completion(self, task="a", body="Re-read your role and constitution.\n\nmerge_and_process architect x\n\nAdded a.\nVerified with tests."):
        self.flow.on_delivered(
            self.roles, "architect",
            {"type": "git_handoff", "task": task, "commit": self.final[:10]}, ["specifier"], body)

    def outbox(self, role):
        d = Path(self.roles[role]["worktree-path"]) / ".swarmforge" / "handoffs" / "outbox"
        return sorted(d.glob("*.handoff"))

    def merge_on_origin(self):
        """Simulate the human merging the PR: the base branch on origin moves to the final commit."""
        git(self.origin, "update-ref", "refs/heads/master", self.final)

    def read_headers(self, path):
        head = path.read_text(encoding="utf-8").split("\n\n", 1)[0]
        return dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)


class PublishTests(FlowCase):
    def test_pushes_the_exact_commit_without_touching_the_master_checkout(self):
        before = (git(self.root, "rev-parse", "HEAD"), git(self.root, "branch", "--show-current"))
        self.deliver_completion()
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/swarm/mod/a"), self.final)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/master"), self.base_head)
        self.assertEqual((git(self.root, "rev-parse", "HEAD"), git(self.root, "branch", "--show-current")), before)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        f = self.feat("a")
        self.assertEqual(f["final_commit"], self.final)
        self.assertEqual(f["pr_url"], "https://example.test/o/r/pull/7")
        self.assertTrue(f["published_at"])
        self.assertEqual(f["status"], "awaiting_merge")

    def test_pr_arguments_and_body(self):
        self.deliver_completion()
        (args,) = [c for c in self.gh.calls if c[:2] == ["pr", "create"]]
        self.assertEqual(args[:9], ["pr", "create", "--base", "master", "--head", "swarm/mod/a",
                                    "--title", "A (Module, 1/3)", "--body-file"])
        self.assertFalse(Path(args[9]).exists())  # temp body file is removed afterwards
        body = self.gh.bodies[-1]
        for needle in ("Summary of a", "Added a.", "- [ ] a works", "Production lines changed: ~1",
                       "within target", "Files outside allowed_paths: none", "Review rounds: 1",
                       "escalated to the architect: no", "Previous feature PR: none",
                       "Opened automatically by SwarmForge"):
            self.assertIn(needle, body)
        self.assertNotIn("merge_and_process", body)

    def test_body_flags_oversize_scope_escalation_and_links_previous_pr(self):
        plan = planlib.load(self.plan_dir)
        feat = dict(plan["features"][1], escalated=True, review_rounds=3)
        body = plan_flow.build_pr_body(
            plan, feat, {"prod_lines": 900, "outside_allowed": ["docs/x.py"]}, "https://x/pull/1")
        self.assertIn("~900 (target ~400, over target)", body)
        self.assertIn("`docs/x.py`", body)
        self.assertIn("escalated to the architect: yes", body)
        self.assertIn("Previous feature PR: https://x/pull/1", body)

    def test_never_forces_and_never_targets_the_base_branch(self):
        calls = []

        def spy(args, cwd=None, timeout=0):
            calls.append(list(args))
            return plan_flow.run_git(args, cwd=cwd, timeout=timeout)

        flow = PlanFlow(self.root, git=spy, gh=self.gh, poll_interval=30)
        flow.on_delivered(self.roles, "architect", {"type": "git_handoff", "task": "a", "commit": self.final[:10]}, ["specifier"], "")
        pushes = [c for c in calls if c[0] == "push"]
        self.assertEqual(pushes, [["push", "origin", f"{self.final}:refs/heads/swarm/mod/a"]])
        self.assertFalse(any("--force" in c or "-f" in c for c in pushes))

    def test_branch_names_are_sanitized(self):
        self.assertEqual(plan_flow.branch_name({"id": "my plan/../x"}, "a"), "swarm/my-plan-.-x/a")
        self.assertEqual(plan_flow.branch_name({"id": "x.lock"}, "a"), "swarm/x-lock/a")

    def test_no_origin_notifies_the_specifier_and_does_not_crash(self):
        git(self.root, "remote", "remove", "origin")
        self.deliver_completion()
        self.assertEqual(self.feat("a")["status"], "awaiting_merge")
        self.assertNotIn("pr_url", self.feat("a"))
        (note,) = self.outbox("architect")
        text = note.read_text(encoding="utf-8")
        self.assertIn("to: specifier", text)
        self.assertIn("type: note", text)
        self.assertIn("swarm plan publish a", text)
        self.assertIn("no 'origin' remote", text)
        self.assertEqual(self.gh.calls, [])

    def test_gh_missing_keeps_the_pushed_branch_and_notifies(self):
        self.gh.create = Result(127, "", "gh not found")
        self.gh.view = Result(127, "", "gh not found")
        self.deliver_completion()
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/swarm/mod/a"), self.final)
        self.assertNotIn("pr_url", self.feat("a"))
        self.assertEqual(self.feat("a")["status"], "awaiting_merge")
        (note,) = self.outbox("architect")
        self.assertIn("gh not found", note.read_text(encoding="utf-8"))

    def test_manual_publish_retry_reuses_an_existing_pr(self):
        self.gh.create = Result(1, "", "a pull request already exists")
        self.gh.set_view(url="https://example.test/o/r/pull/3")
        planlib.update_feature(self.plan_dir, "a", final_commit=self.final)
        result = self.flow.publish("a")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.feat("a")["pr_url"], "https://example.test/o/r/pull/3")
        self.assertTrue(self.flow.publish("a").ok)  # idempotent once published

    def test_publish_rejects_a_feature_that_is_not_awaiting_merge(self):
        result = self.flow.publish("b")
        self.assertFalse(result.ok)
        self.assertIn("not 'awaiting_merge'", result.message)

    def test_auto_publish_false_only_records_the_final_commit(self):
        self.write_plan([feature("a", status="awaiting_merge"), feature("b", ["a"])], auto_publish=False)
        self.deliver_completion()
        self.assertEqual(self.feat("a")["final_commit"], self.final)
        self.assertNotIn("pr_url", self.feat("a"))
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.outbox("architect"), [])

    def test_other_handoffs_are_ignored(self):
        self.flow.on_delivered(self.roles, "coder", {"type": "git_handoff", "task": "a", "commit": self.final[:10]}, ["reviewer"], "")
        self.flow.on_delivered(self.roles, "architect", {"type": "note", "task": ""}, ["specifier"], "")
        self.flow.on_delivered(self.roles, "architect", {"type": "git_handoff", "task": "legacy", "commit": "x"}, ["specifier"], "")
        self.assertEqual(self.gh.calls, [])

    def test_a_failing_runner_never_raises(self):
        def boom(args, cwd=None, timeout=0):
            raise RuntimeError("git exploded")

        flow = PlanFlow(self.root, git=boom, gh=self.gh, poll_interval=30)
        flow.on_delivered(self.roles, "architect", {"type": "git_handoff", "task": "a", "commit": self.final[:10]}, ["specifier"], "")
        self.assertEqual(flow.poll_merges(self.roles, force=True), [])


class MergeDetectionTests(FlowCase):
    def published(self):
        self.deliver_completion()

    def test_detects_merged_through_gh(self):
        self.published()
        self.gh.merged("deadbeef")
        actions = self.flow.poll_merges(self.roles, force=True)
        self.assertIn("feature 'a' merged", actions)
        f = self.feat("a")
        self.assertEqual(f["status"], "merged")
        self.assertEqual(f["merge_commit"], "deadbeef")

    def test_detects_merged_through_reachability_without_gh(self):
        self.gh.create = Result(127, "", "gh not found")
        self.gh.view = Result(127, "", "gh not found")
        self.gh.listing = Result(127, "", "gh not found")
        self.published()
        self.assertEqual(self.feat("a")["status"], "awaiting_merge")
        self.merge_on_origin()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(self.feat("a")["status"], "merged")

    def test_open_pr_is_not_merged(self):
        self.published()
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), [])
        self.assertEqual(self.feat("a")["status"], "awaiting_merge")

    def test_polling_is_rate_limited(self):
        self.published()
        self.flow.poll_merges(self.roles)
        calls = len(self.gh.calls)
        self.now = 10
        self.flow.poll_merges(self.roles)
        self.assertEqual(len(self.gh.calls), calls)
        self.now = 31
        self.flow.poll_merges(self.roles)
        self.assertGreater(len(self.gh.calls), calls)

    def test_no_plan_is_a_no_op(self):
        flow = PlanFlow(Path(self._tmp.name) / "nowhere", gh=self.gh)
        self.assertEqual(flow.poll_merges({}, force=True), [])


class SyncAndDispatchTests(FlowCase):
    def merge(self):
        self.deliver_completion()
        self.merge_on_origin()

    def test_fast_forwards_a_clean_master_and_resets_idle_worktrees(self):
        self.merge()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.final)
        for name in ("coder", "reviewer", "architect"):
            wt = self.roles[name]["worktree-path"]
            self.assertEqual(git(wt, "rev-parse", "HEAD"), self.final, name)
            self.assertEqual(git(wt, "branch", "--show-current"), f"swarmforge-{name}")
        self.assertTrue(self.feat("a")["synced_at"])

    def test_a_dirty_master_is_skipped_and_the_specifier_is_told(self):
        self.merge()
        (self.root / "f.txt").write_text("edited by the user\n", encoding="utf-8")
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base_head)
        self.assertEqual((self.root / "f.txt").read_text(encoding="utf-8"), "edited by the user\n")
        texts = [p.read_text(encoding="utf-8") for p in self.outbox("architect")]
        self.assertTrue(any("uncommitted changes; it was not updated" in t for t in texts))
        # Worktrees were still updated and the next feature still went out.
        self.assertEqual(git(self.roles["coder"]["worktree-path"], "rev-parse", "HEAD"), self.final)
        self.assertEqual(self.feat("b")["status"], "coding")

    def test_master_on_another_branch_is_not_switched(self):
        git(self.root, "checkout", "-q", "-b", "user-work")
        self.merge()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(git(self.root, "branch", "--show-current"), "user-work")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.base_head)

    def test_dirty_coder_worktree_is_deferred_and_nothing_is_lost(self):
        self.merge()
        coder = Path(self.roles["coder"]["worktree-path"])
        (coder / "f.txt").write_text("half-done work\n", encoding="utf-8")
        before = git(coder, "rev-parse", "HEAD")
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), ["feature 'a' merged"])
        self.assertEqual((coder / "f.txt").read_text(encoding="utf-8"), "half-done work\n")
        self.assertEqual(git(coder, "rev-parse", "HEAD"), before)
        self.assertEqual(self.feat("b")["status"], "pending")  # not advanced
        self.assertNotIn("synced_at", self.feat("a"))
        self.assertEqual(self.outbox("specifier"), [])
        # Once the worktree is clean the next poll finishes the job.
        git(coder, "checkout", "--", "f.txt")
        actions = self.flow.poll_merges(self.roles, force=True)
        self.assertTrue(any("dispatched 'b'" in a for a in actions), actions)
        self.assertEqual(git(coder, "rev-parse", "HEAD"), self.final)

    def test_role_with_pending_inbox_work_is_deferred(self):
        self.merge()
        inbox = Path(self.roles["reviewer"]["worktree-path"]) / ".swarmforge" / "handoffs" / "inbox" / "new"
        inbox.mkdir(parents=True)
        (inbox / "x.handoff").write_text("to: reviewer\n\nbody\n", encoding="utf-8")
        before = git(self.roles["reviewer"]["worktree-path"], "rev-parse", "HEAD")
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(git(self.roles["reviewer"]["worktree-path"], "rev-parse", "HEAD"), before)
        self.assertEqual(self.feat("b")["status"], "pending")

    def test_dispatches_the_next_feature_through_the_gate(self):
        self.merge()
        self.flow.poll_merges(self.roles, force=True)
        (handoff,) = self.outbox("specifier")
        h = self.read_headers(handoff)
        self.assertEqual((h["from"], h["to"], h["type"], h["task"]), ("specifier", "coder", "git_handoff", "b"))
        self.assertEqual(h["commit"], self.final[:10])
        self.assertIn(f"merge_and_process specifier {self.final[:10]}", handoff.read_text(encoding="utf-8"))
        b = self.feat("b")
        self.assertEqual(b["status"], "coding")
        self.assertEqual(b["base_commit"], self.final)
        (note,) = self.outbox("architect")
        self.assertIn("dispatched b", self.read_headers(note)["message"])
        # Idempotent: another poll does not dispatch twice.
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(len(self.outbox("specifier")), 1)

    def test_dispatch_respects_the_approval_gate(self):
        self.merge()
        (self.plan_dir / "general.md").write_text("tampered after approval\n", encoding="utf-8")
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertEqual(self.outbox("specifier"), [])
        (note,) = self.outbox("architect")
        self.assertIn("plan content changed since approval", note.read_text(encoding="utf-8"))

    def test_dispatch_respects_the_sequential_rule(self):
        self.write_plan([feature("a", status="awaiting_merge", base_commit=self.base_head),
                         feature("b", ["a"]), feature("x", status="coding")])
        self.merge()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertEqual(self.outbox("specifier"), [])

    def test_auto_next_false_leaves_dispatch_to_the_specifier(self):
        self.write_plan([feature("a", status="awaiting_merge", base_commit=self.base_head),
                         feature("b", ["a"])], auto_next=False)
        self.merge()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertEqual(self.outbox("specifier"), [])
        (note,) = self.outbox("architect")
        self.assertIn("auto_next is off", self.read_headers(note)["message"])

    def test_outbox_failure_rolls_the_reservation_back(self):
        self.merge()
        with mock.patch.object(PlanFlow, "_write_outbox", side_effect=OSError("disk full")):
            self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertNotIn("synced_at", self.feat("a"))

    def test_last_feature_marks_the_plan_done(self):
        self.write_plan([feature("a", status="merged", synced_at="t"),
                         feature("b", ["a"], status="awaiting_merge", base_commit=self.base_head)])
        self.gh.set_view(head="swarm/mod/b")
        self.deliver_completion("b")
        self.merge_on_origin()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(planlib.load(self.plan_dir)["status"], "done")
        self.assertEqual(self.outbox("specifier"), [])
        (note,) = self.outbox("architect")
        self.assertIn("Plan complete", self.read_headers(note)["message"])

    def test_manual_set_status_merged_is_also_synced(self):
        self.deliver_completion()
        planlib.set_feature_status(self.plan_dir, "a", "merged")
        self.merge_on_origin()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(self.feat("b")["status"], "coding")

    def test_sync_waits_while_a_feature_is_in_progress(self):
        self.write_plan([feature("a", status="merged"), feature("b", ["a"], status="coding")])
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), [])
        self.assertNotIn("synced_at", self.feat("a"))


class HookTests(FlowCase):
    def test_deliver_survives_a_failing_after_deliver_hook(self):
        out = Path(self._tmp.name) / "box"
        outbox = out / "architect" / ".swarmforge" / "handoffs" / "outbox"
        outbox.mkdir(parents=True)
        msg = outbox / "m.handoff"
        msg.write_text("from: architect\nto: specifier\ntype: git_handoff\ntask: a\ncommit: abc\n\nbody\n", encoding="utf-8")
        roles = {n: {"worktree-path": str(out / n), "pane-id": ""} for n in ("architect", "specifier")}

        def boom(*_):
            raise RuntimeError("hook exploded")

        handoffd.deliver(roles, "s", "architect", msg, out / "log", out / "daemon",
                         is_running=lambda r: False, after_deliver=boom)
        self.assertTrue((out / "specifier/.swarmforge/handoffs/inbox/new/m.handoff").exists())
        self.assertTrue((out / "architect/.swarmforge/handoffs/sent/m.handoff").exists())
        self.assertIn("after-deliver-error", (out / "log").read_text(encoding="utf-8"))

    def test_poll_once_survives_a_failing_plan_poll_and_passes_hooks(self):
        state = Path(self._tmp.name) / "state"
        (state / "daemon").mkdir(parents=True)
        (state / "roles.tsv").write_text("specifier\tmaster\t/x\ts\td\ta\n", encoding="utf-8")
        (state / "herdr-session").write_text("sess\n", encoding="utf-8")

        class Boom:
            def poll_merges(self, roles):
                raise RuntimeError("poll exploded")

        log = state / "daemon" / "handoffd.log"
        handoffd.poll_once(state, state / "daemon", state / "daemon" / "stop", log, None, Boom())
        self.assertIn("plan-poll-error", log.read_text(encoding="utf-8"))

    def test_poll_once_calls_on_delivered_after_a_real_delivery(self):
        state = Path(self._tmp.name) / "state2"
        arch = Path(self._tmp.name) / "a"
        spec = Path(self._tmp.name) / "s"
        (state / "daemon").mkdir(parents=True)
        (state / "roles.tsv").write_text(
            f"architect\tarchitect\t{arch}\ts\td\ta\n" f"specifier\tmaster\t{spec}\ts\td\ta\n", encoding="utf-8")
        (state / "herdr-session").write_text("sess\n", encoding="utf-8")
        outbox = arch / ".swarmforge" / "handoffs" / "outbox"
        outbox.mkdir(parents=True)
        (outbox / "m.handoff").write_text("from: architect\nto: specifier\ntype: git_handoff\ntask: a\ncommit: abc\n\nbody\n", encoding="utf-8")
        seen = []

        class Spy:
            def on_delivered(self, roles, sender, headers, recipients, body):
                seen.append((sender, headers["task"], recipients))

            def poll_merges(self, roles):
                seen.append("poll")

        with mock.patch.object(handoffd, "notify", lambda *a: None):
            handoffd.poll_once(state, state / "daemon", state / "daemon" / "stop", state / "daemon" / "log", None, Spy())
        self.assertEqual(seen, [("architect", "a", ["specifier"]), "poll"])


class PlanModelTests(FlowCase):
    def test_runtime_and_delivery_fields_do_not_invalidate_the_approval(self):
        self.assertEqual(planlib.approval_errors(self.plan_dir), [])
        planlib.update_feature(self.plan_dir, "a", final_commit="f" * 40, pr_url="https://x/1",
                               published_at="t", merge_commit="m", synced_at="s", report="r")
        plan = planlib.load(self.plan_dir)
        plan.update(auto_publish=False, auto_next=False, base_branch="develop", status="done")
        planlib.save(self.plan_dir, plan)
        self.assertEqual(planlib.approval_errors(self.plan_dir), [])

    def test_scope_changes_still_invalidate_the_approval(self):
        plan = planlib.load(self.plan_dir)
        plan["features"][1]["acceptance"].append("new criterion")
        planlib.save(self.plan_dir, plan)
        self.assertTrue(planlib.approval_errors(self.plan_dir))

    def test_validation_of_the_new_fields(self):
        plan = planlib.load(self.plan_dir)
        errors, _ = planlib.validate(plan, self.plan_dir)
        self.assertEqual(errors, [])
        plan.update(status="done", auto_publish=True, auto_next=False)
        self.assertEqual(planlib.validate(plan, self.plan_dir)[0], [])
        plan.update(auto_publish="yes", auto_next=1, base_branch="")
        plan["features"][0]["pr_url"] = 5
        text = "\n".join(planlib.validate(plan, self.plan_dir)[0])
        for needle in ("auto_publish", "auto_next", "base_branch", "pr_url"):
            self.assertIn(needle, text)

    def test_reopening_a_feature_clears_the_delivery_state(self):
        planlib.update_feature(self.plan_dir, "a", final_commit="f" * 40, pr_url="https://x/1",
                               published_at="t", merge_commit="m", synced_at="s", report="r")
        planlib.set_feature_status(self.plan_dir, "a", "coding")
        f = self.feat("a")
        for name in planlib.STRING_RUNTIME_FIELDS:
            self.assertNotIn(name, f)

    def test_approve_records_the_current_branch_as_base(self):
        plan = planlib.load(self.plan_dir)
        plan.pop("base_branch")
        planlib.save(self.plan_dir, plan)
        git(self.root, "checkout", "-q", "-b", "develop")
        planlib.approve(self.plan_dir, "now")
        self.assertEqual(planlib.load(self.plan_dir)["base_branch"], "develop")
        git(self.root, "checkout", "-q", "master")
        planlib.approve(self.plan_dir, "now")
        self.assertEqual(planlib.load(self.plan_dir)["base_branch"], "develop")  # kept

    def test_extract_report_drops_the_boilerplate(self):
        body = "Re-read your role and constitution.\n\nmerge_and_process architect abc\n\nDid X.\n"
        self.assertEqual(plan_flow.extract_report(body), "Did X.")


# --- review-hardening tests -------------------------------------------------------

class SafetyCase(FlowCase):
    def merge(self):
        self.deliver_completion()
        self.merge_on_origin()

    def notes(self):
        return [p.read_text(encoding="utf-8") for p in self.outbox("architect")]

    def coder(self):
        return Path(self.roles["coder"]["worktree-path"])


class DeliveryTargetTests(SafetyCase):
    def test_unsafe_base_branch_names_are_rejected(self):
        for bad in ("swarm/mod/a", "swarm", "a..b", "-x", "a b", "a/", "x.lock", "", " main", "a\tb", "a~1"):
            self.assertFalse(planlib.valid_base_branch(bad), repr(bad))
        for good in ("master", "main", "release/1.2", "feat/x_y-z"):
            self.assertTrue(planlib.valid_base_branch(good), good)

    def test_validate_and_approve_reject_an_unsafe_base(self):
        plan = planlib.load(self.plan_dir)
        plan["base_branch"] = "swarm/other/x"
        self.assertIn("base_branch", "\n".join(planlib.validate(plan, self.plan_dir)[0]))
        planlib.save(self.plan_dir, plan)
        with self.assertRaises(planlib.PlanError):
            planlib.approve(self.plan_dir, "now")

    def test_flow_refuses_a_swarm_base_and_pushes_nothing(self):
        self.write_plan([feature("a", status="awaiting_merge", base_commit=self.base_head)],
                        base_branch="swarm/mod/a")
        planlib.update_feature(self.plan_dir, "a", final_commit=self.final)
        result = self.flow.publish("a")
        self.assertFalse(result.ok)
        self.assertIn("Refusing", result.message)
        self.assertEqual(git(self.origin, "branch", "--list", "swarm/*"), "")
        self.assertEqual(self.gh.calls, [])

    def test_base_branch_changed_after_approval_refuses(self):
        plan = planlib.load(self.plan_dir)
        plan["base_branch"] = "develop"  # delivery fields do not invalidate the approval
        planlib.save(self.plan_dir, plan)
        self.deliver_completion()
        self.assertNotIn("pr_url", self.feat("a"))
        self.assertEqual(git(self.origin, "branch", "--list", "swarm/*"), "")
        self.assertEqual(self.gh.calls, [])
        (note,) = self.notes()
        self.assertIn("'master' was approved", note)

    def test_origin_url_changed_after_approval_refuses_to_push(self):
        other = Path(self._tmp.name) / "evil.git"
        git(self._tmp.name, "init", "-q", "--bare", str(other))
        git(self.root, "remote", "set-url", "origin", str(other))
        self.deliver_completion()
        self.assertEqual(git(other, "branch", "--list", "swarm/*"), "")
        self.assertNotIn("pr_url", self.feat("a"))
        self.assertIn("origin remote URL changed", self.notes()[0])
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), [])
        self.assertEqual(self.gh.calls, [])

    def test_approval_pins_base_and_origin(self):
        pins = planlib.approval_pins(self.plan_dir)
        self.assertEqual(pins, {"base_branch": "master", "origin_url": str(self.origin)})

    def test_pre_pinning_approval_is_pinned_once_to_the_current_targets(self):
        path = planlib.approval_file(self.plan_dir)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("base_branch")
        data.pop("origin_url")
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(planlib.approval_pins(self.plan_dir))
        self.deliver_completion()
        self.assertTrue(self.feat("a")["pr_url"])
        self.assertEqual(planlib.approval_pins(self.plan_dir)["base_branch"], "master")

    def test_gh_answers_that_do_not_match_the_feature_are_ignored(self):
        self.deliver_completion()
        bad = {"base": dict(state="MERGED", base="develop"),
               "head": dict(state="MERGED", head="swarm/mod/other"),
               "oid": dict(state="MERGED", oid="f" * 40)}
        for name, kw in bad.items():
            self.gh.set_view(**kw)
            self.assertEqual(self.flow.poll_merges(self.roles, force=True), [], name)
            self.assertEqual(self.feat("a")["status"], "awaiting_merge", name)
        self.assertEqual(len([n for n in self.notes() if "does not match" in n]), 1)  # notified once

    def test_a_pr_url_in_plan_json_alone_is_not_trusted(self):
        planlib.update_feature(self.plan_dir, "a", final_commit=self.final,
                               pr_url="https://example.test/o/r/pull/99")
        self.gh.set_view(state="MERGED", head="swarm/mod/not-a")
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), [])
        self.assertEqual(self.feat("a")["status"], "awaiting_merge")

    def test_squash_merge_is_detected_through_the_merged_pr_list(self):
        self.deliver_completion()  # origin/master never contains the final commit
        self.gh.view = Result(1, "", "no pull requests found")
        self.gh.listing = Result(0, json.dumps([self.gh.pr(state="MERGED", merge="squash1")]), "")
        self.assertIn("feature 'a' merged", self.flow.poll_merges(self.roles, force=True))
        self.assertEqual(self.feat("a")["merge_commit"], "squash1")
        self.assertIn(["pr", "list", "--state", "merged", "--head", "swarm/mod/a", "--base", "master",
                       "--json", FIELDS], self.gh.calls)


class PublishHardeningTests(SafetyCase):
    def test_closed_pr_notifies_once_stops_polling_and_republishes_on_a_fresh_branch(self):
        self.deliver_completion()
        self.gh.set_view(state="CLOSED")
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), [])
        f = self.feat("a")
        self.assertEqual(f["status"], "awaiting_merge")
        self.assertTrue(f["pr_closed_at"])
        (note,) = self.notes()
        self.assertIn("closed without", note)
        self.assertIn("swarm plan publish a", note)
        calls = len(self.gh.calls)
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(len(self.gh.calls), calls)       # polling stopped
        self.assertEqual(len(self.notes()), 1)              # no second note
        self.gh.create = Result(0, "https://example.test/o/r/pull/9", "")
        result = self.flow.publish("a")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/swarm/mod/a-r2"), self.final)
        f = self.feat("a")
        self.assertEqual((f["pr_url"], f["pub_branch"]), ("https://example.test/o/r/pull/9", "swarm/mod/a-r2"))
        self.assertNotIn("pr_closed_at", f)
        self.assertEqual(self.gh.calls[-1][5], "swarm/mod/a-r2")

    def test_stale_merged_or_other_commit_pr_is_never_reused(self):
        self.gh.create = Result(1, "", "a pull request already exists")
        planlib.update_feature(self.plan_dir, "a", final_commit=self.final)
        for kw in (dict(state="MERGED"), dict(state="CLOSED"), dict(oid="e" * 40), dict(base="develop")):
            self.gh.set_view(**kw)
            result = self.flow.publish("a")
            self.assertFalse(result.ok, kw)
            self.assertNotIn("pr_url", self.feat("a"))

    def test_publish_falls_back_to_the_architect_branch_tip(self):
        result = self.flow.publish("a")  # no final_commit recorded
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.feat("a")["final_commit"], self.final)

    def test_final_commit_must_descend_from_the_recorded_base(self):
        unrelated = git(self.root, "commit-tree", "HEAD^{tree}", "-m", "orphan")
        planlib.update_feature(self.plan_dir, "a", final_commit=self.final, base_commit=unrelated)
        result = self.flow.publish("a")
        self.assertFalse(result.ok)
        self.assertIn("does not descend", result.message)
        self.assertEqual(git(self.origin, "branch", "--list", "swarm/*"), "")

    def test_an_ambiguous_abbreviation_resolves_to_the_architect_tip(self):
        short = self.final[:10]

        def spy(args, cwd=None, timeout=0):
            if args[:1] == ["rev-parse"] and f"{short}^{{commit}}" in args:
                return Result(128, "", "short object ID is ambiguous")
            return plan_flow.run_git(args, cwd=cwd, timeout=timeout)

        flow = PlanFlow(self.root, git=spy, gh=self.gh, poll_interval=30)
        flow.on_delivered(self.roles, "architect", {"type": "git_handoff", "task": "a", "commit": short},
                          ["specifier"], "")
        self.assertEqual(self.feat("a")["final_commit"], self.final)

    def test_a_failing_hook_tells_the_specifier(self):
        def boom(args, cwd=None, timeout=0):
            raise RuntimeError("git exploded")

        flow = PlanFlow(self.root, git=boom, gh=self.gh, poll_interval=30)
        flow.on_delivered(self.roles, "architect", {"type": "git_handoff", "task": "a", "commit": self.final[:10]},
                          ["specifier"], "")
        (note,) = self.notes()
        self.assertIn("swarm plan publish a", note)

    def test_pr_body_goes_through_a_redacted_capped_body_file(self):
        secrets = ["ghp_" + "A" * 36, "github_pat_" + "B" * 30, "sk-" + "c" * 30, "AKIA" + "D" * 16,
                   "Bearer abcdef0123456789", r"C:\Users\bob\work\x", "/home/alice/proj", "/Users/carol/p",
                   str(self.root)]
        report = "Did the thing.\n" + "\n".join(secrets) + "\npassword: hunter2\n" + "x" * 9000
        self.deliver_completion(body=f"Re-read your role and constitution.\n\n{report}")
        body = self.gh.bodies[-1]
        for secret in secrets + ["hunter2"]:
            self.assertNotIn(secret, body)
        self.assertIn("[redacted-token]", body)
        self.assertIn("Did the thing.", body)
        self.assertNotIn("x" * 4001, body)
        self.assertEqual(plan_flow.redact("see C:\\Users\\bob\\a and /home/al/b"), "see <user-dir>\\a and <user-dir>/b")


class ResetSafetyTests(SafetyCase):
    def assert_deferred(self, mutate):
        self.merge()
        mutate(self.coder())
        before = git(self.coder(), "rev-parse", "HEAD")
        branch_before = git(self.coder(), "rev-parse", "--abbrev-ref", "HEAD")
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), ["feature 'a' merged"])
        self.assertEqual(git(self.coder(), "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.coder(), "rev-parse", "--abbrev-ref", "HEAD"), branch_before)
        for name in ("reviewer", "architect"):  # all-or-nothing: no other worktree moved either
            self.assertEqual(git(self.roles[name]["worktree-path"], "rev-parse", "HEAD"),
                             self.final if name == "architect" else self.base_head)
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertNotIn("synced_at", self.feat("a"))

    def test_detached_head_is_deferred(self):
        self.assert_deferred(lambda wt: git(wt, "checkout", "-q", "--detach"))

    def test_paused_rebase_is_deferred(self):
        def pause(wt):
            p = Path(git(wt, "rev-parse", "--git-path", "rebase-merge"))
            (p if p.is_absolute() else wt / p).mkdir(parents=True)

        self.assert_deferred(pause)

    def test_merge_in_progress_is_deferred(self):
        def merging(wt):
            p = Path(git(wt, "rev-parse", "--git-path", "MERGE_HEAD"))
            (p if p.is_absolute() else wt / p).write_text(self.base_head + "\n", encoding="utf-8")

        self.assert_deferred(merging)

    def test_wrong_branch_is_deferred(self):
        self.assert_deferred(lambda wt: git(wt, "checkout", "-q", "-b", "side-work"))

    def test_non_empty_outbox_is_deferred(self):
        def unsent(wt):
            (wt / ".swarmforge" / "handoffs" / "outbox" / "m.handoff").write_text("x\n", encoding="utf-8")

        self.assert_deferred(unsent)

    def test_untracked_file_is_deferred_and_kept(self):
        self.assert_deferred(lambda wt: (wt / "scratch.txt").write_text("keep me\n", encoding="utf-8"))
        self.assertEqual((self.coder() / "scratch.txt").read_text(encoding="utf-8"), "keep me\n")

    def test_ignored_file_colliding_with_the_new_base_is_deferred(self):
        (self.root / ".git" / "info" / "exclude").write_text(
            ".swarmforge/\n.worktrees/\nsrc/a.py\n", encoding="utf-8")

        def collide(wt):
            (wt / "src").mkdir()
            (wt / "src" / "a.py").write_text("my ignored work\n", encoding="utf-8")

        self.assert_deferred(collide)
        self.assertEqual((self.coder() / "src" / "a.py").read_text(encoding="utf-8"), "my ignored work\n")

    def test_orphaned_local_commits_get_a_backup_ref(self):
        self.merge()
        coder = self.coder()
        (coder / "mine.txt").write_text("local\n", encoding="utf-8")
        git(coder, "add", "mine.txt")
        git(coder, "commit", "-q", "-m", "local only")
        tip = git(coder, "rev-parse", "HEAD")
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(git(coder, "rev-parse", "HEAD"), self.final)
        refs = git(self.root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/swarmforge/backup/coder/")
        self.assertEqual(len(refs.splitlines()), 1, refs)
        self.assertTrue(refs.endswith(tip))
        self.assertIn("backup-ref", (self.root / ".swarmforge" / "logs" / "plan-flow.log").read_text(encoding="utf-8"))

    def test_normal_reset_creates_no_backup(self):
        self.merge()
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(git(self.root, "for-each-ref", "refs/swarmforge/backup/"), "")

    def test_hand_merged_feature_does_not_reset_worktrees(self):
        planlib.update_feature(self.plan_dir, "a", final_commit=self.final)
        planlib.set_feature_status(self.plan_dir, "a", "merged")  # no PR, no merge commit
        git(self.root, "push", "-q", "origin", f"{self.final}:refs/heads/elsewhere")
        self.merge_on_origin()
        before = git(self.coder(), "rev-parse", "HEAD")
        actions = self.flow.poll_merges(self.roles, force=True)
        self.assertTrue(any("marked merged by hand" in a for a in actions), actions)
        self.assertEqual(git(self.coder(), "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.final)  # master still fast-forwarded
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertTrue(self.feat("a")["synced_at"])
        self.assertIn("by hand", "".join(self.notes()))

    def test_deferrals_back_off_and_notify_once_after_five(self):
        self.merge()
        (self.coder() / "f.txt").write_text("half-done\n", encoding="utf-8")
        for _ in range(4):
            self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(self.notes(), [])
        self.assertGreater(self.flow._defer_until, self.now)
        for _ in range(3):
            self.flow.poll_merges(self.roles, force=True)
        (note,) = self.notes()
        self.assertIn("coder", note)
        self.assertIn("uncommitted", note)
        self.assertIn(str(self.coder()), note)
        self.assertTrue(self.feat("a")["defer_noted_at"])
        # Not forced: the deferral backoff skips the sync attempt entirely.
        self.now = 1000
        calls = []
        real = self.flow.git
        self.flow.git = lambda args, cwd=None, timeout=0: (calls.append(args), real(args, cwd=cwd, timeout=timeout))[1]
        self.flow._defer_until = 5000
        self.flow.poll_merges(self.roles)
        self.assertFalse([c for c in calls if c[0] == "checkout"])

    def test_sync_rechecks_the_state_under_the_lock(self):
        self.merge()
        self.flow.poll_merges(self.roles, force=True)
        resets = []
        real = self.flow.git
        self.flow.git = lambda args, cwd=None, timeout=0: (resets.append(args), real(args, cwd=cwd, timeout=timeout))[1]
        self.assertEqual(self.flow.sync_and_advance("a", self.roles), [])  # already synced by the other process
        self.assertFalse([c for c in resets if c[0] == "checkout"])


class MigrationTests(SafetyCase):
    def test_old_merges_are_marked_synced_and_never_trigger_a_sync(self):
        self.write_plan([feature("a", status="merged"), feature("b", ["a"])], flow_version=0)
        before = {n: git(self.roles[n]["worktree-path"], "rev-parse", "HEAD") for n in ("coder", "reviewer")}
        git(self.root, "push", "-q", "origin", f"{self.final}:refs/heads/elsewhere")
        self.merge_on_origin()
        self.assertEqual(self.flow.poll_merges(self.roles, force=True), [])
        self.assertEqual(self.feat("a")["synced_at"], "migrated")
        self.assertEqual(self.feat("b")["status"], "pending")
        self.assertEqual(planlib.load(self.plan_dir)["flow_version"], planlib.FLOW_VERSION)
        self.assertEqual(self.outbox("specifier"), [])
        self.assertEqual({n: git(self.roles[n]["worktree-path"], "rev-parse", "HEAD") for n in before}, before)
        self.assertEqual(planlib.approval_errors(self.plan_dir), [])  # the version never touches the approval


class WorkerTests(SafetyCase):
    def test_the_daemon_hooks_return_at_once_while_a_slow_publish_runs(self):
        started, release = threading.Event(), threading.Event()
        inner = self.gh

        def slow(args, cwd=None, timeout=0):
            if args[:2] == ["pr", "create"]:
                started.set()
                release.wait(20)
            return inner(args, cwd=cwd, timeout=timeout)

        flow = PlanFlow(self.root, gh=slow, poll_interval=30, synchronous=False, clock=lambda: self.now)
        self.addCleanup(flow.close)
        self.addCleanup(release.set)
        t0 = time.monotonic()
        flow.on_delivered(self.roles, "architect",
                          {"type": "git_handoff", "task": "a", "commit": self.final[:10]}, ["specifier"], "")
        self.assertEqual(flow.poll_merges(self.roles, force=True), [])
        self.assertLess(time.monotonic() - t0, 3)
        self.assertTrue(started.wait(20))
        self.assertEqual(flow.poll_merges(self.roles, force=True), [])  # already queued: no pile-up
        self.assertNotIn("pr_url", self.feat("a"))
        release.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not self.feat("a").get("pr_url"):
            time.sleep(0.1)
        self.assertEqual(self.feat("a")["pr_url"], "https://example.test/o/r/pull/7")

    def test_poll_once_does_not_wait_for_the_flow(self):
        state = Path(self._tmp.name) / "st"
        (state / "daemon").mkdir(parents=True)
        (state / "roles.tsv").write_text("specifier\tmaster\t/x\ts\td\ta\n", encoding="utf-8")
        (state / "herdr-session").write_text("sess\n", encoding="utf-8")
        gate = threading.Event()
        self.addCleanup(gate.set)
        flow = PlanFlow(self.root, gh=self.gh, synchronous=False, clock=lambda: self.now)
        self.addCleanup(flow.close)
        real_poll = flow._poll
        flow._poll = lambda *a: (gate.wait(20), real_poll(*a))[1]
        t0 = time.monotonic()
        handoffd.poll_once(state, state / "daemon", state / "daemon" / "stop", state / "daemon" / "log", None, flow)
        self.assertLess(time.monotonic() - t0, 3)

    def test_worker_survives_a_failing_job_and_close_is_clean(self):
        flow = PlanFlow(self.root, gh=self.gh, synchronous=False)
        flow._on_delivered = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        flow.on_delivered(self.roles, "architect", {"type": "git_handoff", "task": "a", "commit": "x"},
                          ["specifier"], "")
        flow.close()
        self.assertIn("worker-error", (self.root / ".swarmforge" / "logs" / "plan-flow.log").read_text(encoding="utf-8"))
        self.assertIsNone(flow._worker)

    def test_poll_failures_back_off_exponentially_with_a_cap_and_reset(self):
        self.deliver_completion()
        real = plan_flow.run_git

        def down(args, cwd=None, timeout=0):
            if args[:1] == ["fetch"]:
                return Result(1, "", "network down")
            return real(args, cwd=cwd, timeout=timeout)

        self.flow.git = down
        delays = []
        for _ in range(9):
            self.now += 10000
            self.flow.poll_merges(self.roles)
            delays.append(self.flow._poll_delay())
        self.assertEqual(delays[0], 60)
        self.assertEqual(delays[1], 120)
        self.assertEqual(delays[-1], plan_flow.BACKOFF_CAP_S)
        # The timestamp is set after the poll, so a backed-off poll is skipped.
        calls = len(self.gh.calls)
        self.now += 10
        self.flow.poll_merges(self.roles)
        self.assertEqual(len(self.gh.calls), calls)
        self.flow.git = real
        self.now += 10000
        self.flow.poll_merges(self.roles)
        self.assertEqual(self.flow._failures, 0)
        self.assertEqual(self.flow._poll_delay(), 30)

    def test_a_poll_fetches_once(self):
        self.deliver_completion()
        self.merge_on_origin()
        seen = []
        real = plan_flow.run_git
        self.flow.git = lambda args, cwd=None, timeout=0: (seen.append(args), real(args, cwd=cwd, timeout=timeout))[1]
        self.flow.poll_merges(self.roles, force=True)
        self.assertEqual(len([a for a in seen if a[:1] == ["fetch"]]), 1)

    def test_daemon_uses_short_timeouts(self):
        self.assertLessEqual(max(plan_flow.DAEMON_TIMEOUTS.values()), 90)
        self.assertLessEqual(plan_flow.DAEMON_TIMEOUTS["git_timeout"], 30)
        self.assertLessEqual(plan_flow.DAEMON_TIMEOUTS["gh_timeout"], 30)
        seen = []
        flow = PlanFlow(self.root, git=lambda a, cwd=None, timeout=0: (seen.append(timeout), Result(0, "", ""))[1],
                        gh=self.gh, **plan_flow.DAEMON_TIMEOUTS)
        flow._has_origin()
        self.assertEqual(seen, [20])


class MirrorTests(unittest.TestCase):
    def test_swarmforge_and_template_copies_are_identical(self):
        repo = SCRIPTS.parents[1]
        rels = ["plan.py", "plan_cli.py", "plan_flow.py", "entrypoints/handoffd.py"]
        for rel in rels:
            a = (repo / "swarmforge" / "scripts" / "swarm_python" / rel).read_bytes().replace(b"\r\n", b"\n")
            b = (SCRIPTS / "swarm_python" / rel).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(a, b, rel)


class CliTests(FlowCase):
    def run_cli(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = plan_cli.main(argv, plan_dir=self.plan_dir, env=env or {}, flow=self.flow)
        return code, out.getvalue(), err.getvalue()

    def test_publish_is_human_only(self):
        code, _, err = self.run_cli(["publish", "a"], {"SWARMFORGE_ROLE": "specifier"})
        self.assertEqual(code, 3)
        self.assertEqual(self.gh.calls, [])

    def test_publish_and_status_show_the_pr(self):
        planlib.update_feature(self.plan_dir, "a", final_commit=self.final)
        code, out, err = self.run_cli(["publish", "a"])
        self.assertEqual(code, 0, err)
        self.assertIn("https://example.test/o/r/pull/7", out)
        code, out, _ = self.run_cli(["status"])
        self.assertIn("PR: https://example.test/o/r/pull/7", out)
        self.assertIn(f"commit final: {self.final[:10]}", out)

    def test_publish_failure_is_reported(self):
        with mock.patch.object(PlanFlow, "_resolve", return_value=""):
            code, _, err = self.run_cli(["publish", "a"])  # no usable final commit
        self.assertEqual(code, 1)
        self.assertIn("final commit", err)

    def test_sync_is_human_only_and_runs_the_poll(self):
        self.assertEqual(self.run_cli(["sync"], {"SWARMFORGE_ROLE": "coder"})[0], 3)
        self.deliver_completion()
        self.merge_on_origin()
        (self.root / ".swarmforge" / "roles.tsv").write_text(
            "".join(f"{r}\t{i['worktree-name']}\t{i['worktree-path']}\n" for r, i in self.roles.items()),
            encoding="utf-8")
        code, out, _ = self.run_cli(["sync"])
        self.assertEqual(code, 0)
        self.assertIn("feature 'a' merged", out)
        self.assertEqual(self.feat("b")["status"], "coding")


if __name__ == "__main__":
    unittest.main()
