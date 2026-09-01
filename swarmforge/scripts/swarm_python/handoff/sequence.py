import fcntl

from .project import state_dir


def next_sequence() -> str:
    handoff_dir = state_dir()
    handoff_dir.mkdir(parents=True, exist_ok=True)
    seq_file = handoff_dir / "sequence"
    lock_file = handoff_dir / "sequence.lock"
    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        last = 0
        if seq_file.exists():
            try:
                last = int(seq_file.read_text(encoding="utf-8").strip())
            except ValueError:
                last = 0
        next_val = last + 1
        formatted = f"{next_val:06d}"
        seq_file.write_text(formatted + "\n", encoding="utf-8")
        return formatted
    finally:
        lock_fd.close()
        if lock_file.exists():
            lock_file.unlink()
