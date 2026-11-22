"""swarm start refreshes every role worktree from the project's swarmforge/."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "swarmforge" / "scripts"))

from swarm_python.git_ops import sync_worktree_scripts  # noqa: E402


class WorktreeSyncTest(unittest.TestCase):
    def test_scripts_shims_prompts_and_constitution_reach_the_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sf = root / "swarmforge"
            (sf / "scripts" / "shared-articles").mkdir(parents=True)
            (sf / "roles").mkdir()
            (sf / "constitution" / "articles").mkdir(parents=True)
            (sf / "scripts" / "ready_for_next.sh").write_text("sh", encoding="utf-8")
            (sf / "scripts" / "ready_for_next.cmd").write_text("cmd", encoding="utf-8")
            (sf / "scripts" / "shared-articles" / "handoffs.prompt").write_text("h", encoding="utf-8")
            (sf / "roles" / "coder.prompt").write_text("role", encoding="utf-8")
            (sf / "constitution" / "articles" / "a.prompt").write_text("a", encoding="utf-8")
            (sf / "constitution.prompt").write_text("c", encoding="utf-8")
            state = root / ".swarmforge"
            state.mkdir()
            for name in ("sessions.tsv", "roles.tsv", "herdr-session"):
                (state / name).write_text("x", encoding="utf-8")
            wt = root / ".worktrees" / "coder"
            wt.mkdir(parents=True)
            roles = [SimpleNamespace(worktree_path=wt)]
            sync_worktree_scripts(sf / "scripts", state, roles, state / "sessions.tsv",
                                  state / "roles.tsv", state / "herdr-session")
            self.assertEqual((wt / "swarmforge/scripts/ready_for_next.cmd").read_text(), "cmd")
            self.assertTrue((wt / "swarmforge/scripts/shared-articles/handoffs.prompt").exists())
            self.assertEqual((wt / "swarmforge/roles/coder.prompt").read_text(), "role")
            self.assertTrue((wt / "swarmforge/constitution/articles/a.prompt").exists())
            self.assertEqual((wt / "swarmforge/constitution.prompt").read_text(), "c")


if __name__ == "__main__":
    unittest.main()
