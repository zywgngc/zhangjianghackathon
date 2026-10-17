"""Single service owner on POSIX and Windows; closing the handle releases the lock."""
import os
from contextlib import contextmanager


@contextmanager
def owner_lock(path):
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("此 DATA_DIR 已有服务运行；请使用单个 uvicorn worker") from None
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("此 DATA_DIR 已有服务运行；请使用单个 uvicorn worker") from None
        yield
