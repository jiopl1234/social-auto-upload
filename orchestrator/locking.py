"""OS-backed account locks and conservative process-liveness checks."""

from __future__ import annotations

import errno
import os
from pathlib import Path

from .errors import TaskError


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _open_process = _kernel32.OpenProcess
    _open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _open_process.restype = wintypes.HANDLE
    _get_process_times = _kernel32.GetProcessTimes
    _get_process_times.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    _get_process_times.restype = wintypes.BOOL
    _wait_for_single_object = _kernel32.WaitForSingleObject
    _wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _wait_for_single_object.restype = wintypes.DWORD
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = (wintypes.HANDLE,)
    _close_handle.restype = wintypes.BOOL

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x102
    _ERROR_INVALID_PARAMETER = 87


class ProcessLock:
    """Hold an exclusive file lock until release or process exit.

    The lock file is deliberately retained after release: removing it could let
    two processes lock different inodes for the same account.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise TaskError("LOCK_ERROR", "Account lock is already held by this worker.")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise TaskError("LOCK_ERROR", "Unable to access the account lock.") from exc

        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            if exc.errno in (errno.EACCES, errno.EAGAIN) or getattr(exc, "winerror", None) == 33:
                raise TaskError("ACCOUNT_BUSY", "This account is already in use.") from exc
            raise TaskError("LOCK_ERROR", "Unable to acquire the account lock.") from exc

        self._fd = fd

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        error: OSError | None = None
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as exc:
            error = exc
        finally:
            try:
                os.close(fd)
            except OSError as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise TaskError("LOCK_ERROR", "Unable to release the account lock.") from error

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.release()


def process_identity(pid: int) -> str | None:
    """Return an opaque process-start marker, or None if it cannot be verified."""
    if pid <= 0:
        return None
    if os.name == "nt":
        handle, _ = _windows_open(pid)
        if not handle:
            return None
        try:
            if _wait_for_single_object(handle, 0) != _WAIT_TIMEOUT:
                return None
            identity = _windows_identity(handle)
            if _wait_for_single_object(handle, 0) != _WAIT_TIMEOUT:
                return None
            return identity
        finally:
            _close_handle(handle)

    stat = _linux_stat(pid)
    if stat is None:
        return None
    state, started = stat
    return None if state in ("Z", "X", "x") else f"proc:{started}"


def process_state(pid: int, identity: str | None) -> str:
    """Return LIVE, DEAD, or UNKNOWN without mistaking access failure for death."""
    if pid <= 0:
        return "DEAD"
    if os.name == "nt":
        handle, error = _windows_open(pid)
        if not handle:
            return "DEAD" if error == _ERROR_INVALID_PARAMETER else "UNKNOWN"
        try:
            result = _wait_for_single_object(handle, 0)
            if result == _WAIT_OBJECT_0:
                return "DEAD"
            if result != _WAIT_TIMEOUT:
                return "UNKNOWN"
            if identity is not None:
                current = _windows_identity(handle)
                if current is None:
                    return "UNKNOWN"
                if current != identity:
                    return "DEAD"
            return "LIVE"
        finally:
            _close_handle(handle)

    stat = _linux_stat(pid)
    if stat is not None:
        state, started = stat
        if state in ("Z", "X", "x"):
            return "DEAD"
        if identity is not None and identity != f"proc:{started}":
            return "DEAD"
        return "LIVE"
    if os.path.isdir("/proc"):
        try:
            os.stat(f"/proc/{pid}/stat")
        except FileNotFoundError:
            return "DEAD"
        except OSError:
            return "UNKNOWN"
        return "UNKNOWN"

    # Systems without procfs can establish existence, but cannot verify a
    # persisted process-start marker against PID reuse.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "DEAD"
    except OSError:
        return "UNKNOWN"
    return "LIVE" if identity is None else "UNKNOWN"


def _linux_stat(pid: int) -> tuple[str, str] | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as proc_file:
            raw = proc_file.read()
    except OSError:
        return None
    # The parenthesized command name may contain spaces or ')' characters.
    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 1 :].split()
    if len(fields) < 20:
        return None
    return fields[0], fields[19]


if os.name == "nt":

    def _windows_open(pid: int) -> tuple[int | None, int | None]:
        if pid > 0xFFFFFFFF:
            return None, _ERROR_INVALID_PARAMETER
        handle = _open_process(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
        return (handle, None) if handle else (None, ctypes.get_last_error())


    def _windows_identity(handle: int) -> str | None:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not _get_process_times(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        started = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return f"win:{started}"
