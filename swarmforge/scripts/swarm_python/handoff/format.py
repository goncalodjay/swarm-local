from pathlib import Path

RESERVED_FIELDS = frozenset({
    "id", "from", "role", "recipient",
    "created_at", "enqueued_at", "dequeued_at", "completed_at",
})
ALLOWED_FIELDS = frozenset({"type", "to", "priority", "task", "commit", "message"})
ALLOWED_TYPES = frozenset({"git_handoff", "note"})


def header_field(path: Path, field: str):
    prefix = f"{field}: "
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                return None
            if line.startswith(prefix):
                return line[len(prefix):]
    return None


def header_value(path: Path, field: str, default: str) -> str:
    return header_field(path, field) or default


def body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = text.split("\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def set_header(path: Path, field: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"{field}: "
    out = []
    inserted = False
    replaced = False
    for line in lines:
        if not inserted and not line:
            if not replaced:
                out.append(f"{prefix}{value}")
            out.append(line)
            inserted = True
            continue
        if not inserted and line.startswith(prefix):
            out.append(f"{prefix}{value}")
            inserted = True
            replaced = True
            continue
        out.append(line)
    if not inserted:
        out.append(f"{prefix}{value}")
    tmp = path.parent / f".headers.{path.name}.tmp"
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_draft(path: Path) -> dict:
    headers = {}
    ordered = []
    errors = []
    body_lines = []
    body_seen = False
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if body_seen:
            body_lines.append(line)
            continue
        if not line:
            body_seen = True
            continue
        if ": " not in line:
            errors.append(f"Line {line_no}: expected 'field: value'.")
            continue
        field, value = line.split(": ", 1)
        if not field or not value:
            errors.append(f"Line {line_no}: field and value must both be non-empty.")
            continue
        if field in RESERVED_FIELDS:
            errors.append(
                f"Line {line_no}: header '{field}' is reserved and must not be "
                "written by agents."
            )
            continue
        if field not in ALLOWED_FIELDS:
            errors.append(f"Line {line_no}: unknown header '{field}'.")
            continue
        if field in headers:
            errors.append(f"Line {line_no}: duplicate header '{field}'.")
            continue
        headers[field] = value
        ordered.append(field)
    body = "\n".join(body_lines).strip("\n")
    return {"headers": headers, "ordered": ordered, "errors": errors, "body": body}
