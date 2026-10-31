"""Tests for the executable check of the bundled Gherkin toolchain.

Run from the repo root:  python3 -m unittest discover -s tests/swarm_python
"""

import contextlib
import errno
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "template" / "swarmforge" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swarm_python import deps  # noqa: E402


class ToolchainCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bin = Path(self._tmp.name)

    def fake_elf(self, name):
        path = self.bin / name
        path.write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 32)
        return path

    def host(self, system, machine="x86_64"):
        return mock.patch.multiple(
            "swarm_python.deps.platform", system=lambda: system, machine=lambda: machine)

    def test_missing_tool_is_classified(self):
        with mock.patch("swarm_python.deps.shutil.which", return_value=None):
            status = deps.check_tool(self.bin, "gherkin-parser")
        self.assertEqual(status.status, deps.TOOL_MISSING)
        self.assertFalse(status.ok)

    def test_linux_elf_on_windows_is_a_platform_mismatch_without_running_it(self):
        self.fake_elf("gherkin-parser")
        with self.host("Windows", "AMD64"), mock.patch("swarm_python.deps._run_probe") as run:
            status = deps.check_tool(self.bin, "gherkin-parser")
        run.assert_not_called()
        self.assertEqual(status.status, deps.TOOL_WRONG_PLATFORM)
        self.assertIn("windows/amd64", status.detail)
        self.assertIn("Linux ELF", status.detail)
        self.assertIn("WSL", status.detail)

    def test_wrong_platform_message_says_how_to_fix_it(self):
        self.fake_elf("gherkin-parser")
        with self.host("Windows", "AMD64"):
            status = deps.check_tool(self.bin, "gherkin-parser")
        self.assertIn("swarm-init --update", status.detail)
        self.assertIn("scripts/build_toolchain.sh windows-x64", status.detail)

    def test_platform_file_mismatch_is_mentioned(self):
        bin_dir = self.bin / "toolchain" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir.parent / "PLATFORM").write_text("linux-x64\n", encoding="utf-8")
        (bin_dir / "gherkin-parser").write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 32)
        with self.host("Darwin", "arm64"):
            status = deps.check_tool(bin_dir, "gherkin-parser")
        self.assertIn("installed toolchain is for linux-x64", status.detail)
        self.assertIn("this host is darwin-arm64", status.detail)

    def test_missing_tool_message_points_to_the_fix_without_platform_file(self):
        with self.host("Linux", "aarch64"), mock.patch("swarm_python.deps.shutil.which", return_value=None):
            status = deps.check_tool(self.bin, "gherkin-parser")
        self.assertIn("swarm-init --update", status.detail)
        self.assertIn("build_toolchain.sh linux-arm64", status.detail)
        self.assertNotIn("installed toolchain is for", status.detail)

    def test_host_triple_mapping(self):
        self.assertEqual(deps.host_triple("windows", "amd64"), "windows-x64")
        self.assertEqual(deps.host_triple("darwin", "arm64"), "darwin-arm64")
        self.assertEqual(deps.host_triple("linux", "x86_64"), "linux-x64")
        self.assertEqual(deps.host_triple("windows", "ARM64"), "windows-arm64")
        self.assertEqual(deps.host_triple("windows", "AMD64"), "windows-x64")
        self.assertEqual(deps.host_triple("freebsd", "x86_64"), "")
        self.assertEqual(deps.host_triple("linux", "riscv64"), "")

    def test_linux_elf_on_macos_is_a_platform_mismatch(self):
        self.fake_elf("gherkin-mutator")
        with self.host("Darwin", "arm64"):
            status = deps.check_tool(self.bin, "gherkin-mutator")
        self.assertEqual(status.status, deps.TOOL_WRONG_PLATFORM)
        self.assertIn("darwin/arm64", status.detail)

    def test_exec_format_error_from_the_os_is_classified(self):
        script = self.bin / "gherkin-parser"
        script.write_bytes(b"not an executable\n")
        for error in (OSError(errno.ENOEXEC, "Exec format error"), self.winerror_193()):
            with self.host("Linux"), mock.patch("swarm_python.deps._run_probe", side_effect=error):
                status = deps.check_tool(self.bin, "gherkin-parser")
            self.assertEqual(status.status, deps.TOOL_WRONG_PLATFORM)

    @staticmethod
    def winerror_193():
        error = OSError(8, "%1 is not a valid Win32 application")
        error.winerror = 193
        return error

    def test_permission_error_is_not_executable(self):
        (self.bin / "gherkin-parser").write_bytes(b"#!/bin/sh\n")
        with self.host("Linux"), mock.patch("swarm_python.deps._run_probe", side_effect=PermissionError("denied")):
            status = deps.check_tool(self.bin, "gherkin-parser")
        self.assertEqual(status.status, deps.TOOL_NOT_EXECUTABLE)

    def test_runnable_tool_is_ok_even_when_it_exits_nonzero(self):
        runnable = self.bin / "gherkin-parser.cmd" if sys.platform == "win32" else self.bin / "gherkin-parser"
        if sys.platform == "win32":
            runnable.write_text("@exit /b 1\r\n", encoding="utf-8")
        else:
            runnable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            runnable.chmod(0o755)
        status = deps.check_tool(self.bin, "gherkin-parser")
        self.assertEqual(status.status, deps.TOOL_OK, status.detail)

    def test_sys_executable_stands_in_as_a_runnable_tool(self):
        with mock.patch.object(deps, "resolve_tool", return_value=sys.executable):
            status = deps.check_tool(self.bin, "gherkin-parser")
        self.assertEqual(status.status, deps.TOOL_OK)

    def test_a_tool_that_never_exits_is_killed_and_counts_as_started(self):
        hang = subprocess.TimeoutExpired(["x"], 0.1)
        with self.host("Linux"), mock.patch.object(deps, "resolve_tool", return_value=sys.executable), \
                mock.patch("swarm_python.deps._run_probe", side_effect=hang) as run:
            status = deps.check_tool(self.bin, "gherkin-parser", timeout=0.1)
        self.assertEqual(status.status, deps.TOOL_OK)
        self.assertEqual(run.call_args.args[1], 0.1)

    def test_windows_prefers_a_cmd_or_exe_wrapper_next_to_the_binary(self):
        self.fake_elf("gherkin-parser")
        (self.bin / "gherkin-parser.cmd").write_text("@wsl gherkin-parser %*\r\n", encoding="utf-8")
        self.assertTrue(deps.resolve_tool(self.bin, "gherkin-parser", "windows").endswith("gherkin-parser.cmd"))
        self.assertTrue(deps.resolve_tool(self.bin, "gherkin-parser", "linux").endswith("gherkin-parser"))
        (self.bin / "gherkin-parser.exe").write_bytes(b"MZ")
        self.assertTrue(deps.resolve_tool(self.bin, "gherkin-parser", "windows").endswith("gherkin-parser.exe"))

    def test_wrapper_replaces_the_elf_verdict_on_windows(self):
        self.fake_elf("gherkin-parser")
        wrapper = self.bin / "gherkin-parser.cmd"
        wrapper.write_text("@exit /b 0\r\n", encoding="utf-8")
        with self.host("Windows", "AMD64"), mock.patch("swarm_python.deps._run_probe") as run:
            status = deps.check_tool(self.bin, "gherkin-parser")
        self.assertEqual(status.status, deps.TOOL_OK)
        self.assertEqual(run.call_args.args[0], str(wrapper))

    def test_check_toolchain_reports_every_tool_and_skips_a_missing_directory(self):
        self.assertEqual(deps.check_toolchain(self.bin / "nope"), [])
        for name in deps.TOOLCHAIN_TOOLS:
            self.fake_elf(name)
        with self.host("Windows", "AMD64"):
            statuses = deps.check_toolchain(self.bin)
        self.assertEqual([s.name for s in statuses], list(deps.TOOLCHAIN_TOOLS))
        report = deps.toolchain_report(statuses)
        self.assertEqual(len(report.splitlines()), 3)
        self.assertIn("gherkin-parser [wrong-platform]", report)
        self.assertEqual(deps.toolchain_report([deps.ToolStatus("t", deps.TOOL_OK)]), "")


    def test_probe_timeout_kills_the_process_tree(self):
        proc = mock.MagicMock()
        proc.wait.side_effect = [subprocess.TimeoutExpired(["x"], 1), 0]
        with mock.patch("swarm_python.deps.subprocess.Popen", return_value=proc) as popen, \
                mock.patch("swarm_python.deps._kill_process_tree") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                deps._run_probe("tool", 3)
        kill.assert_called_once_with(proc)
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_default_probe_timeout_is_short(self):
        self.assertLessEqual(deps.TOOL_PROBE_TIMEOUT, 3)

    def test_probes_run_in_parallel(self):
        import threading
        import time
        gate = threading.Barrier(len(deps.TOOLCHAIN_TOOLS), timeout=5)

        def slow(bin_dir, name, **kw):
            gate.wait()  # only passes if every probe is in flight at once
            return deps.ToolStatus(name, deps.TOOL_OK)

        started = time.monotonic()
        with mock.patch.object(deps, "check_tool", side_effect=slow):
            statuses = deps.check_toolchain(self.bin)
        self.assertEqual(len(statuses), 3)
        self.assertLess(time.monotonic() - started, 4)

    def test_path_hit_outside_bin_dir_is_labelled(self):
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        tool = elsewhere / "gherkin-parser"
        tool.write_bytes(b"x")
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        with self.host("Linux"), mock.patch("swarm_python.deps.shutil.which", return_value=str(tool)), \
                mock.patch("swarm_python.deps._run_probe"):
            status = deps.check_tool(bin_dir, "gherkin-parser")
        self.assertTrue(status.from_path)
        self.assertIn("resolved from PATH", status.detail)
        self.assertIn(str(tool), status.detail)


class ReportToolchainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".swarmforge" / "toolchain" / "bin").mkdir(parents=True)
        from swarm_python.paths import build_context
        self.ctx = build_context(self.root, SCRIPTS)

    def run_report(self, statuses):
        from swarm_python.entrypoints import swarm_cli
        out = io.StringIO()
        with mock.patch.object(swarm_cli, "check_toolchain", return_value=statuses), contextlib.redirect_stdout(out):
            swarm_cli.report_toolchain(self.ctx)
        return out.getvalue(), (self.root / ".swarmforge" / "toolchain-check.txt").read_text(encoding="utf-8")

    def test_unusable_tools_warn_and_land_in_the_file(self):
        bad = deps.ToolStatus("gherkin-parser", deps.TOOL_WRONG_PLATFORM, "Linux ELF on windows/amd64", "C:/x/gherkin-parser")
        good = deps.ToolStatus("gherkin-mutator", deps.TOOL_OK, "", "C:/x/gherkin-mutator")
        out, text = self.run_report([bad, good])
        self.assertIn("not usable", out)
        self.assertIn("gherkin-parser [wrong-platform]", out)
        self.assertNotIn("gherkin-mutator", out)
        self.assertIn("UNUSABLE", text)
        self.assertIn("do not generate .feature files", text)
        self.assertIn("gherkin-parser: wrong-platform (C:/x/gherkin-parser)", text)
        self.assertIn("gherkin-mutator: ok", text)

    def test_all_ok_prints_nothing_and_records_ok(self):
        out, text = self.run_report([deps.ToolStatus("gherkin-parser", deps.TOOL_OK, "", "p")])
        self.assertEqual(out, "")
        self.assertTrue(text.startswith("Gherkin toolchain: ok"))


if __name__ == "__main__":
    unittest.main()
