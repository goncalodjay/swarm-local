import os
import re
import shutil
from pathlib import Path

from .config import AGENT_WINDOW, RoleRow
from .env import shell_quote


def _extra_args_prefix(row: RoleRow) -> str:
    return f"{row.extra_args} " if row.extra_args else ""


def _grok_wants_auto_approve(row: RoleRow) -> bool:
    args = row.extra_args
    if not args:
        return False
    if "--always-approve" in args:
        return True
    if "--yolo" in args:
        return True
    if re.search(r"--permission-mode\s+bypassPermissions", args):
        return True
    return False


def _grok_permission_prefix(row: RoleRow) -> str:
    if _grok_wants_auto_approve(row):
        return "--permission-mode bypassPermissions "
    return "--permission-mode acceptEdits "


def _write_agent_instruction(role: str, role_worktree: Path, prompt_file: Path):
    # Inline the constitution, its articles, and the role prompt instead of
    # telling the agent to go read them: every role otherwise burns its first
    # turn on the same fixed set of file reads before it can do anything.
    swarmforge_dir = role_worktree / "swarmforge"
    sections = []

    constitution_file = swarmforge_dir / "constitution.prompt"
    if constitution_file.exists():
        sections.append(constitution_file.read_text(encoding="utf-8"))

    articles_dir = swarmforge_dir / "constitution" / "articles"
    if articles_dir.is_dir():
        for path in sorted(articles_dir.glob("*.prompt")):
            sections.append(path.read_text(encoding="utf-8"))

    shared_dir = swarmforge_dir / "scripts" / "shared-articles"
    if shared_dir.is_dir():
        for path in sorted(shared_dir.glob("*.prompt")):
            sections.append(path.read_text(encoding="utf-8"))

    role_file = swarmforge_dir / "roles" / f"{role}.prompt"
    if role_file.exists():
        sections.append(role_file.read_text(encoding="utf-8"))

    sections.append(
        "The constitution, its articles, and your role above were loaded for "
        "you at startup; you do not need to re-read those files now."
    )
    prompt_file.write_text("\n\n---\n\n".join(sections) + "\n", encoding="utf-8")


def build_launch_command(ctx, index: int, row: RoleRow) -> str:
    role = row.role
    agent = row.agent
    display = row.display_name
    role_worktree = row.worktree_path
    role_tool_bin = role_worktree / ".swarmforge" / "toolchain" / "bin"
    if str(role_worktree) == str(ctx.working_dir):
        role_script_dir = ctx.script_dir
    else:
        role_script_dir = role_worktree / "swarmforge" / "scripts"
    prompt_file = ctx.prompts_dir / f"{role}.md"

    _write_agent_instruction(role, role_worktree, prompt_file)

    base = (
        f"export SWARMFORGE_ROLE={shell_quote(role)} && "
        f"export ENGRAM_PROJECT={shell_quote(ctx.working_dir.name)} && "
        f"export PATH={shell_quote(str(role_script_dir))}:"
        f"{shell_quote(str(role_tool_bin))}:$PATH && "
        f"cd {shell_quote(str(role_worktree))} && "
    )

    # Resolve the agent binary at launch time so the herdr pane does not
    # depend on the shell's PATH inheriting the caller's env.
    agent_bin = shell_quote(shutil.which(agent) or agent)

    if agent == "claude":
        agent_cmd = (
            f"{agent_bin} --append-system-prompt-file {shell_quote(str(prompt_file))} "
            f"--permission-mode acceptEdits -n {shell_quote(f'SwarmForge {display}')} "
            f"{_extra_args_prefix(row)}"
            f'"$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "codex":
        agent_cmd = (
            f"{agent_bin} exec -C {shell_quote(str(role_worktree))} "
            f"{_extra_args_prefix(row)}"
            f'"$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "copilot":
        agent_cmd = (
            f"{agent_bin} -C {shell_quote(str(role_worktree))} "
            f"--name {shell_quote(f'SwarmForge {display}')} "
            f"{_extra_args_prefix(row)}"
            f'-i "$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "grok":
        agent_cmd = (
            f"{agent_bin} --cwd {shell_quote(str(role_worktree))} "
            f"{_grok_permission_prefix(row)}"
            f"{_extra_args_prefix(row)}"
            f'--rules "$(cat {shell_quote(str(prompt_file))})" '
            f'--verbatim "$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "opencode":
        agent_cmd = (
            f"{agent_bin} {_extra_args_prefix(row)}"
            f'--prompt "$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "hermes":
        agent_cmd = (
            f"{agent_bin} chat --in {shell_quote(str(role_worktree))} "
            f"{_extra_args_prefix(row)}"
            f"--query-file {shell_quote(str(prompt_file))}"
        )
    elif agent == "pi":
        agent_cmd = (
            f"{agent_bin} --name {shell_quote(f'SwarmForge {display}')} "
            f"--no-context-files {_extra_args_prefix(row)}"
            f'"$(cat {shell_quote(str(prompt_file))})"'
        )
    else:
        agent_cmd = ""

    command = base + agent_cmd

    if index == 0:
        cleanup_parts = [
            "; exit_code=$?;",
            "nohup",
            shell_quote(str(ctx.script_dir / "swarm-cleanup.sh")),
            shell_quote(ctx.herdr_session),
            shell_quote(str(ctx.working_dir)),
        ]
        cleanup_parts.extend(shell_quote(r.workspace_id) for r in ctx.roles)
        cleanup_parts.extend([">/dev/null", "2>&1", "&!", ";", "exit", "$exit_code"])
        command += " " + " ".join(cleanup_parts)

    return command


def write_launch_script(ctx, role: str, command: str) -> Path:
    # herdr panes on Windows default to PowerShell, which can't parse the
    # POSIX syntax above. Writing it to a file and invoking that file with
    # an explicit `bash` keeps the pane's own shell (PowerShell, cmd, bash,
    # whatever) out of the picture entirely.
    script_path = ctx.prompts_dir / f"{role}.sh"
    script_path.write_text(f"#!/usr/bin/env bash\n{command}\n", encoding="utf-8")
    return script_path


def send_launch_command(session: str, pane_id: str, script_path: Path):
    from .herdr_ops import pane_run
    bash_bin = shutil.which("bash") or "bash"
    invocation = f'"{bash_bin}" "{script_path}"'
    if os.name == "nt":
        # PowerShell (the pane's default shell on Windows) treats a leading
        # quoted string as an expression, not a command, without the call
        # operator. POSIX shells choke on a leading `&`, so this must match
        # the OS the pane actually runs on, not the string's own syntax.
        invocation = f"& {invocation}"
    pane_run(session, pane_id, invocation)
