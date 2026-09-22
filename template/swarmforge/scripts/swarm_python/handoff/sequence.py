import os

from .project import state_dir


def _lock_exclusive(fd):
    if os.name == "nt":
        import msvcrt
        # msvcrt.locking needs at least one byte in the file to lock.
        fd.write("x")
        fd.flush()
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)


def next_sequence() -> str:
    handoff_dir = state_dir()
    handoff_dir.mkdir(parents=True, exist_ok=True)
    seq_file = handoff_dir / "sequence"
    lock_file = handoff_dir / "sequence.lock"
    lock_fd = open(lock_file, "w")
    try:
        _lock_exclusive(lock_fd)
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
