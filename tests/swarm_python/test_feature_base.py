"""Tests for the guarantees that keep a feature on the merged base.

The bug this covers: a feature was built on the pre-squash history of the
coder's branch, so the finished commit did not descend from the recorded base
and the swarm refused to open the PR AFTER the whole code/review/architect
cycle was spent. The fix is to make the problem visible when the feature
STARTS:

  * the base commit of a feature is the one the handoff carries, not whatever
    the specifier's checkout happens to be;
  * the coder's worktree must descend from that base before the feature is
    handed over (behind -> fast-forward, pre-merge history -> reset with a
    backup ref, own work -> the handoff is refused);
  * the coder is warned at review time when its commit does not descend from
    the base;
  * `swarm plan rebase <id>` is the supported recovery for a stuck feature.

Everything runs against temp dirs with real git and a LOCAL bare repository
as `origin`. Nothing touches the network or GitHub.

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
from swarm_python import worktree_ops  # noqa: E402
from swarm_python.entrypoints import swarm_handoff  # noqa: E402
from test_plan import decisions_md, feature, feature_md  # noqa: E402


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


class RepoCase(unittest.TestCase):
    """A master checkout with a bare `origin`, a coder worktree and a plan."""

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
        exclude.write_text(".swarmforge/\n.worktrees/\nswarmforge/\n", encoding="utf-8")
        git(self.root, "remote", "add", "origin", str(self.origin))
        git(self.root, "push", "-q", "-u", "origin", "master")
        self.base_head = git(self.root, "rev-parse", "HEAD")

        self.coder = self.root / ".worktrees" / "coder"
        git(self.root, "worktree", "add", "-q", "-B", "swarmforge-coder", str(self.coder), "HEAD")
        (self.coder / ".swarmforge" / "handoffs" / "outbox").mkdir(parents=True)
        self.reviewer = self.root / ".worktrees" / "reviewer"
        git(self.root, "worktree", "add", "-q", "-B", "swarmforge-reviewer", str(self.reviewer), "HEAD")
        (self.reviewer / ".swarmforge" / "handoffs" / "outbox").mkdir(parents=True)
        (self.root / ".swarmforge" / "handoffs" / "outbox").mkdir(parents=True)

        self.plan_dir = self.root / ".swarmforge" / "plan"
        self.write_plan([feature("a"), feature("b", ["a"])])
        self.write_roles_tsv()

    def write_plan(self, features, **extra):
        plan = {"id": "mod", "title": "Module", "status": "approved", "general_file": "general.md",
                "base_branch": "master", "features": features}
        plan.update(extra)
        plan.setdefault("flow_version", planlib.FLOW_VERSION)
        (self.plan_dir / "features").mkdir(parents=True, exist_ok=True)
        (self.plan_dir / "general.md").write_text("overview\n", encoding="utf-8")
        for f in features:
            (self.plan_dir / "features" / f"{f['id']}.md").write_text(feature_md(f["id"]), encoding="utf-8")
        (self.plan_dir / "decisions.md").write_text(decisions_md(), encoding="utf-8")
        planlib.save(self.plan_dir, plan)
        planlib.record_approval(self.plan_dir)

    def write_roles_tsv(self):
        rows = [
            ("specifier", "master", str(self.root)),
            ("coder", "coder", str(self.coder)),
            ("reviewer", "reviewer", str(self.reviewer)),
        ]
        (self.root / ".swarmforge" / "roles.tsv").write_text(
            "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")

    # helpers

    def feat(self, fid):
        return planlib.feature_by_id(planlib.load(self.plan_dir), fid)

    def commit_in_coder(self, name, text, message):
        (self.coder / name).write_text(text, encoding="utf-8")
        git(self.coder, "add", name)
        git(self.coder, "commit", "-q", "-m", message)
        return git(self.coder, "rev-parse", "HEAD")

    def commit_from_another_history(self):
        """A commit that does not descend from the base (an orphan branch)."""
        git(self.coder, "checkout", "-q", "--orphan", "other-history")
        git(self.coder, "rm", "-q", "-rf", ".")
        (self.coder / "other.py").write_text("other\n", encoding="utf-8")
        git(self.coder, "add", "other.py")
        git(self.coder, "commit", "-q", "-m", "a commit from another history")
        commit = git(self.coder, "rev-parse", "HEAD")
        git(self.coder, "checkout", "-q", "swarmforge-coder")
        return commit

    def merge_squash_on_origin(self, content="merged\n"):
        """What a human does when merging the PR: the base branch gets the
        content of the feature, but NOT the feature's history (a squash)."""
        other = Path(self._tmp.name) / "other"
        git(Path(self._tmp.name), "clone", "-q", str(self.origin), str(other))
        git(other, "config", "user.email", "t@example.com")
        git(other, "config", "user.name", "t")
        (other / "src").mkdir(exist_ok=True)
        (other / "src" / "a.py").write_text(content, encoding="utf-8")
        git(other, "add", "src/a.py")
        git(other, "commit", "-q", "-m", "squash merged feature")
        git(other, "push", "-q", "origin", "master")
        tip = git(other, "rev-parse", "HEAD")
        git(self.root, "fetch", "-q", "origin")
        return tip


class EnsureDescendsTests(RepoCase):
    def test_a_worktree_on_the_base_is_left_alone(self):
        ok, message = worktree_ops.ensure_descends(str(self.coder), "swarmforge-coder", self.base_head)
        self.assertTrue(ok, message)
        self.assertEqual(message, "")
        self.assertEqual(git(self.coder, "rev-parse", "HEAD"), self.base_head)

    def test_a_worktree_behind_the_base_is_fast_forwarded(self):
        tip = self.merge_squash_on_origin()
        ok, message = worktree_ops.ensure_descends(str(self.coder), "swarmforge-coder", tip)
        self.assertTrue(ok, message)
        self.assertEqual(git(self.coder, "rev-parse", "HEAD"), tip)

    def test_a_worktree_with_the_pre_merge_history_is_reset_with_a_backup_ref(self):
        # The coder built on its own history; the base got the same content as
        # a squash merge. The content is identical, so the branch is reset and
        # the old commits are kept in a backup ref.
        (self.coder / "src").mkdir(exist_ok=True)
        self.commit_in_coder("src/a.py", "merged\n", "feature a")
        tip = self.merge_squash_on_origin("merged\n")
        ok, message = worktree_ops.ensure_descends(str(self.coder), "swarmforge-coder", tip)
        self.assertTrue(ok, message)
        self.assertEqual(git(self.coder, "rev-parse", "HEAD"), tip)
        refs = git(self.root, "for-each-ref", "--format=%(refname) %(objectname)",
                   "refs/swarmforge/backup/coder/")
        self.assertEqual(len(refs.splitlines()), 1, refs)

    def test_a_worktree_with_its_own_work_is_refused(self):
        self.commit_in_coder("mine.py", "mine\n", "work that is not in the base")
        tip = self.merge_squash_on_origin("something else\n")
        ok, message = worktree_ops.ensure_descends(str(self.coder), "swarmforge-coder", tip)
        self.assertFalse(ok)
        self.assertIn("do not descend", message)
        self.assertIn("rebase", message)
        # Nothing was touched: the work is still there.
        self.assertTrue((self.coder / "mine.py").exists())
        self.assertNotEqual(git(self.coder, "rev-parse", "HEAD"), tip)

    def test_a_dirty_worktree_that_must_be_moved_is_refused_and_nothing_is_lost(self):
        tip = self.merge_squash_on_origin()
        (self.coder / "f.txt").write_text("half done\n", encoding="utf-8")
        ok, message = worktree_ops.ensure_descends(str(self.coder), "swarmforge-coder", tip)
        self.assertFalse(ok)
        self.assertIn("uncommitted changes", message)
        self.assertEqual((self.coder / "f.txt").read_text(encoding="utf-8"), "half done\n")
        self.assertNotEqual(git(self.coder, "rev-parse", "HEAD"), tip)

    def test_the_swarm_owned_copies_never_make_a_worktree_dirty(self):
        (self.coder / "swarmforge" / "scripts").mkdir(parents=True)
        (self.coder / "swarmforge" / "scripts" / "x.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        ok, message = worktree_ops.ensure_descends(str(self.coder), "swarmforge-coder", self.base_head)
        self.assertTrue(ok, message)

    def test_a_worktree_on_another_branch_is_left_alone(self):
        tip = self.merge_squash_on_origin()
        git(self.coder, "checkout", "-q", "-b", "side-work")
        ok, message = worktree_ops.ensure_descends(str(self.coder), "swarmforge-coder", tip)
        self.assertFalse(ok)
        self.assertIn("side-work", message)
        self.assertEqual(git(self.coder, "branch", "--show-current"), "side-work")
        self.assertNotEqual(git(self.coder, "rev-parse", "HEAD"), tip)


class HandoffBaseTests(RepoCase):
    def send(self, commit=None, role="specifier", to="coder", task="a"):
        draft = self.root / "draft.txt"
        draft.write_text(
            f"type: git_handoff\nto: {to}\npriority: 50\ntask: {task}\n"
            f"commit: {commit or git(self.root, 'rev-parse', '--short=10', 'HEAD')}\n",
            encoding="utf-8")
        env = {"SWARMFORGE_ROLE": role, "SWARMFORGE_PLAN_DIR": str(self.plan_dir)}
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(sys, "argv", ["swarm_handoff", str(draft)]), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    swarm_handoff.main()
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def test_the_base_commit_is_the_one_the_handoff_carries(self):
        # The specifier's checkout is stale: the base moved on the remote. The
        # base of the feature must be the commit of the header, not the HEAD
        # of that checkout.
        tip = self.merge_squash_on_origin()
        git(self.root, "reset", "-q", "--hard", self.base_head)  # stale checkout
        code, out, err = self.send(commit=git(self.root, "rev-parse", "--short=10", tip))
        self.assertEqual(code, 0, err)
        self.assertIn("HANDOFF QUEUED", out)
        self.assertEqual(self.feat("a")["base_commit"], tip)
        self.assertEqual(self.feat("a")["status"], "coding")

    def test_a_coder_worktree_behind_the_base_is_fast_forwarded(self):
        tip = self.merge_squash_on_origin()
        code, _, err = self.send(commit=git(self.root, "rev-parse", "--short=10", tip))
        self.assertEqual(code, 0, err)
        self.assertEqual(git(self.coder, "rev-parse", "HEAD"), tip)

    def test_the_handoff_is_refused_when_the_coder_worktree_does_not_descend(self):
        self.commit_in_coder("mine.py", "mine\n", "work that is not in the base")
        tip = self.merge_squash_on_origin("something else\n")
        code, _, err = self.send(commit=git(self.root, "rev-parse", "--short=10", tip))
        self.assertEqual(code, 2)
        self.assertIn("do not descend", err)
        self.assertIn("swarm plan rebase", err)
        # The feature was not reserved and nothing was queued.
        self.assertEqual(self.feat("a")["status"], "pending")
        self.assertEqual(list((self.root / ".swarmforge" / "handoffs" / "outbox").glob("*.handoff")), [])

    def test_the_pre_merge_history_is_reset_when_the_content_is_the_same(self):
        (self.coder / "src").mkdir(exist_ok=True)
        self.commit_in_coder("src/a.py", "merged\n", "feature a")
        tip = self.merge_squash_on_origin("merged\n")
        code, _, err = self.send(commit=git(self.root, "rev-parse", "--short=10", tip))
        self.assertEqual(code, 0, err)
        self.assertEqual(git(self.coder, "rev-parse", "HEAD"), tip)
        self.assertEqual(self.feat("a")["base_commit"], tip)


class ReviewWarningTests(RepoCase):
    def test_the_coder_is_warned_when_its_commit_does_not_descend(self):
        planlib.update_feature(self.plan_dir, "a", base_commit=self.base_head)
        other = self.commit_from_another_history()
        warning = swarm_handoff._descent_warning(self.plan_dir, "a", other)
        self.assertIn("does not descend", warning)
        self.assertIn(self.base_head[:10], warning)

    def test_no_warning_when_the_commit_descends(self):
        planlib.update_feature(self.plan_dir, "a", base_commit=self.base_head)
        commit = self.commit_in_coder("mine.py", "mine\n", "on top of the base")
        self.assertEqual(swarm_handoff._descent_warning(self.plan_dir, "a", commit), "")

    def test_no_warning_without_a_recorded_base(self):
        commit = self.commit_in_coder("mine.py", "mine\n", "on top of the base")
        self.assertEqual(swarm_handoff._descent_warning(self.plan_dir, "a", commit), "")


class RebaseCommandTests(RepoCase):
    def run_cli(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = plan_cli.main(argv, plan_dir=self.plan_dir, env=env or {})
        return code, out.getvalue(), err.getvalue()

    def test_rebase_is_human_only(self):
        code, _, err = self.run_cli(["rebase", "a"], {"SWARMFORGE_ROLE": "specifier"})
        self.assertEqual(code, 3)
        self.assertIn("persona", err)

    def test_rebase_puts_the_coder_on_the_base_and_reopens_the_feature(self):
        self.commit_in_coder("mine.py", "mine\n", "work that is not in the base")
        tip = self.merge_squash_on_origin("something else\n")
        planlib.update_feature(self.plan_dir, "a", final_commit="deadbeef", pr_url="https://x/1")
        planlib.set_feature_status(self.plan_dir, "a", "coding")
        code, out, err = self.run_cli(["rebase", "a"])
        self.assertEqual(code, 0, err)
        self.assertIn("pending", out)
        self.assertEqual(git(self.coder, "rev-parse", "HEAD"), tip)
        feature = self.feat("a")
        self.assertEqual(feature["status"], "pending")
        self.assertNotIn("final_commit", feature)
        self.assertNotIn("pr_url", feature)
        # The commits that were dropped are still reachable.
        refs = git(self.root, "for-each-ref", "refs/swarmforge/backup/coder/")
        self.assertTrue(refs.strip())

    def test_rebase_puts_every_worktree_on_the_base(self):
        # A role left on the old base would review the next feature against
        # the wrong history, so the recovery moves them all.
        tip = self.merge_squash_on_origin("something else\n")
        planlib.set_feature_status(self.plan_dir, "a", "coding")
        code, out, err = self.run_cli(["rebase", "a"])
        self.assertEqual(code, 0, err)
        self.assertEqual(git(self.coder, "rev-parse", "HEAD"), tip)
        self.assertEqual(git(self.reviewer, "rev-parse", "HEAD"), tip)
        self.assertEqual(self.feat("a")["status"], "pending")

    def test_rebase_refuses_when_another_feature_is_in_progress(self):
        self.write_plan([feature("a"), feature("b", ["a"], status="coding")])
        code, _, err = self.run_cli(["rebase", "a"])
        self.assertEqual(code, 1)
        self.assertIn("sequential", err)
        self.assertEqual(self.feat("a")["status"], "pending")

    def test_rebase_of_an_unknown_feature_is_reported(self):
        code, _, err = self.run_cli(["rebase", "zzz"])
        self.assertEqual(code, 1)
        self.assertIn("zzz", err)


if __name__ == "__main__":
    unittest.main()
