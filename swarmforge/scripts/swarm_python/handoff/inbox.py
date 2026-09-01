import sys
from pathlib import Path

from .format import header_field, header_value, body
from .timefmt import now_id_ts


def handoff_files(d: Path) -> list[Path]:
    if not d.exists():
        return []
    out = []
    for entry in sorted(d.iterdir(), key=lambda p: p.name):
        if entry.is_file() and entry.name.endswith(".handoff"):
            out.append(entry)
    return out


def batch_dirs(d: Path) -> list[Path]:
    if not d.exists():
        return []
    out = []
    for entry in sorted(d.iterdir(), key=lambda p: p.name):
        if entry.is_dir() and entry.name.startswith("batch_"):
            out.append(entry)
    return out


def new_batch_dir(in_process_dir: Path) -> Path:
    suffix = 1
    while True:
        candidate = in_process_dir / f"batch_{now_id_ts()}_{suffix:06d}"
        if not candidate.exists():
            return candidate
        suffix += 1


def print_task(file: Path) -> None:
    task_name = header_field(file, "task")
    print(f"TASK: {file}")
    print(f"FROM: {header_value(file, 'from', 'unknown')}")
    print(f"TYPE: {header_value(file, 'type', 'unknown')}")
    print(f"PRIORITY: {header_value(file, 'priority', '50')}")
    if task_name:
        print(f"TASK_NAME: {task_name}")
    print("PAYLOAD:")
    sys.stdout.write(body(file))


def print_batch(batch_dir: Path) -> None:
    files = handoff_files(batch_dir)
    if not files:
        print(
            f"AMBIGUOUS_TASK_STATE: batch contains no tasks: {batch_dir}",
            file=sys.stderr,
        )
        sys.exit(2)
    print(f"BATCH: {batch_dir}")
    print(f"COUNT: {len(files)}")
    print(f"PRIORITY: {header_value(files[0], 'priority', '50')}")
    for index, f in enumerate(files, start=1):
        print()
        print(f"BATCH_ITEM: {index}")
        print_task(f)
