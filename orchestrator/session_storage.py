"""Atomic, per-user storage for managed browser state and diagnostic evidence."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


@lru_cache(maxsize=1)
def _windows_api() -> tuple[Any, Any]:
    loader = getattr(ctypes, "WinDLL")
    advapi = loader("Advapi32", use_last_error=True)
    kernel = loader("Kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                                  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi.SetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD,
                                             ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_void_p, ctypes.c_void_p]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    kernel.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_SecurityAttributes)]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    return advapi, kernel


@lru_cache(maxsize=1)
def _current_user_sid() -> str:
    advapi, kernel = _windows_api()
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("Cannot identify the current Windows user")
    try:
        length = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if length.value == 0:
            raise OSError("Cannot read the current Windows user")
        buffer = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)):
            raise OSError("Cannot read the current Windows user")
        user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        sid_text = ctypes.c_void_p()
        if not advapi.ConvertSidToStringSidW(user.User.Sid, ctypes.byref(sid_text)):
            raise OSError("Cannot read the current Windows user")
        try:
            return ctypes.wstring_at(sid_text)
        finally:
            kernel.LocalFree(sid_text)
    finally:
        kernel.CloseHandle(token)


def _reject_reparse(path: Path) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise OSError("Managed storage path is a reparse point")


def _windows_private_acl(path: Path, *, directory: bool) -> None:
    advapi, kernel = _windows_api()
    sid = _current_user_sid()
    inherit = "OICI" if directory else ""
    descriptor_text = f"D:P(A;{inherit};FA;;;{sid})"
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        descriptor_text, 1, ctypes.byref(descriptor), None,
    ):
        raise OSError("Cannot construct private storage permissions")
    try:
        if directory:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
                    raise OSError("Cannot create private storage directory")
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                                ctypes.byref(dacl), ctypes.byref(defaulted)) or not present.value:
            raise OSError("Cannot read private storage permissions")
        flags = 0x00000004 | 0x80000000  # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        if advapi.SetNamedSecurityInfoW(str(path), 1, flags, None, None, dacl, None) != 0:
            raise OSError("Cannot secure managed storage permissions")
    finally:
        kernel.LocalFree(descriptor)


def ensure_private_directory(path: Path) -> None:
    """Create or repair the ACL of a managed child directory only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse(path)
    if path.exists() and not path.is_dir():
        raise OSError("Managed storage directory is unavailable")
    if os.name == "nt":
        _windows_private_acl(path, directory=True)
    else:
        path.mkdir(mode=0o700, exist_ok=True)
        path.chmod(0o700)
    _reject_reparse(path)
    if not path.is_dir():
        raise OSError("Managed storage directory is unavailable")


def ensure_private_file(path: Path) -> None:
    """Replace a managed file's ACL with the current user only."""
    path = Path(path)
    _reject_reparse(path)
    if not path.is_file():
        raise OSError("Managed storage file is unavailable")
    if os.name == "nt":
        _windows_private_acl(path, directory=False)
    else:
        path.chmod(0o600)
    _reject_reparse(path)


def atomic_private_write(path: Path, payload: bytes, *,
                         validate: Callable[[Path], object] | None = None) -> None:
    """Validate and secure a temporary file before replacing the destination."""
    destination = Path(path)
    ensure_private_directory(destination.parent)
    _reject_reparse(destination)
    if destination.exists():
        ensure_private_file(destination)
    temporary = destination.with_name(f"{destination.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if validate is not None:
            validate(temporary)
        ensure_private_file(temporary)
        _reject_reparse(destination)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
