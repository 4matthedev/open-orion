"""Command sanitization and static risk classification.

Every command the model wants to run is classified *before* execution:

* ``forbidden`` — always hard-blocked (``rm -rf /``, block-device writes,
  fork bombs, ``curl | sudo sh``, overwriting ``/etc/passwd``, ...). This
  guard cannot be disabled from the CLI.
* ``risky``  — requires interactive confirmation unless ``safety_level``
  is ``permissive`` or ``auto``.
* ``safe``   — runs without a prompt (unless ``safety_level=strict`` or
  ``always_confirm``).

The classifier is pattern-based (not perfect); in autonomous mode the model
itself is the primary guard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppSettings

Level = Literal["safe", "risky", "forbidden"]


@dataclass(frozen=True)
class RiskAssessment:
    level: Level
    reason: str


def sanitize_command(command: str, max_length: int | None = None) -> str:
    """Normalize a raw LLM command: strip NUL bytes and surrounding whitespace."""
    clean = command.replace("\x00", "").strip()
    if max_length and len(clean) > max_length:
        clean = clean[:max_length]
    return clean


# -- block devices -----------------------------------------------------
# /dev/sda1, /dev/nvme0n1p2, /dev/mmcblk0, /dev/vda, /dev/hda, ...
_DEV = (
    r"(?:/dev/(?:sd[a-z]\d*|nvme\d+n\d+(?:p\d+)?|mmcblk\d+(?:p\d+)?"
    r"|vd[a-z]\d*|hd[a-z]\d*|loop\d+(?:p\d+)?))"
)
_BLOCK_WRITE = re.compile(
    r"\bdd\b[^\n;]*\bof\s*=\s*" + _DEV
    + r"|\bmkfs(?:\.[a-z0-9]+)?\s+" + _DEV
    + r"|\b(?:fdisk|gdisk|sfdisk|parted|wipefs)\s+" + _DEV
    + r"|\bshred\b[^\n;]*" + _DEV
)

# -- catastrophic / irreversible ---------------------------------------
_REMOVES_ROOT = re.compile(
    r"\brm\s+(?:-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)\s+"
    r"/(?:\s|\*|$)"
)
_FORK_BOMB = re.compile(r"\{\s*:\s*\|\s*:\s*&\s*\}")
_CURL_SUDO_SH = re.compile(
    r"\b(?:curl|wget)\b[^\n|;]*\|\s*\bsudo\b"
)
_CRITICAL_FILE = re.compile(
    r"(?:>>?|\btouch\b|\btruncate\b|: >|>)\s*"
    r"(?:[^;|]*?/)?/etc/(?:passwd|shadow|sudoers|ssh/authorized_keys)"
)
_CHMOD_CRITICAL = re.compile(
    r"\bchmod\b[^\n;]*/etc/(?:passwd|shadow|sudoers|ssh/authorized_keys)"
)

# -- risky (destructive / system-mutating) -----------------------------
_RM = re.compile(r"\brm\s+-\w*r\w*\b|\brm\b")
_SUDO = re.compile(r"\bsudo\b")
_SHUTDOWN = re.compile(r"\b(?:shutdown|reboot|poweroff|halt)\b|\binit\s+[06]\b")
_SERVICE_STOP = re.compile(
    r"\b(?:systemctl|service|rc-service|svc)\s+\S+\s+(?:stop|restart|halt|disable|mask|kill)\b"
    r"|\bsystemctl\s+(?:stop|restart|disable|mask|poweroff|reboot|shutdown)\b"
)
_KILL = re.compile(r"\b(?:kill|killall|pkill|pkexec)\b")
_REDIRECT = re.compile(r"(?:>>?|: >|&>|>)")
_CHMOD_CHOWN = re.compile(r"\b(?:chmod|chown|chgrp|setfacl|chattr)\b")
_DD_MKFS = re.compile(r"\b(?:dd|mkfs|fdisk|parted|gdisk|sfdisk|wipefs|pvcreate|vgremove)\b")
_MOUNT = re.compile(r"\b(?:mount|umount)\b")
_USERADMIN = re.compile(
    r"\b(?:passwd|useradd|userdel|usermod|groupadd|groupdel|adduser|deluser|addgroup)\b"
)
_FIREWALL = re.compile(r"\b(?:iptables|ip6tables|ufw|nft\b|firewall-cmd|systemctl\s+restart\s+network)\b")
_CRON = re.compile(r"\bcrontab\b")
_FORCE_PUSH = re.compile(r"\bgit\s+push\b[^\n;]*(?:-f|--force)\b")
_PACKAGE_DELETE = re.compile(
    r"\b(?:apt|apt-get|dpkg|pacman|dnf|yum|brew|pip\d?|pipx|npm|yarn|pnpm)\b"
    r"[^\n;]*\b(?:remove|rm|purge|uninstall)\b"
)
_SCHED_OVERWRITE = re.compile(r"\bcrontab\s+-r\b|\batrm\b")

_FORBIDDEN_CHECKS: tuple[tuple[str, re.Pattern, str], ...] = (
    ("removes_root", _REMOVES_ROOT, "recursive force-delete of the root filesystem"),
    ("block_write", _BLOCK_WRITE, "direct write to a block device"),
    ("fork_bomb", _FORK_BOMB, "fork bomb pattern"),
    ("curl_sudo_sh", _CURL_SUDO_SH, "piping a remote download straight into sudo"),
    ("critical_file", _CRITICAL_FILE, "overwriting a critical system file"),
    ("chmod_critical", _CHMOD_CRITICAL, "changing permissions on a critical system file"),
)

_RISKY_CHECKS: tuple[tuple[str, re.Pattern, str], ...] = (
    ("sudo", _SUDO, "command runs with elevated privileges"),
    ("rm", _RM, "deletes files"),
    ("shutdown", _SHUTDOWN, "halts, reboots, or powers off the machine"),
    ("service", _SERVICE_STOP, "stops, disables, or restarts a service"),
    ("kill", _KILL, "terminates processes"),
    ("redirect", _REDIRECT, "redirects output to a file (may overwrite)"),
    ("chmod_chown", _CHMOD_CHOWN, "changes file permissions or ownership"),
    ("dd_mkfs", _DD_MKFS, "low-level disk/filesystem operation"),
    ("mount", _MOUNT, "mounts or unmounts filesystems"),
    ("useradmin", _USERADMIN, "modifies users or passwords"),
    ("firewall", _FIREWALL, "changes firewall or network rules"),
    ("cron", _CRON, "modifies scheduled tasks"),
    ("force_push", _FORCE_PUSH, "force-pushes to a git remote"),
    ("package", _PACKAGE_DELETE, "removes or purges installed packages"),
    ("cron_replace", _SCHED_OVERWRITE, "edits or clears the crontab"),
)


def _flag(checks: tuple[tuple[str, re.Pattern, str], ...], command: str) -> str | None:
    for _name, pattern, reason in checks:
        if pattern.search(command):
            return reason
    return None


def classify_command(command: str) -> RiskAssessment:
    """Classify a command as ``safe``, ``risky``, or ``forbidden``."""
    command = sanitize_command(command)
    if not command:
        return RiskAssessment("safe", "empty command")
    reason = _flag(_FORBIDDEN_CHECKS, command)
    if reason:
        return RiskAssessment("forbidden", reason)
    reason = _flag(_RISKY_CHECKS, command)
    if reason:
        return RiskAssessment("risky", reason)
    return RiskAssessment("safe", "read-only or benign command")


def needs_confirmation(
    settings: "AppSettings",
    level: Level,
    model_requests_confirm: bool = False,
) -> bool:
    """Decide whether ``level`` requires a user prompt under this profile.

    ``safety_level=auto``/``permissive`` suppress prompts entirely (but the
    ``forbidden`` gate always blocks before this is consulted);
    ``always_confirm`` and ``strict`` prompt for every command.
    """
    if settings.always_confirm or model_requests_confirm:
        return True
    if settings.safety_level in ("auto", "permissive"):
        return False
    if settings.safety_level == "strict":
        return True
    return level == "risky"