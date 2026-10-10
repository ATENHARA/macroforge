"""Keep an API key in Windows Credential Manager, the encrypted vault Windows uses for passwords.

The key is encrypted by Windows for your user account, is not written to any file in the MacroForge
folder, is not part of any program's environment, and can be seen or deleted in Control Panel >
Credential Manager > Windows Credentials (entry names start with ``MacroForge:``). Standard library
only (``ctypes``); on other systems every function reports that it is unavailable.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from .errors import MacroForgeError

TARGET_PREFIX = "MacroForge:"
MAX_KEY_CHARS = 1000  # Windows limits a credential to 2560 bytes (UTF-16)
_GENERIC = 1  # CRED_TYPE_GENERIC
_PERSIST_LOCAL_MACHINE = 2  # kept on this PC for this user (not synced to other computers)
_NOT_FOUND = 1168  # ERROR_NOT_FOUND


class KeystoreError(MacroForgeError):
    """The key could not be stored or read."""


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", _FileTime),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.c_void_p),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


def _advapi():
    """The Windows security library with typed function signatures."""
    if sys.platform != "win32":
        raise KeystoreError("Windows Credential Manager is only available on Windows")
    lib = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.POINTER(_Credential)
    lib.CredWriteW.argtypes, lib.CredWriteW.restype = [pointer, wintypes.DWORD], wintypes.BOOL
    lib.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(pointer)]
    lib.CredReadW.restype = wintypes.BOOL
    lib.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    lib.CredDeleteW.restype = wintypes.BOOL
    lib.CredFree.argtypes, lib.CredFree.restype = [ctypes.c_void_p], None
    return lib


def _target(name: str) -> str:
    return TARGET_PREFIX + name


def store(name: str, value: str) -> None:
    """Save ``value`` under ``name`` (replacing an earlier one)."""
    if not value or len(value) > MAX_KEY_CHARS:
        raise KeystoreError("the key is empty or too long to store")
    lib = _advapi()
    blob = value.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob, len(blob))
    cred = _Credential()
    cred.Type = _GENERIC
    cred.TargetName = _target(name)
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.c_void_p)
    cred.Persist = _PERSIST_LOCAL_MACHINE
    cred.UserName = "MacroForge"
    if not lib.CredWriteW(ctypes.byref(cred), 0):
        raise KeystoreError(f"Windows refused to save the key (error {ctypes.get_last_error()})")


def load(name: str) -> str:
    """The stored value, or an empty string if there is none (or this is not Windows)."""
    try:
        lib = _advapi()
    except KeystoreError:
        return ""
    found = ctypes.POINTER(_Credential)()
    if not lib.CredReadW(_target(name), _GENERIC, 0, ctypes.byref(found)):
        if ctypes.get_last_error() == _NOT_FOUND:
            return ""
        raise KeystoreError("Windows could not read the stored key "
                            f"(error {ctypes.get_last_error()})")
    try:
        cred = found.contents
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return raw.decode("utf-16-le", "replace").strip()
    finally:
        lib.CredFree(found)


def delete(name: str) -> bool:
    """Remove the stored value; returns False if there was none."""
    lib = _advapi()
    if lib.CredDeleteW(_target(name), _GENERIC, 0):
        return True
    if ctypes.get_last_error() == _NOT_FOUND:
        return False
    raise KeystoreError("Windows could not delete the stored key "
                        f"(error {ctypes.get_last_error()})")
