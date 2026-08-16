"""Read-only system telemetry for the desktop UIs.

Every value is real, read live from the machine — on Linux from ``/proc``,
sysfs, or the stdlib; on Windows from ``psutil`` (or ctypes/PowerShell when
psutil is unavailable) — nothing is faked. The HUD (``jarvis_hud.py``) and any
future widget pull their readouts from here so the sampling code is shared and
unit-testable.

All functions are safe to call from any thread; they never mutate state and
never raise (they return ``0`` / ``None`` defaults on failure).
"""

from __future__ import annotations

import shutil
import socket
import time
from pathlib import Path

from .platform import is_windows


def _win_uptime() -> float:
    """Seconds since boot on Windows (GetTickCount64 via ctypes)."""
    try:
        import ctypes  # noqa: PLC0415 - stdlib, lazy

        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:  # noqa: BLE001 - best-effort telemetry
        return 0.0


def _win_cpu_load() -> float:
    """Instantaneous CPU usage percentage (two GetSystemTimes samples)."""
    try:
        import ctypes  # noqa: PLC0415 - stdlib, lazy

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                        ("dwHighDateTime", ctypes.c_uint32)]

        kernel32 = ctypes.windll.kernel32
        idle_t, kern_t, user_t = FILETIME(), FILETIME(), FILETIME()

        def sample() -> tuple[float, float]:
            kernel32.GetSystemTimes(
                ctypes.byref(idle_t), ctypes.byref(kern_t), ctypes.byref(user_t))
            idle = (idle_t.dwHighDateTime << 32) + idle_t.dwLowDateTime
            kern = (kern_t.dwHighDateTime << 32) + kern_t.dwLowDateTime
            user = (user_t.dwHighDateTime << 32) + user_t.dwLowDateTime
            return idle, kern + user

        i1, k1 = sample()
        time.sleep(0.25)
        i2, k2 = sample()
        total = (k2 - k1) - (i2 - i1)
        busy = (k2 - k1) - (i2 - i1)
        return 100.0 * busy / total if total > 0 else 0.0
    except Exception:  # noqa: BLE001 - best-effort telemetry
        return 0.0


def _win_memory() -> tuple[float, float]:
    """Return (available, total) memory in GiB (GlobalMemoryStatusEx)."""
    try:
        import ctypes  # noqa: PLC0415 - stdlib, lazy

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            return (mem.ullAvailPhys / (1024 ** 3),
                    mem.ullTotalPhys / (1024 ** 3))
    except Exception:  # noqa: BLE001 - best-effort telemetry
        pass
    return 0.0, 0.0


def _win_battery() -> int | None:
    """Battery capacity as a percentage (GetSystemPowerStatus), else None."""
    try:
        import ctypes  # noqa: PLC0415 - stdlib, lazy

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            if status.BatteryLifePercent not in (255, 0):
                return int(status.BatteryLifePercent)
    except Exception:  # noqa: BLE001 - best-effort telemetry
        pass
    return None


def _win_net_rx_tx() -> tuple[int, int]:
    """Return cumulative (rx, tx) bytes via psutil (best effort)."""
    try:
        import psutil  # noqa: PLC0415 - optional on Windows

        counters = psutil.net_io_counters()
        return int(counters.bytes_recv), int(counters.bytes_sent)
    except Exception:  # noqa: BLE001 - best-effort telemetry
        return 0, 0


def _win_disk_root() -> Path:
    """A filesystem path whose volume represents the system drive."""
    try:
        return Path.home().anchor
    except Exception:  # noqa: BLE001
        return Path("C:\\")


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
    if is_windows():
        return _win_uptime()
    return read_float("/proc/uptime")


def sys_hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "localhost"


def sys_cpu_load() -> float:
    """Instantaneous CPU usage as a percentage (two 250ms samples)."""
    if is_windows():
        return _win_cpu_load()

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
    if is_windows():
        return _win_memory()
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
    if is_windows():
        return _win_battery()
    for p in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        cap = read_int(str(p / "capacity"), -1)
        if cap >= 0:
            return cap
    return None


def sys_cpu_temp() -> float | None:
    """CPU temperature in °C, or None if unavailable.

    Windows has no portable stdlib readout; ``psutil.sensors_temperatures()``
    is used when available, otherwise None.
    """
    if is_windows():
        try:
            import psutil  # noqa: PLC0415 - optional on Windows

            for temps in psutil.sensors_temperatures().values():
                if temps and temps[0].current:
                    return float(temps[0].current)
        except Exception:  # noqa: BLE001 - best-effort telemetry
            pass
        return None
    temp = read_float("/sys/class/thermal/thermal_zone0/temp", -1)
    return temp / 1000.0 if temp >= 0 else None


def sys_disk() -> tuple[float, float]:
    """Return (used-percent, free-GiB) for the system drive."""
    root = _win_disk_root() if is_windows() else Path("/")
    usage = shutil.disk_usage(root)
    return usage.used / usage.total * 100.0, usage.free / (1024 ** 3)


def sys_net_rx_tx() -> tuple[int, int]:
    """Return cumulative (rx, tx) bytes across all interfaces except loopback."""
    if is_windows():
        return _win_net_rx_tx()
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
