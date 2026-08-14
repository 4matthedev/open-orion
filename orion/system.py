"""Read-only system telemetry for the desktop UIs.

Every value is real, read live from ``/proc``, sysfs, or the stdlib — nothing
is faked. The HUD (``jarvis_hud.py``) and any future widget pull their readouts
from here so the sampling code is shared and unit-testable.

All functions are safe to call from any thread; they never mutate state and
never raise (they return ``0`` / ``None`` defaults on failure).
"""

from __future__ import annotations

import shutil
import socket
import time
from pathlib import Path


def read_float(path: str, default: float = 0.0) -> float:
    """Read the first whitespace-delimited number from ``path``."""
    try:
        with open(path) as f:
            return float(f.read().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return default


def read_int(path: str, default: int = 0) -> int:
    """Read an integer from ``path``."""
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return default


def sys_uptime() -> float:
    """Seconds since boot."""
    return read_float("/proc/uptime")


def sys_hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "localhost"


def sys_cpu_load() -> float:
    """Instantaneous CPU usage as a percentage (two 250ms samples)."""

    def sample() -> tuple[int, int]:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        idle = int(parts[3]) + int(parts[4])
        total = sum(int(p) for p in parts)
        return total, idle

    t1 = sample()
    time.sleep(0.25)
    t2 = sample()
    dt = t2[0] - t1[0]
    di = t2[1] - t1[1]
    return 100.0 * (dt - di) / dt if dt else 0.0


def sys_memory() -> tuple[float, float]:
    """Return (available, total) memory in GiB."""
    total = mem_available = 0.0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) / (1024 ** 2)
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) / (1024 ** 2)
    except OSError:
        pass
    return mem_available, total


def sys_battery() -> int | None:
    """Battery capacity as a percentage, or None when no battery is present."""
    for p in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        cap = read_int(str(p / "capacity"), -1)
        if cap >= 0:
            return cap
    return None


def sys_cpu_temp() -> float | None:
    """CPU temperature in °C (thermal_zone0), or None if unavailable."""
    temp = read_float("/sys/class/thermal/thermal_zone0/temp", -1)
    return temp / 1000.0 if temp >= 0 else None


def sys_disk() -> tuple[float, float]:
    """Return (used-percent, free-GiB) for the root filesystem."""
    usage = shutil.disk_usage("/")
    return usage.used / usage.total * 100.0, usage.free / (1024 ** 3)


def sys_net_rx_tx() -> tuple[int, int]:
    """Return cumulative (rx, tx) bytes across all interfaces except loopback."""
    rx = tx = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" not in line or " lo:" in line:
                    continue
                cols = line.split(":")[1].split()
                rx += int(cols[0])
                tx += int(cols[8])
    except (OSError, ValueError, IndexError):
        pass
    return rx, tx


__all__ = [
    "read_float",
    "read_int",
    "sys_uptime",
    "sys_hostname",
    "sys_cpu_load",
    "sys_memory",
    "sys_battery",
    "sys_cpu_temp",
    "sys_disk",
    "sys_net_rx_tx",
]
