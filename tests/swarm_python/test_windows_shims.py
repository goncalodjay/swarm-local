"""Every bash helper agents call by name needs a `.cmd` shim for PowerShell/cmd,
and the template copy must mirror the source tree."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPERS = ("ready_for_next", "done_with_current", "swarm_handoff")


class WindowsShimTest(unittest.TestCase):
    def test_every_helper_has_a_sibling_cmd_shim(self):
        for base in (ROOT / "swarmforge" / "scripts", ROOT / "template" / "swarmforge" / "scripts"):
            for name in HELPERS:
                self.assertTrue((base / f"{name}.sh").is_file(), f"{base}/{name}.sh")
                self.assertTrue((base / f"{name}.cmd").is_file(), f"missing shim {base}/{name}.cmd")

    def test_shims_are_mirrored_in_the_template(self):
        for name in HELPERS:
            src = (ROOT / "swarmforge" / "scripts" / f"{name}.cmd").read_bytes()
            mirror = (ROOT / "template" / "swarmforge" / "scripts" / f"{name}.cmd").read_bytes()
            self.assertEqual(src, mirror, name)

    def test_shim_runs_the_sibling_script_under_git_bash(self):
        text = (ROOT / "swarmforge" / "scripts" / "ready_for_next.cmd").read_text(encoding="utf-8")
        self.assertIn("%~dpn0.sh", text)
        self.assertIn("where git", text)
        self.assertIn("exit /b %ERRORLEVEL%", text)
        self.assertIn(" %*", text)
        self.assertNotIn("enabledelayedexpansion", text.lower().replace("disabledelayedexpansion", ""))
        self.assertNotIn("System32", text)

    def test_no_agent_facing_text_names_a_helper_with_dot_sh(self):
        sys.path.insert(0, str(ROOT / "swarmforge" / "scripts"))
        from swarm_python import scheduler, sessions
        pattern = re.compile(r"(?:%s)\.sh" % "|".join(HELPERS))
        texts = {
            "WAKE_MESSAGE": scheduler.WAKE_MESSAGE,
            "STALE_MESSAGE": scheduler.STALE_MESSAGE,
            "RESUME_PROMPT": sessions.RESUME_PROMPT,
        }
        for base in (ROOT / "swarmforge", ROOT / "template" / "swarmforge"):
            for path in [*(base / "roles").glob("*.prompt"),
                         *(base / "scripts" / "shared-articles").glob("*.prompt"),
                         base / "constitution.prompt"]:
                if path.exists():
                    texts[str(path)] = path.read_text(encoding="utf-8")
        for name, text in texts.items():
            self.assertIsNone(pattern.search(text), f"{name} names a helper with .sh")

    def test_posix_wrappers_exec_the_sh_scripts(self):
        from swarm_python.launch import write_posix_helper_wrappers
        with tempfile.TemporaryDirectory() as d:
            bin_dir = Path(d) / "bin"
            write_posix_helper_wrappers(bin_dir, Path(d) / "scripts")
            for name in HELPERS:
                body = (bin_dir / name).read_text(encoding="utf-8")
                self.assertTrue(body.startswith("#!/usr/bin/env bash\nexec "))
                self.assertIn(f"{name}.sh", body)

    def test_the_coder_owns_feature_files_not_the_specifier(self):
        for base in (ROOT / "swarmforge", ROOT / "template" / "swarmforge"):
            specifier = (base / "roles" / "specifier.prompt").read_text(encoding="utf-8")
            coder = (base / "roles" / "coder.prompt").read_text(encoding="utf-8")
            self.assertNotIn("also write `features/<id>.feature`", specifier)
            self.assertNotIn("Commit any `.feature` files first", specifier)
            self.assertIn("NEVER create or commit `.feature` files", specifier)
            self.assertIn("create `features/<feature-id>.feature`", coder)


def _git_bash():
    git = shutil.which("git")
    if git:
        for rel in ("../bin/bash.exe", "../../bin/bash.exe"):
            candidate = (Path(git).resolve().parent / rel).resolve()
            if candidate.exists():
                return candidate
    return None


@unittest.skipUnless(os.name == "nt" and _git_bash(), "needs Windows with Git Bash")
class WindowsShimBehaviorTest(unittest.TestCase):
    """Run each real shim next to a stub .sh and look at what the script saw."""

    STUB = (
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do printf \'[%s]\\n\' "$a"; done\n'
        'exit "${STUB_EXIT:-0}"\n'
    )

    def run_shim(self, name, args, exit_code=0):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            shim = folder / f"{name}.cmd"
            shim.write_bytes((ROOT / "swarmforge" / "scripts" / f"{name}.cmd").read_bytes())
            (folder / f"{name}.sh").write_bytes(self.STUB.encode())
            env = dict(os.environ, STUB_EXIT=str(exit_code))
            done = subprocess.run([str(shim), *args], capture_output=True, env=env, timeout=60)
            seen = done.stdout.decode("utf-8").replace("\r", "").split("\n")
            return done.returncode, [s for s in seen if s]

    def test_arguments_arrive_untouched_for_every_shim(self):
        args = ["hello!", "a b", "", "after", "C:\\foo\\bar", "line\\nbreak", "100%", "x y&z"]
        for name in HELPERS:
            code, seen = self.run_shim(name, args)
            self.assertEqual(code, 0, name)
            self.assertEqual(seen, [f"[{a}]" for a in args], name)

    def test_the_exit_code_is_propagated(self):
        for name in HELPERS:
            code, _ = self.run_shim(name, ["x"], exit_code=7)
            self.assertEqual(code, 7, name)


if __name__ == "__main__":
    unittest.main()
