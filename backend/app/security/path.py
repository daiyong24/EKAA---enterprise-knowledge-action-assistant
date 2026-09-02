"""Safe uploaded-name validation, independent of filesystem implementation."""

from __future__ import annotations

import ntpath
import posixpath
import re

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")


def validate_safe_filename(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 128:
        raise ValueError("filename is invalid")
    normalized = name.replace("\\", "/")
    if normalized != posixpath.basename(normalized) or normalized != ntpath.basename(
        name
    ):
        raise ValueError("path components are not allowed")
    if normalized in {".", ".."} or ".." in normalized.split("/"):
        raise ValueError("path traversal is not allowed")
    if not _SAFE_NAME.fullmatch(normalized) or normalized.endswith((".", " ")):
        raise ValueError("filename is invalid")
    return normalized
