"""End-to-end tests for `swarm-init --update` against throwaway git repos.

The update logic lives in bash, so each test builds a tiny project (a real
`git init` repo holding a fake install), runs the real script through
subprocess, and inspects the filesystem afterwards.
"""

import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "template"
SWARM_INIT = REPO / "swarm-init"

BASE_ROWS = [
    "window specifier opencode master --model opencode-go/kimi --auto",
    "window coder opencode coder --model opencode-go/flash --auto",
    "window reviewer pi reviewer --model openai/x --thinking medium",
    "window architect opencode architect batch --model opencode-go/minimax --auto",
]
STALE_ROW = "window refactorer codex refactorer --model gpt-5.6-luna --yolo"
STALE_MARKER = "STALE-ROLE-PROMPT-MARKER"


def find_bash():
    """Prefer Git Bash: on Windows, `bash` on PATH is often the WSL launcher."""
    git_exe = shutil.which("git")
    if git_exe:
        for rel in ("../bin/bash.exe", "../../bin/bash.exe", "../usr/bin/bash.exe"):
            candidate = (Path(git_exe).parent / rel).resolve()
            if candidate.exists():
                return str(candidate)
    return shutil.which("bash")


BASH = find_bash()


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def run_update(project, *args, stdin="", assume_tty=False, extra_env=None):
    env = dict(os.environ)
    env.update(extra_env or {})
    if assume_tty:
        env["SWARMFORGE_ASSUME_TTY"] = "1"
    else:
        env.pop("SWARMFORGE_ASSUME_TTY", None)
    return subprocess.run(
        [BASH, SWARM_INIT.as_posix(), "--update", *args, Path(project).as_posix()],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


@unittest.skipUnless(BASH and shutil.which("git"), "needs bash and git")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def make_install(self, stale=False):
        """Fake install committed in git: stale role prompt text, optional stale role."""
        root = self.project
        git(root, "init", "-q", "-b", "master")
        shutil.copytree(TEMPLATE / "swarmforge", root / "swarmforge", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(TEMPLATE / "swarm", root / "swarm")
        shutil.copy(TEMPLATE / "swarm.cmd", root / "swarm.cmd")
        for path in (root / "swarmforge").rglob("*"):
            if path.is_file() and path.suffix == ".prompt":
                text = path.read_text(encoding="utf-8").replace("{{LANGUAGES}}", "Go")
                path.write_text(text, encoding="utf-8", newline="\n")
        (root / "swarmforge" / "roles" / "coder.prompt").write_text(STALE_MARKER + "\n", encoding="utf-8", newline="\n")
        rows = list(BASE_ROWS)
        if stale:
            rows.insert(2, STALE_ROW)
            (root / "swarmforge" / "roles" / "refactorer.prompt").write_text("mine\n", encoding="utf-8", newline="\n")
        (root / "swarmforge" / "swarmforge.conf").write_text("# header\n" + "\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        state = root / ".swarmforge"
        (state / "prompts").mkdir(parents=True)
        (state / "handoffs").mkdir()
        (state / "handoffs" / "keep.txt").write_text("keep", encoding="utf-8")
        for role in ("coder", "refactorer"):
            (state / "prompts" / f"{role}.md").write_text(role, encoding="utf-8")
            (state / "prompts" / f"{role}.sh").write_text(role, encoding="utf-8")
        (state / "agents.json").write_text(
            '{"coder": {"task": "t1"}, "refactorer": {"task": "t2"}}\n', encoding="utf-8"
        )
        (state / "roles.tsv").write_text(
            "coder\tcoder\t/x\ts\tC\topencode\ttask\tw1:p1\tw1\n"
            "refactorer\trefactorer\t/x\ts\tR\tcodex\ttask\tw2:p1\tw2\n",
            encoding="utf-8",
        )
        (root / ".gitignore").write_text(".swarmforge/\n.worktrees/\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "install")

    def conf_text(self):
        return (self.project / "swarmforge" / "swarmforge.conf").read_text(encoding="utf-8")

    def coder_prompt(self):
        return (self.project / "swarmforge" / "roles" / "coder.prompt").read_text(encoding="utf-8")

    def test_clean_update_replaces_role_prompts_and_constitution(self):
        self.make_install()
        result = run_update(self.project, "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(STALE_MARKER, self.coder_prompt())
        project_prompt = (self.project / "swarmforge/constitution/articles/project.prompt").read_text(encoding="utf-8")
        self.assertIn("Go", project_prompt)
        self.assertNotIn("{{LANGUAGES}}", project_prompt)
        self.assertEqual(self.conf_text().count("window "), 4)
        self.assertFalse((self.project / "swarmforge" / "swarmforge.conf.bak").exists())
        self.assertTrue((self.project / ".swarmforge/handoffs/keep.txt").exists())

    def test_reconcile_keeps_known_rows_and_removes_stale_role(self):
        self.make_install(stale=True)
        result = run_update(self.project, "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        conf = self.conf_text()
        self.assertNotIn("refactorer", conf)
        for row in BASE_ROWS:
            self.assertIn(row, conf)
        bak = (self.project / "swarmforge" / "swarmforge.conf.bak").read_text(encoding="utf-8")
        self.assertIn(STALE_ROW, bak)
        state = self.project / ".swarmforge"
        self.assertFalse((state / "prompts" / "refactorer.md").exists())
        self.assertTrue((state / "prompts" / "coder.md").exists())
        backups = list(state.glob("backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / ".swarmforge/prompts/refactorer.md").exists())
        agents = (state / "agents.json").read_text(encoding="utf-8")
        self.assertIn('"coder"', agents)
        self.assertNotIn("refactorer", agents)
        self.assertNotIn("refactorer", (state / "roles.tsv").read_text(encoding="utf-8"))
        # The user's own prompt for the unknown role is never deleted.
        self.assertTrue((self.project / "swarmforge/roles/refactorer.prompt").exists())

    def test_second_backup_gets_timestamped_name(self):
        self.make_install(stale=True)
        (self.project / "swarmforge" / "swarmforge.conf.bak").write_text("old", encoding="utf-8")
        result = run_update(self.project, "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.project / "swarmforge" / "swarmforge.conf.bak").read_text(encoding="utf-8"), "old")
        self.assertEqual(len(list((self.project / "swarmforge").glob("swarmforge.conf.bak.*"))), 1)

    def test_dirty_template_file_blocks_without_yes(self):
        self.make_install()
        dirty = self.project / "swarmforge" / "roles" / "coder.prompt"
        dirty.write_text("my local edits\n", encoding="utf-8", newline="\n")
        result = run_update(self.project, "--languages", "Go")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("swarmforge/roles/coder.prompt", result.stderr)
        self.assertEqual(self.coder_prompt(), "my local edits\n")
        self.assertEqual(list((self.project / ".swarmforge").glob("backup-*")), [])
        self.assertFalse((self.project / "swarmforge" / "swarmforge.conf.bak").exists())

    def test_dirty_template_file_with_yes_backs_up_then_replaces(self):
        self.make_install()
        dirty = self.project / "swarmforge" / "roles" / "coder.prompt"
        dirty.write_text("my local edits\n", encoding="utf-8", newline="\n")
        result = run_update(self.project, "--yes", "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("my local edits", self.coder_prompt())
        backups = list((self.project / ".swarmforge").glob("backup-*"))
        self.assertEqual(len(backups), 1)
        saved = backups[0] / "swarmforge" / "roles" / "coder.prompt"
        self.assertEqual(saved.read_text(encoding="utf-8"), "my local edits\n")

    def test_dirty_worktree_blocks_and_is_never_modified(self):
        self.make_install()
        git(self.project, "worktree", "add", "-q", ".worktrees/coder", "-b", "swarmforge-coder")
        wip = self.project / ".worktrees" / "coder" / "wip.txt"
        wip.write_text("unsaved work", encoding="utf-8")
        result = run_update(self.project, "--languages", "Go")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".worktrees/coder", result.stderr)
        self.assertIn(STALE_MARKER, self.coder_prompt())
        forced = run_update(self.project, "--yes", "--languages", "Go")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(wip.read_text(encoding="utf-8"), "unsaved work")

    def test_stale_role_without_tty_or_yes_aborts_untouched(self):
        self.make_install(stale=True)
        before = self.conf_text()
        result = run_update(self.project)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.conf_text(), before)
        self.assertIn(STALE_MARKER, self.coder_prompt())

    def test_answering_no_aborts_untouched(self):
        self.make_install(stale=True)
        before = self.conf_text()
        result = run_update(self.project, stdin="n\n", assume_tty=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.conf_text(), before)
        self.assertIn(STALE_MARKER, self.coder_prompt())
        blank = run_update(self.project, stdin="\n", assume_tty=True)
        self.assertNotEqual(blank.returncode, 0)

    def test_answering_yes_proceeds(self):
        self.make_install(stale=True)
        result = run_update(self.project, stdin="s\nn\n", assume_tty=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("refactorer", self.conf_text())

    def test_new_template_role_without_tty_aborts(self):
        self.make_install()
        conf = self.project / "swarmforge" / "swarmforge.conf"
        rows = [r for r in BASE_ROWS if " reviewer " not in r]
        conf.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        git(self.project, "commit", "-qam", "drop reviewer")
        result = run_update(self.project, "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("reviewer", self.conf_text())
        self.assertIn(STALE_MARKER, self.coder_prompt())

    def test_update_installs_the_toolchain_for_the_host_and_records_it(self):
        self.make_install()
        result = run_update(self.project, "--yes", extra_env={"SWARMFORGE_PLATFORM": "windows-x64"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tc = self.project / ".swarmforge" / "toolchain"
        self.assertEqual((tc / "PLATFORM").read_text(encoding="utf-8").strip(), "windows-x64")
        self.assertTrue((tc / "bin" / "gherkin-parser.exe").is_file())
        self.assertFalse((tc / "bin" / "gherkin-parser").exists())

    def test_update_reinstalls_toolchain_when_platform_changed(self):
        self.make_install()
        old = self.project / ".swarmforge" / "toolchain"
        (old / "bin").mkdir(parents=True)
        (old / "bin" / "gherkin-parser.exe").write_bytes(b"MZ-old")
        (old / "PLATFORM").write_text("windows-x64\n", encoding="utf-8")
        result = run_update(self.project, "--yes", extra_env={"SWARMFORGE_PLATFORM": "linux-x64"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((old / "PLATFORM").read_text(encoding="utf-8").strip(), "linux-x64")
        self.assertTrue((old / "bin" / "gherkin-parser").is_file())
        self.assertFalse((old / "bin" / "gherkin-parser.exe").exists())

    def test_gitignore_entries_are_appended_never_removed(self):
        self.make_install()
        ignore = self.project / ".gitignore"
        ignore.write_text("node_modules/\n", encoding="utf-8")
        git(self.project, "commit", "-qam", "ignore")
        result = run_update(self.project, "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = ignore.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "node_modules/")
        self.assertIn(".swarmforge/", lines)
        self.assertIn(".worktrees/", lines)




def bash_function(name):
    """Source text of one top-level function of swarm-init."""
    text = SWARM_INIT.read_text(encoding="utf-8")
    start = text.index(name + "() {")
    end = text.index("\n}\n", start) + 2
    return text[start:end]


def run_bash(script):
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60)


@unittest.skipUnless(BASH and shutil.which("git"), "needs bash and git")
class SafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    make_install = UpdateTests.make_install
    coder_prompt = UpdateTests.coder_prompt

    def spawn_sleeper(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        self.addCleanup(proc.kill)
        return proc

    def write_pid(self, pid):
        daemon = self.project / ".swarmforge" / "daemon"
        daemon.mkdir(parents=True, exist_ok=True)
        (daemon / "handoffd.pid").write_text(str(pid) + "\n", encoding="utf-8")

    def test_running_swarm_detected_with_live_native_pid(self):
        self.make_install()
        proc = self.spawn_sleeper()
        self.write_pid(proc.pid)
        result = run_update(self.project, "--yes", "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("en ejecuci", result.stdout + result.stderr)
        self.assertIn(f"pid {proc.pid}", result.stdout + result.stderr)

    def test_dead_pid_is_not_a_running_swarm(self):
        self.make_install()
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.write_pid(proc.pid)
        result = run_update(self.project, "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("en ejecuci", result.stdout + result.stderr)

    def test_clean_update_always_backs_up_owned_paths(self):
        self.make_install()
        result = run_update(self.project, "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list((self.project / ".swarmforge").glob("backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "swarmforge/roles/coder.prompt").read_text(encoding="utf-8"), STALE_MARKER + "\n")
        self.assertTrue((backups[0] / "swarmforge/scripts").is_dir())
        self.assertTrue((backups[0] / "swarm").is_file())

    def test_gitignored_owned_file_blocks_and_is_backed_up(self):
        self.make_install()
        extra = self.project / "swarmforge" / "scripts" / "local_hook.sh"
        extra.write_text("echo mine\n", encoding="utf-8", newline="\n")
        (self.project / ".gitignore").write_text(".swarmforge/\n.worktrees/\nlocal_hook.sh\n", encoding="utf-8")
        git(self.project, "commit", "-qam", "ignore hook")
        blocked = run_update(self.project, "--languages", "Go")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("local_hook.sh", blocked.stderr + blocked.stdout)
        self.assertTrue(extra.exists())
        result = run_update(self.project, "--yes", "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list((self.project / ".swarmforge").glob("backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "swarmforge/scripts/local_hook.sh").exists())

    def test_non_git_target_is_treated_as_at_risk(self):
        self.make_install()

        def force_remove(func, path, _exc):
            os.chmod(path, 0o700)
            func(path)

        shutil.rmtree(self.project / ".git", onerror=force_remove)
        blocked = run_update(self.project, "--languages", "Go")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("git", blocked.stdout + blocked.stderr)
        self.assertIn(STALE_MARKER, self.coder_prompt())
        result = run_update(self.project, "--yes", "--languages", "Go")
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list((self.project / ".swarmforge").glob("backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "swarmforge/roles/coder.prompt").read_text(encoding="utf-8"), STALE_MARKER + "\n")

    def test_replace_dir_swaps_and_leaves_no_old_copy(self):
        root = Path(self._tmp.name)
        (root / "src").mkdir()
        (root / "src" / "new.txt").write_text("new", encoding="utf-8")
        (root / "dest").mkdir()
        (root / "dest" / "old.txt").write_text("old", encoding="utf-8")
        script = bash_function("replace_dir") + "\n" + f'replace_dir "{(root / "src").as_posix()}" "{(root / "dest").as_posix()}"'
        result = run_bash(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((root / "dest" / "new.txt").exists())
        self.assertFalse((root / "dest" / "old.txt").exists())
        self.assertFalse((root / "dest.old").exists())

    def test_replace_dir_restores_original_when_copy_fails(self):
        root = Path(self._tmp.name)
        (root / "dest").mkdir()
        (root / "dest" / "old.txt").write_text("old", encoding="utf-8")
        script = bash_function("replace_dir") + "\n" + f'replace_dir "{(root / "missing-src").as_posix()}" "{(root / "dest").as_posix()}"'
        result = run_bash(script)
        self.assertEqual(result.returncode, 1)
        self.assertIn("restaur", result.stderr)
        self.assertEqual((root / "dest" / "old.txt").read_text(encoding="utf-8"), "old")
        self.assertFalse((root / "dest.old").exists())

    def test_catalog_providers_group_non_contiguous_rows(self):
        rows = "\n".join(
            [
                "openai\tOpenAI\tgpt-5\tGPT 5",
                "anthropic\tAnthropic\tsonnet\tSonnet",
                "openai\tOpenAI\tgpt-6\tGPT 6",
                "anthropic\tAnthropic\topus\tOpus",
                "x\t\t\t",
            ]
        )
        script = bash_function("_catalog_providers") + "\n" + f"printf '%s\n' '{rows}' | _catalog_providers"
        result = run_bash(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["openai\tOpenAI", "anthropic\tAnthropic"])


@unittest.skipUnless(BASH, "needs bash")
class ToolchainInstallTests(unittest.TestCase):
    """install_toolchain against a fake template (no real binaries)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.template = self.root / "template"
        for triple, suffix in (("linux-x64", ""), ("windows-x64", ".exe")):
            bin_dir = self.template / "toolchain" / triple / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / f"gherkin-parser{suffix}").write_text(triple, encoding="utf-8")
        self.state = self.root / "proj" / ".swarmforge"
        self.builder = self.root / "fake_builder.sh"
        (self.root / "vendor" / "acceptance-pipeline-specification").mkdir(parents=True)
        self.builder.write_text(
            '#!/usr/bin/env bash\nmkdir -p "$2"\nsfx=""\n[[ "$1" == windows-* ]] && sfx=.exe\n'
            'for t in gherkin-parser gherkin-mutator gherkin-ir-dry-checker; do printf built-%s "$1" >"$2/$t$sfx"; done\n',
            encoding="utf-8", newline="\n")
        self.chmod_log = self.root / "chmod.log"

    def seed_existing(self, platform="linux-x64"):
        bin_dir = self.tc("bin")
        bin_dir.mkdir(parents=True)
        (bin_dir / "gherkin-parser").write_text("existing", encoding="utf-8")
        (self.tc("PLATFORM")).write_text(platform + "\n", encoding="utf-8")

    def install(self, platform, go=True, builder=None):
        script = "\n".join(
            [
                'mprintf() { printf "$@"; }',
                bash_function("replace_dir"),
                bash_function("host_triple"),
                bash_function("install_toolchain"),
                f"have_go() {{ {'true' if go else 'false'}; }}",
                f'chmod() {{ echo "$@" >>"{self.chmod_log.as_posix()}"; }}',
                f'TEMPLATE_DIR="{self.template.as_posix()}"',
                f'SCRIPT_DIR="{self.root.as_posix()}"',
                f'export SWARMFORGE_BUILD_TOOLCHAIN="{(builder or self.builder).as_posix()}"',
                f'export SWARMFORGE_PLATFORM="{platform}"',
                f'install_toolchain "{self.state.as_posix()}"',
            ]
        )
        return run_bash(script)

    def tc(self, *parts):
        return self.state.joinpath("toolchain", *parts)

    def test_picks_the_prebuilt_for_the_platform(self):
        for triple, name in (("linux-x64", "gherkin-parser"), ("windows-x64", "gherkin-parser.exe")):
            result = self.install(triple)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.tc("PLATFORM").read_text(encoding="utf-8").strip(), triple)
            self.assertEqual(self.tc("bin", name).read_text(encoding="utf-8"), triple)
            self.assertEqual(sorted(p.name for p in self.tc("bin").iterdir()), [name])
            self.assertFalse(self.state.joinpath("toolchain.old").exists())

    def test_reinstalls_when_the_recorded_platform_differs(self):
        self.install("linux-x64")
        self.install("windows-x64")
        self.assertEqual(self.tc("PLATFORM").read_text(encoding="utf-8").strip(), "windows-x64")
        self.assertFalse(self.tc("bin", "gherkin-parser").exists())

    def test_missing_triple_builds_from_source_when_go_exists(self):
        result = self.install("darwin-arm64", go=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tc("bin", "gherkin-parser").read_text(encoding="utf-8"), "built-darwin-arm64")
        self.assertEqual(len(list(self.tc("bin").iterdir())), 3)
        self.assertEqual(self.tc("PLATFORM").read_text(encoding="utf-8").strip(), "darwin-arm64")

    def test_missing_triple_without_go_warns_and_does_not_fail(self):
        result = self.install("darwin-arm64", go=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Go no est", result.stderr)
        self.assertIn("darwin-arm64", result.stderr)
        self.assertIn("build_toolchain.sh darwin-arm64", result.stderr)
        self.assertFalse(self.tc().exists())

    def test_without_go_an_existing_toolchain_is_kept(self):
        self.seed_existing()
        result = self.install("darwin-arm64", go=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tc("bin", "gherkin-parser").read_text(encoding="utf-8"), "existing")
        self.assertEqual(self.tc("PLATFORM").read_text(encoding="utf-8").strip(), "linux-x64")
        self.assertIn("conserv", result.stderr)

    def test_failed_build_is_a_warning_not_an_error(self):
        self.seed_existing()
        bad = self.root / "bad_builder.sh"
        bad.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n")
        result = self.install("darwin-x64", go=True, builder=bad)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("la compilaci", result.stderr)
        self.assertIn("build_toolchain.sh darwin-x64", result.stderr)
        self.assertEqual(self.tc("bin", "gherkin-parser").read_text(encoding="utf-8"), "existing")
        self.assertEqual(self.tc("PLATFORM").read_text(encoding="utf-8").strip(), "linux-x64")

    def test_partial_build_is_not_installed(self):
        partial = self.root / "partial_builder.sh"
        partial.write_text('#!/usr/bin/env bash\nmkdir -p "$2"\nprintf x >"$2/gherkin-parser"\n',
                           encoding="utf-8", newline="\n")
        result = self.install("darwin-x64", go=True, builder=partial)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.tc().exists())

    def test_missing_builder_or_vendor_sources_are_reported_not_installed(self):
        result = self.install("darwin-x64", go=True, builder=self.root / "nope.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("faltan scripts/build_toolchain.sh", result.stderr)
        self.assertFalse(self.tc().exists())
        shutil.rmtree(self.root / "vendor")
        result = self.install("darwin-x64", go=True)
        self.assertIn("faltan scripts/build_toolchain.sh", result.stderr)
        self.assertFalse(self.tc().exists())

    def test_rerun_with_matching_platform_is_idempotent(self):
        self.install("windows-x64")
        result = self.install("windows-x64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in self.tc("bin").iterdir()), ["gherkin-parser.exe"])
        self.assertEqual(self.tc("PLATFORM").read_text(encoding="utf-8").strip(), "windows-x64")
        self.assertFalse(self.state.joinpath("toolchain.old").exists())

    def test_old_flat_layout_without_platform_file_is_replaced(self):
        old = self.tc("bin")
        old.mkdir(parents=True)
        (old / "gherkin-parser").write_text("legacy", encoding="utf-8")
        (old / "gherkin-legacy-only").write_text("legacy", encoding="utf-8")
        result = self.install("windows-x64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in old.iterdir()), ["gherkin-parser.exe"])
        self.assertEqual(self.tc("PLATFORM").read_text(encoding="utf-8").strip(), "windows-x64")

    def test_chmod_runs_on_unix_triples_and_is_skipped_on_windows(self):
        self.install("windows-x64")
        self.assertNotIn("+x", self.chmod_log.read_text(encoding="utf-8") if self.chmod_log.exists() else "")
        self.install("linux-x64")
        self.assertIn("+x", self.chmod_log.read_text(encoding="utf-8"))

    def host_triple_with(self, uname_s, uname_m, **env):
        script = "\n".join(
            [
                bash_function("host_triple"),
                f'uname() {{ if [[ "$1" == -s ]]; then echo {uname_s}; else echo {uname_m}; fi; }}',
                "unset SWARMFORGE_PLATFORM PROCESSOR_ARCHITEW6432 PROCESSOR_ARCHITECTURE",
                *[f'export {k}="{v}"' for k, v in env.items()],
                "host_triple",
            ]
        )
        return run_bash(script)

    def test_windows_arch_comes_from_the_windows_environment(self):
        cases = [
            ({"PROCESSOR_ARCHITECTURE": "ARM64"}, "windows-arm64"),
            ({"PROCESSOR_ARCHITEW6432": "ARM64", "PROCESSOR_ARCHITECTURE": "AMD64"}, "windows-arm64"),
            ({"PROCESSOR_ARCHITECTURE": "AMD64"}, "windows-x64"),
            ({}, "windows-x64"),  # falls back to uname -m
        ]
        for env, expected in cases:
            result = self.host_triple_with("MINGW64_NT-10.0", "x86_64", **env)
            self.assertEqual(result.stdout.strip(), expected, (env, result.stderr))

    def test_non_windows_arch_comes_from_uname(self):
        self.assertEqual(self.host_triple_with("Darwin", "arm64").stdout.strip(), "darwin-arm64")
        self.assertEqual(self.host_triple_with("Linux", "aarch64", PROCESSOR_ARCHITECTURE="AMD64").stdout.strip(),
                         "linux-arm64")

    def test_invalid_platform_override_installs_nothing_but_does_not_fail(self):
        result = self.install("plan9-x64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no es v", result.stderr)
        self.assertFalse(self.tc("PLATFORM").exists())


@unittest.skipUnless(BASH, "needs bash")
class BuildToolchainScriptTests(unittest.TestCase):
    script = REPO / "scripts" / "build_toolchain.sh"

    def dry_run(self, *args):
        return subprocess.run([BASH, self.script.as_posix(), "--dry-run", *args],
                              capture_output=True, text=True, timeout=60)

    def test_triple_maps_to_goos_and_goarch(self):
        cases = {
            "windows-x64": ("windows", "amd64", ".exe"),
            "windows-arm64": ("windows", "arm64", ".exe"),
            "darwin-arm64": ("darwin", "arm64", ""),
            "darwin-x64": ("darwin", "amd64", ""),
            "linux-x64": ("linux", "amd64", ""),
            "linux-arm64": ("linux", "arm64", ""),
        }
        for triple, (goos, goarch, suffix) in cases.items():
            result = self.dry_run(triple, "/out/bin")
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.strip().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn(f"GOOS={goos} GOARCH={goarch} go build", lines[0])
            self.assertIn("CGO_ENABLED=0", lines[0])
            self.assertIn(f"-o /out/bin/gherkin-parser{suffix} ./cmd/gherkin-parser", lines[0])

    def test_all_covers_every_triple(self):
        result = self.dry_run("--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip().splitlines()), 18)
        self.assertIn("template/toolchain/windows-x64/bin/gherkin-mutator.exe", result.stdout)

    def test_unsupported_triple_fails(self):
        for bad in ("freebsd-x64", "linux-riscv", "linux"):
            result = self.dry_run(bad)
            self.assertEqual(result.returncode, 1, bad)
            self.assertIn("Unsupported triple", result.stderr)


if __name__ == "__main__":
    unittest.main()
