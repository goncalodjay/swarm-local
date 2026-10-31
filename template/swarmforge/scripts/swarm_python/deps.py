import errno
import os
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .agent_probe import _kill_process_tree
from .ansi import RED, RESET


def command_exists(name):
    return shutil.which(name) is not None


def require(command):
    if not command_exists(command):
        print(
            f"{RED}Error:{RESET} '{command}' is required but not installed.",
            file=sys.stderr,
        )
        sys.exit(1)


def check_backends(roles):
    seen = set()
    for r in roles:
        if r.agent not in seen:
            require(r.agent)
            seen.add(r.agent)


# --- bundled Gherkin toolchain ------------------------------------------------
#
# `command -v` only proves a file with the right name is on PATH. The bundled
# binaries are Linux ELF executables: on Windows (Git Bash) or macOS they are
# found and then fail with "Exec format error". So the check EXECUTES each tool.

TOOLCHAIN_TOOLS = ("gherkin-parser", "gherkin-mutator", "gherkin-ir-dry-checker")
TOOL_OK = "ok"
TOOL_MISSING = "missing"
TOOL_WRONG_PLATFORM = "wrong-platform"  # exec format error / foreign binary
TOOL_NOT_EXECUTABLE = "not-executable"  # permissions or a broken launcher
TOOL_PROBE_TIMEOUT = 3
ELF_MAGIC = b"\x7fELF"
# Wrapper suffixes preferred over the bare file on Windows.
WINDOWS_SUFFIXES = (".exe", ".cmd", ".bat")


@dataclass(frozen=True)
class ToolStatus:
    name: str
    status: str
    detail: str = ""
    path: str = ""
    from_path: bool = False  # resolved from PATH, not from the project's toolchain/bin

    @property
    def ok(self):
        return self.status == TOOL_OK


def host_platform():
    """(system, machine), lowercased: ('windows', 'amd64'), ('linux', 'x86_64')..."""
    return platform.system().lower(), platform.machine().lower()


def is_elf(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == ELF_MAGIC
    except OSError:
        return False


def resolve_tool(bin_dir, name, system=None):
    """Path of tool `name`: a `.exe`/`.cmd`/`.bat` wrapper next to the binary
    wins on Windows, then the bare file, then whatever PATH offers. '' if none.
    Callers must tell a PATH hit apart (see check_tool's `from_path`)."""
    system = host_platform()[0] if system is None else system
    bin_dir = Path(bin_dir)
    if system == "windows":
        for suffix in WINDOWS_SUFFIXES:
            candidate = bin_dir / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    bare = bin_dir / name
    if bare.is_file():
        return str(bare)
    return shutil.which(name) or ""


_ARCH_NAMES = {"x86_64": "x64", "amd64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def host_triple(system=None, machine=None):
    """`<os>-<arch>` as used by template/toolchain/<triple> (e.g. 'windows-x64'),
    or '' when the host is not one of the supported builds."""
    host_system, host_machine = host_platform()
    system = host_system if system is None else system
    machine = host_machine if machine is None else machine
    arch = _ARCH_NAMES.get(str(machine).lower())
    if system in ("linux", "darwin", "windows") and arch:
        return f"{system}-{arch}"
    return ""


def installed_triple(bin_dir):
    """Triple recorded by swarm-init in toolchain/PLATFORM ('' when absent)."""
    try:
        return (Path(bin_dir).parent / "PLATFORM").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _fix_hint(bin_dir, system, machine):
    """How to get a toolchain that runs here, mentioning an installed/host mismatch."""
    host = host_triple(system, machine)
    installed = installed_triple(bin_dir) if bin_dir else ""
    parts = []
    if installed and host and installed != host:
        parts.append(f"The installed toolchain is for {installed} but this host is {host}.")
    parts.append(
        "Run `swarm-init --update` on this project from this OS (it installs the build "
        "matching this host)" + (f", or `scripts/build_toolchain.sh {host}`" if host else "") +
        "; inside WSL/Linux the Linux build works as is.")
    return " ".join(parts)


def _wrong_platform_message(name, system, machine, detail, bin_dir=None):
    return (
        f"{name} cannot run on this host ({system}/{machine}): {detail}. "
        f"{_fix_hint(bin_dir, system, machine)} "
        f"Do not generate .feature files until this is fixed."
    )


def check_tool(bin_dir, name, timeout=TOOL_PROBE_TIMEOUT, system=None, machine=None):
    """Classify one tool by trying to run it (`--version`, short timeout, no shell)."""
    host_system, host_machine = host_platform()
    system = host_system if system is None else system
    machine = host_machine if machine is None else machine
    path = resolve_tool(bin_dir, name, system)
    if not path:
        return ToolStatus(
            name, TOOL_MISSING,
            f"{name} was not found in {bin_dir} or on PATH. {_fix_hint(bin_dir, system, machine)}")
    try:
        from_path = Path(path).resolve().parent != Path(bin_dir).resolve()
    except OSError:
        from_path = False
    status = _probe(bin_dir, name, path, timeout, system, machine)
    if from_path:
        status = ToolStatus(status.name, status.status, f"{status.detail} (resolved from PATH: {path})".strip(),
                            status.path, True)
    return status


def _run_probe(path, timeout):
    """Run `path --version` without a shell; a hung process tree is killed."""
    popen_kwargs = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        [path, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, **popen_kwargs)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.wait(timeout=2)
        except (subprocess.SubprocessError, OSError):
            pass
        raise


def _probe(bin_dir, name, path, timeout, system, machine):
    if system != "linux" and is_elf(path):
        return ToolStatus(
            name, TOOL_WRONG_PLATFORM,
            _wrong_platform_message(name, system, machine, "the file is a Linux ELF binary", bin_dir), path)
    try:
        _run_probe(path, timeout)
    except subprocess.TimeoutExpired:
        # It started (the OS could execute it) but did not exit: executable.
        return ToolStatus(name, TOOL_OK, "started but did not exit within the probe timeout", path)
    except PermissionError as e:
        return ToolStatus(name, TOOL_NOT_EXECUTABLE, f"{name} is not executable: {e}", path)
    except FileNotFoundError as e:
        # Found on disk but the launcher (shebang interpreter, loader) is not.
        return ToolStatus(name, TOOL_NOT_EXECUTABLE, f"{name} cannot start: {e}", path)
    except OSError as e:
        if e.errno == errno.ENOEXEC or getattr(e, "winerror", None) == 193:
            return ToolStatus(
                name, TOOL_WRONG_PLATFORM,
                _wrong_platform_message(name, system, machine, f"exec format error ({e})", bin_dir), path)
        return ToolStatus(name, TOOL_NOT_EXECUTABLE, f"{name} failed to start: {e}", path)
    return ToolStatus(name, TOOL_OK, "", path)


def check_toolchain(bin_dir, tools=TOOLCHAIN_TOOLS, **kwargs):
    """ToolStatus for every bundled tool. Empty when the project has no toolchain
    directory at all (it simply does not use the Gherkin tools)."""
    if not Path(bin_dir).is_dir():
        return []
    # In parallel, so one hung tool stalls startup for about one timeout, not three.
    with ThreadPoolExecutor(max_workers=max(1, len(tools))) as pool:
        return list(pool.map(lambda name: check_tool(bin_dir, name, **kwargs), tools))


def toolchain_report(statuses):
    """Plain-text report; one line per tool that is not usable ('' when all are)."""
    return "\n".join(f"{s.name} [{s.status}]: {s.detail}" for s in statuses if not s.ok)


def toolchain_summary(statuses):
    """One line per tool, usable or not, with the path it resolved to."""
    return "\n".join(
        f"{s.name}: {s.status}" + (f" ({s.path})" if s.path else "") + (f" - {s.detail}" if s.detail else "")
        for s in statuses)
