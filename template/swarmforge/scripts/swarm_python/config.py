import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .ansi import RED, RESET

VALID_AGENTS = {"claude", "codex", "copilot", "grok", "hermes", "opencode", "pi"}
VALID_RECEIVE_MODES = {"task", "batch"}
MASTER_WORKTREES = {"none", "master"}

SESSION_PREFIX = "swarmforge"
AGENT_WINDOW = "swarm"


@dataclass
class RoleRow:
    role: str
    agent: str
    session: str
    display_name: str
    worktree_name: str
    worktree_path: Path
    receive_mode: str
    extra_args: str
    workspace_id: str = ""
    pane_id: str = ""


def fail(message):
    print(f"{RED}Error:{RESET} {message}", file=sys.stderr)
    sys.exit(1)


def _display_name(role: str) -> str:
    parts = re.split(r"[-_\s]+", role)
    parts = [p for p in parts if p]
    out = []
    for p in parts:
        if p:
            out.append(p[0].upper() + p[1:])
        else:
            out.append(p)
    return " ".join(out)


def _session_name(role: str) -> str:
    return f"{SESSION_PREFIX}-{role}"


def parse_config(ctx) -> list[RoleRow]:
    if not ctx.config_file.exists():
        fail(f"Config not found at {ctx.config_file}")
    if not ctx.constitution_file.exists():
        fail(f"Constitution prompt not found at {ctx.constitution_file}")

    rows: list[RoleRow] = []
    seen_roles: set[str] = set()
    seen_worktrees: set[str] = set()

    for line_no, raw_line in enumerate(
        ctx.config_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            fail(f"Invalid config line {line_no}: {line}")

        keyword, role, agent, worktree, *trailing = fields
        agent = agent.lower()

        receive_mode = (
            trailing[0] if trailing and trailing[0] in VALID_RECEIVE_MODES else "task"
        )
        extra_tokens = (
            trailing[1:] if trailing and trailing[0] in VALID_RECEIVE_MODES else trailing
        )
        extra_args = " ".join(extra_tokens) if extra_tokens else None

        if keyword != "window":
            fail(f"Unknown config directive on line {line_no}: {keyword}")
        if "_" in role:
            fail(
                f"Invalid role '{role}' on line {line_no}: "
                "role names may not contain underscores"
            )
        if role in seen_roles:
            fail(f"Duplicate role '{role}' in {ctx.config_file}")
        if worktree not in MASTER_WORKTREES and worktree in seen_worktrees:
            fail(f"Duplicate worktree '{worktree}' in {ctx.config_file}")
        if "/" in worktree or worktree in (".", ".."):
            fail(f"Invalid worktree '{worktree}' for role '{role}'")
        if agent not in VALID_AGENTS:
            fail(f"Unsupported agent '{agent}' for role '{role}'")
        if receive_mode not in VALID_RECEIVE_MODES:
            fail(
                f"Invalid receive mode '{receive_mode}' for role '{role}' on line "
                f"{line_no}: expected task or batch"
            )

        prompt_file = ctx.roles_dir / f"{role}.prompt"
        if not prompt_file.exists():
            fail(f"Missing role prompt {prompt_file}")

        if worktree in MASTER_WORKTREES:
            worktree_path = ctx.working_dir
        else:
            worktree_path = ctx.worktrees_dir / worktree

        rows.append(
            RoleRow(
                role=role,
                agent=agent,
                session=_session_name(role),
                display_name=_display_name(role),
                worktree_name=worktree,
                worktree_path=worktree_path,
                receive_mode=receive_mode,
                extra_args=extra_args,
            )
        )
        seen_roles.add(role)
        if worktree not in MASTER_WORKTREES:
            seen_worktrees.add(worktree)

    if not rows:
        fail(f"No windows defined in {ctx.config_file}")

    return rows
