import re
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


def _write_agent_instruction(role: str, prompt_file: Path):
    prompt_file.write_text(
        f"Read swarmforge/constitution.prompt, then read every file it refers to "
        f"recursively, and obey all of those instructions.\n"
        f"Read swarmforge/roles/{role}.prompt, then read every file it refers to "
        f"recursively, and follow all of those instructions.\n",
        encoding="utf-8",
    )


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

    _write_agent_instruction(role, prompt_file)

    base = (
        f"export SWARMFORGE_ROLE={shell_quote(role)} && "
        f"export PATH={shell_quote(str(role_script_dir))}:"
        f"{shell_quote(str(role_tool_bin))}:$PATH && "
        f"cd {shell_quote(str(role_worktree))} && "
    )

    if agent == "claude":
        agent_cmd = (
            f"claude --append-system-prompt-file {shell_quote(str(prompt_file))} "
            f"--permission-mode acceptEdits -n {shell_quote(f'SwarmForge {display}')} "
            f"{_extra_args_prefix(row)}"
            f'"$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "codex":
        agent_cmd = (
            f"codex exec -C {shell_quote(str(role_worktree))} "
            f"{_extra_args_prefix(row)}"
            f'"$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "copilot":
        agent_cmd = (
            f"copilot -C {shell_quote(str(role_worktree))} "
            f"--name {shell_quote(f'SwarmForge {display}')} "
            f"{_extra_args_prefix(row)}"
            f'-i "$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "grok":
        agent_cmd = (
            f"grok --cwd {shell_quote(str(role_worktree))} "
            f"{_grok_permission_prefix(row)}"
            f"{_extra_args_prefix(row)}"
            f'--rules "$(cat {shell_quote(str(prompt_file))})" '
            f'--verbatim "$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "opencode":
        agent_cmd = (
            f"opencode {_extra_args_prefix(row)}"
            f'--prompt "$(cat {shell_quote(str(prompt_file))})"'
        )
    elif agent == "pi":
        agent_cmd = (
            f"pi --name {shell_quote(f'SwarmForge {display}')} "
            f"--no-context-files {_extra_args_prefix(row)}"
            f'"$(cat {shell_quote(str(prompt_file))})"'
        )
    else:
        agent_cmd = ""

    command = base + agent_cmd

    if index == 0:
        cleanup_parts = [
            "; exit_code=$?;",
            f"SWARMFORGE_TERMINAL_BACKEND={shell_quote(ctx.terminal_backend)}",
            "nohup",
            shell_quote(str(ctx.script_dir / "swarm-cleanup.sh")),
            shell_quote(ctx.tmux_socket),
            shell_quote(str(ctx.window_ids_file)),
        ]
        cleanup_parts.extend(shell_quote(r.session) for r in ctx.roles)
        cleanup_parts.extend([">/dev/null", "2>&1", "&!", ";", "exit", "$exit_code"])
        command += " " + " ".join(cleanup_parts)

    return command


def send_launch_command(socket, target: str, command: str):
    from .tmux_ops import tmux_send_keys
    tmux_send_keys(socket, target, command, "Enter")
