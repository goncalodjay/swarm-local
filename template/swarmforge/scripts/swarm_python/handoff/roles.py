from .project import project_root, roles_file


def role_rows() -> list[list[str]]:
    path = roles_file()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        rows.append(line.split("\t"))
    return rows


def role_known(name: str) -> bool:
    return any(row and row[0] == name for row in role_rows())


def role_row(name: str):
    for row in role_rows():
        if row and row[0] == name:
            return row
    raise SystemExit(f"Unknown role: {name}")


def role_worktree_name(name: str) -> str:
    return role_row(name)[1]


def role_worktree_path(name: str) -> str:
    return role_row(name)[2]


def role_session(name: str) -> str:
    return role_row(name)[3]


def role_display_name(name: str) -> str:
    return role_row(name)[4]


def role_agent(name: str) -> str:
    return role_row(name)[5]


def role_receive_mode(name: str) -> str:
    row = role_row(name)
    if len(row) > 6 and row[6]:
        return row[6]
    return "task"
