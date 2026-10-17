"""Nonblocking kernel file locks; closing the handle releases ownership."""
import errno
import os
from contextlib import contextmanager


class LockUnavailable(BlockingIOError):
    """Another handle or process owns the requested lock."""


@contextmanager
def exclusive_lock(path, *, busy_message=None):
    # O_CREAT without O_TRUNC preserves an owner's metadata when a competitor
    # opens the file. Binary read/write mode also permits an in-place metadata
    # update without append-mode writes or platform newline translation.
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
    with os.fdopen(descriptor, "r+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if busy_message is not None:
                    raise RuntimeError(busy_message) from None
                raise LockUnavailable(errno.EWOULDBLOCK, "File lock is already held", str(path)) from None
            # Windows permits locking beyond EOF. Seed only after acquiring,
            # so simultaneous first owners cannot write into each other's lock.
            if handle.seek(0, 2) == 0:
                handle.write(b"\0")
                handle.flush()
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if busy_message is not None:
                    raise RuntimeError(busy_message) from None
                raise LockUnavailable(errno.EWOULDBLOCK, "File lock is already held", str(path)) from None
        yield handle


@contextmanager
def owner_lock(path):
    with exclusive_lock(path, busy_message="此 DATA_DIR 已有服务运行；请使用单个 uvicorn worker"):
        yield
