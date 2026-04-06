from __future__ import annotations
import builtins
import os
import sys
import re
import atexit
import datetime
import logging
import threading


# ── Defaults (overridable via configure()) ────────────────────────────────────

_DEFAULT_LOG_FOLDER = "logger"
_DEFAULT_MAX_SIZE   = 10 * 1024 ** 3  # 10 GB
_DEFAULT_CHECK_EVERY = 100            # signal cleanup thread every N writes


# ── ANSI ──────────────────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ── Module identity ───────────────────────────────────────────────────────────

# Absolute path of this file — used to skip our own frames when walking the stack
_THIS_FILE = os.path.normpath(os.path.abspath(__file__))

# Prefixes that warrant showing caller file:line in the output
_CALLER_PREFIXES = (
    "W", "ERR", "CRITICAL", "D",
    "EXP", "TRACEBACK" , "F", "A",
)

# Keep references to the originals BEFORE we patch anything
_original_print        = builtins.print
_original_stderr_write = sys.stderr.write


# ── Filename cache ────────────────────────────────────────────────────────────
#
# Maps raw co_filename -> (normalized_abs_path, basename).
# Avoids repeated normpath+abspath+basename on every stack frame per call.
# Bounded to _FILENAME_CACHE_MAX entries to prevent unbounded growth in
# long-running apps with hot-reloaded or dynamically-imported modules.

_FILENAME_CACHE_MAX = 512

# (Python 3.9+)
_filename_cache: dict[str, tuple[str, str]] = {}
# (compatibil 3.8)
#_filename_cache = {}  # type: dict

def _resolve_filename(raw: str) -> tuple[str, str]:
    """Return (normalized_abs_path, basename) for a raw co_filename, cached."""
    entry = _filename_cache.get(raw)
    if entry is None:
        if len(_filename_cache) >= _FILENAME_CACHE_MAX:
            # Evict an arbitrary entry rather than letting the dict grow forever
            _filename_cache.pop(next(iter(_filename_cache)))
        norm  = os.path.normpath(os.path.abspath(raw))
        base  = os.path.basename(raw)
        entry = (norm, base)
        _filename_cache[raw] = entry
    return entry


def _get_caller_info() -> str:
    """Walk the call stack to find the first frame outside this module.

    Filename normalization results are cached to avoid repeated os calls.
    Returns '[filename:line]' or '[unknown]'.
    """
    try:
        frame = sys._getframe(0)
        while frame is not None:
            norm, base = _resolve_filename(frame.f_code.co_filename)
            if norm != _THIS_FILE:
                return f"[{base}:{frame.f_lineno}]"
            frame = frame.f_back
    except (ValueError, AttributeError):
        pass
    return "[unknown]"


def _needs_caller_info(message: str) -> bool:
    """Return True if the message starts with a severity keyword."""
    return message.startswith(_CALLER_PREFIXES)


# ── Configuration state ───────────────────────────────────────────────────────
#
# All mutable config lives here so configure() can update it atomically.
# _log_folder is resolved to an absolute path immediately so os.chdir() calls
# after import cannot silently redirect log files to a different directory.

_config_lock = threading.Lock()
_log_folder  = os.path.normpath(os.path.abspath(_DEFAULT_LOG_FOLDER))
_max_size    = _DEFAULT_MAX_SIZE
_check_every = _DEFAULT_CHECK_EVERY


def configure(
    *,
    log_folder:  str | None = None,
    max_size:    int | None = None,
    check_every: int | None = None,
) -> None:
    """Reconfigure the logger at runtime (all parameters are optional).

    Args:
        log_folder:   Directory for log files.  Resolved to an absolute path
                      immediately so later os.chdir() calls have no effect.
        max_size:     Maximum total size in bytes before oldest log is deleted.
        check_every:  Number of log writes between folder-size checks.
    """
    global _log_folder, _max_size, _check_every

    with _config_lock:
        if log_folder is not None:
            resolved = os.path.normpath(os.path.abspath(log_folder))
            os.makedirs(resolved, exist_ok=True)
            _log_folder = resolved
        if max_size is not None:
            if max_size <= 0:
                raise ValueError("max_size must be > 0")
            _max_size = max_size
        if check_every is not None:
            if check_every <= 0:
                raise ValueError("check_every must be > 0")
            _check_every = check_every


# ── Background cleanup thread ─────────────────────────────────────────────────
#
# The hot path (print / stderr) only increments a counter and, when the
# threshold is reached, sets _cleanup_event.  All I/O-heavy work (scandir,
# remove) happens exclusively on this background thread so that printing
# threads never pay the cost of a folder scan.

_cleanup_event = threading.Event()
_cleanup_stop  = threading.Event()  # set by atexit to shut the thread down


def _get_folder_size(folder: str) -> int:
    """Return total size in bytes of all files directly inside folder."""
    total = 0
    try:
        for entry in os.scandir(folder):  # scandir is faster than listdir+stat
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
    except OSError:
        pass
    return total


def _delete_oldest_log(folder: str) -> None:
    """Delete the oldest file (by mtime) in folder."""
    oldest_path  = None
    oldest_mtime = float("inf")
    try:
        for entry in os.scandir(folder):
            if entry.is_file(follow_symlinks=False):
                mtime = entry.stat().st_mtime
                if mtime < oldest_mtime:
                    oldest_mtime = mtime
                    oldest_path  = entry.path
    except OSError:
        return

    if oldest_path is None:
        return

    try:
        os.remove(oldest_path)
        _original_print(f"[LOGGER] deleted oldest log: {os.path.basename(oldest_path)}")
    except OSError as exc:
        _original_print(f"[LOGGER] failed to delete log: {exc}")


def _cleanup_loop() -> None:
    """Background thread: wait for the cleanup event, then enforce folder size.

    Runs until _cleanup_stop is set (via atexit).  The wait() timeout lets the
    thread notice _cleanup_stop even if no more writes ever arrive.
    """
    while not _cleanup_stop.is_set():
        # Block until a printing thread signals that the write threshold was
        # reached, or until the timeout lets us re-check _cleanup_stop.
        _cleanup_event.wait(timeout=5.0)
        _cleanup_event.clear()

        if _cleanup_stop.is_set():
            break

        # Snapshot config atomically so configure() changes are seen consistently
        with _config_lock:
            folder   = _log_folder
            max_size = _max_size

        try:
            total = _get_folder_size(folder)
            while total >= max_size:
                mb     = total    / (1024 * 1024)
                max_mb = max_size / (1024 * 1024)
                _original_print(
                    f"[LOGGER] WARNING: log folder '{folder}' exceeds {max_mb:.0f} MB "
                    f"(current: {mb:.1f} MB) → deleting oldest log"
                )
                before = total
                _delete_oldest_log(folder)
                total  = _get_folder_size(folder)
                if total >= before:
                    # Nothing was deleted (empty folder or permission error)
                    _original_print(
                        "[LOGGER] WARNING: could not reduce log folder size, aborting cleanup"
                    )
                    break
        except Exception as exc:
            _original_print(f"[LOGGER] cleanup error: {exc}")


_cleanup_thread = threading.Thread(
    target=_cleanup_loop,
    name="logger-cleanup",
    daemon=True,  # will not prevent the process from exiting on its own
)
_cleanup_thread.start()


def _stop_cleanup_thread() -> None:
    """Signal the cleanup thread to exit and wait for it (called by atexit)."""
    _cleanup_stop.set()
    _cleanup_event.set()         # unblock wait() immediately
    _cleanup_thread.join(timeout=5.0)


atexit.register(_stop_cleanup_thread)


# ── Write counter ─────────────────────────────────────────────────────────────
#
# Shared by both the print and stderr paths.  Every log write (regardless of
# source) counts toward the cleanup threshold.

_write_counter = 0
_counter_lock  = threading.Lock()


def _bump_counter() -> bool:
    """Increment the shared write counter.

    Returns True and resets the counter every _check_every calls, indicating
    that the cleanup thread should be woken up.  The threshold is read inside
    the lock so configure(check_every=…) takes effect on the next cycle.
    """
    global _write_counter
    with _counter_lock:
        _write_counter += 1
        with _config_lock:
            threshold = _check_every
        if _write_counter >= threshold:
            _write_counter = 0
            return True
    return False


# ── Application name ──────────────────────────────────────────────────────────

def _resolve_app_name() -> str:
    """Use the entry-point script name so log files are named after the
    application, not after this module."""
    entry = sys.argv[0] if sys.argv and sys.argv[0] else __file__
    name  = os.path.splitext(os.path.basename(entry))[0]
    return name or "app"


# ── File handler setup ────────────────────────────────────────────────────────

os.makedirs(_log_folder, exist_ok=True)

_app_name = _resolve_app_name()

_file_logger = logging.getLogger(f"app_file_logger.{_app_name}")
_file_logger.setLevel(logging.DEBUG)
_file_logger.propagate = False


class _DailyFileHandler(logging.Handler):
    """One open file handle per calendar day; rotates automatically at midnight.

    Thread-safe: the date check and file-handle swap are both inside the lock
    so midnight rotation is fully atomic.
    """

    def __init__(self, app_name: str) -> None:
        super().__init__()
        self._app_name      = app_name
        self._current_date: str | None = None
        self._stream        = None
        self._lock          = threading.Lock()

    def _open_for_date(self, folder: str, date_str: str) -> None:
        """Close the current stream and open a new one for date_str."""
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None

        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{self._app_name}_{date_str}.log")
        self._stream       = open(path, "a", encoding="utf-8")
        self._current_date = date_str

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._lock:
                # strftime is INSIDE the lock so the date check and handle swap
                # are atomic — prevents a midnight race condition.
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                with _config_lock:
                    folder = _log_folder
                if today != self._current_date:
                    self._open_for_date(folder, today)
                self._stream.write(self.format(record) + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except OSError:
                    pass
                self._stream = None
        super().close()


_handler = _DailyFileHandler(_app_name)
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
)
_file_logger.addHandler(_handler)

atexit.register(_handler.close)


# ── Print / stderr overrides ──────────────────────────────────────────────────

PRINT_CONTEXT = threading.local()


def _patched_print(*args, **kwargs) -> None:
    """Drop-in builtins.print replacement that mirrors output to the log file.

    Per-thread suppression : set PRINT_CONTEXT.enable_print = False
    Global suppression     : call disable_print()

    Hot-path order (cheapest checks first):
      1. Thread-local suppression flag  — one attribute lookup, free exit
      2. Counter bump                   — one lock + int compare, then return
      3. Message build                  — join only when we will actually log
      4. strftime                       — one call, reused for console + file
      5. Caller info                    — only for WARNING/ERROR/… prefixes
      6. Cleanup signal                 — set Event only; zero I/O on this thread
    """
    # 1. Per-thread suppression — cheapest possible early exit
    if getattr(PRINT_CONTEXT, "enable_print", True) is False:
        return

    # 2. Counter — wake cleanup thread if threshold reached
    if _bump_counter():
        _cleanup_event.set()

    # 3. Build message
    message = " ".join(map(str, args))

    # 4. Timestamp — single call reused for both outputs
    current_time = datetime.datetime.now().strftime("%H:%M:%S")

    # 5. Strip ANSI codes for the file — keep original for the console
    #    which can render colours natively.
    clean = (_ANSI_RE.sub("", message) if "\x1b" in message else message)

    # 6. Caller info only for severity prefixes (checked on clean string,
    #    so ANSI codes don't interfere with the prefix match)
    if _needs_caller_info(clean):
        caller = _get_caller_info()
        _original_print(f"{current_time} {caller} {message}", **kwargs)
        _file_logger.info(f"{caller} {clean}")
    else:
        _original_print(f"{current_time} {message}", **kwargs)
        _file_logger.info(clean)


def _dummy_print(*args, **kwargs) -> None:
    """No-op replacement used by disable_print()."""
    pass


def disable_print() -> None:
    """Suppress all print output globally (including file logging)."""
    builtins.print = _dummy_print


def _patched_stderr_write(message: str) -> None:
    """Mirror stderr writes to the log file, stripping ANSI codes.

    Hot-path order:
      1. Whitespace-only guard — cheap strip(), free exit
      2. Counter bump          — same as _patched_print
      3. ANSI fast-path        — char search before running regex
      4. Caller info           — always shown for stderr entries
    """
    _original_stderr_write(message)

    # 1. Skip blank / whitespace-only writes immediately
    if not message.strip():
        return

    # 2. Counter — wake cleanup thread if threshold reached
    if _bump_counter():
        _cleanup_event.set()

    # 3. Strip ANSI only when escape sequences are actually present
    clean = (_ANSI_RE.sub("", message) if "\x1b" in message else message).rstrip("\n")
    if not clean.strip():
        return

    # 4. Caller info always included for stderr
    caller = _get_caller_info()
    _file_logger.info(f"{caller} [STDERR] {clean}")


def _patched_stderr_writelines(lines) -> None:
    """Mirror stderr.writelines() to the log file."""
    for line in lines:
        _patched_stderr_write(line)


# ── One-time patch (double-import safe) ───────────────────────────────────────
#
# A module-level lock guarantees that even if two threads import this module
# simultaneously, the patch is applied exactly once and atomically — there is
# no window where print is patched but stderr is not (or vice-versa).

_patch_lock = threading.Lock()

with _patch_lock:
    if not getattr(builtins, "_logger_patched", False):
        builtins.print            = _patched_print              # type: ignore[attr-defined]
        sys.stderr.write          = _patched_stderr_write       # type: ignore[method-assign]
        sys.stderr.writelines     = _patched_stderr_writelines  # type: ignore[method-assign]
        builtins._logger_patched  = True                        # type: ignore[attr-defined]