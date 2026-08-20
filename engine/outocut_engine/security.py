from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes


class SecretProtectionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def protect_secret(value: str) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8")
    if sys.platform != "win32":
        raise SecretProtectionError("OutoCut 密钥存储仅支持 Windows DPAPI")
    input_blob, input_buffer = _blob(raw)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "OutoCut API secret",
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )
    _ = input_buffer
    if not ok:
        raise SecretProtectionError("Windows DPAPI 加密失败")
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if sys.platform != "win32":
        raise SecretProtectionError("OutoCut 密钥存储仅支持 Windows DPAPI")
    encrypted = base64.b64decode(value)
    input_blob, input_buffer = _blob(encrypted)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0x01, ctypes.byref(output_blob)
    )
    _ = input_buffer
    if not ok:
        raise SecretProtectionError("Windows DPAPI 解密失败")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)
