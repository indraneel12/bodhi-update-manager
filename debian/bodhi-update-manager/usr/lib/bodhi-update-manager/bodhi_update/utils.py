"""Small utility helpers for display and system state checks."""

from __future__ import annotations

import os
import shutil


REBOOT_REQUIRED_PATH = "/var/run/reboot-required"

# Privilege tools tried in preference order.
_PRIVILEGE_TOOL_CANDIDATES = ("pkexec", "sudo", "doas")


def format_size(num_bytes: int) -> str:
    """Convert a size in bytes into a human-readable string (e.g. 23.5 MB)."""
    size = float(num_bytes)

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} TB"  # unreachable, but satisfies the type checker


def reboot_required() -> bool:
    """Return True if the system has flagged that a restart is needed."""
    return os.path.exists(REBOOT_REQUIRED_PATH)


def find_privilege_tool() -> str | None:
    """Return the first available privilege-escalation binary."""
    for tool in _PRIVILEGE_TOOL_CANDIDATES:
        if shutil.which(tool):
            return tool
    return None
