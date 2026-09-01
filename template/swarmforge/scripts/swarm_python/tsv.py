from pathlib import Path


def read_tsv(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        rows.append(line.split("\t"))
    return rows


def write_tsv(path: Path, rows: list[list[str]]) -> None:
    content = "".join("\t".join(row) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")
