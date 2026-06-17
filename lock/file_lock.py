"""FileLock — mutex inter-PROCES și inter-THREAD bazat pe fcntl.flock.

    from lock import FileLock
    with FileLock("/cale/catre/.lock"):     # exclusiv
        ...secțiune critică...               # al doilea proces/thread așteaptă aici

Fiecare apel deschide PROPRIUL file descriptor → flock(LOCK_EX) e exclusiv între
file descriptions diferite, deci serializează atât procese cât și thread-uri.
Pe Windows (fără fcntl) e no-op — bot-ul rulează pe Linux. Generic: poate proteja
ORICE operație, nu doar plasarea de ordine.
"""

try:
    import fcntl                            # Unix (Linux/WSL)
    _HAVE_FCNTL = True
except ImportError:                         # Windows → gate dezactivat (no-op)
    _HAVE_FCNTL = False


class FileLock:
    """Lock inter-PROCES și inter-THREAD: fcntl.flock(LOCK_EX) pe un fd propriu."""

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
