"""FileLock — cross-process and cross-thread mutex based on fcntl.flock.

    from lock import FileLock
    with FileLock("/path/to/.lock"):        # exclusive
        ...critical section...               # the next process/thread waits here

Each invocation opens its own file descriptor, so flock(LOCK_EX) is exclusive
across distinct file descriptions and serializes both processes and threads.
On Windows, where fcntl is unavailable, this is a no-op; the bot runs on Linux.
The lock is generic and can protect any operation, not just order placement.
"""

try:
    import fcntl                            # Unix (Linux/WSL)
    _HAVE_FCNTL = True
except ImportError:                         # Windows: gate disabled (no-op)
    _HAVE_FCNTL = False


class FileLock:
    """Cross-process/thread lock using fcntl.flock(LOCK_EX) on an owned fd."""

    def __init__(self, path):
        self.path = path
        self._fd = None

    def __enter__(self):
        self._fd = open(self.path, "a+")
        if _HAVE_FCNTL:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            if _HAVE_FCNTL:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            self._fd.close()
