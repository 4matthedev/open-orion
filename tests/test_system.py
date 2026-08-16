"""Unit tests for the shared system-telemetry helpers."""

from orion.core.system import (
    read_float,
    read_int,
    sys_battery,
    sys_cpu_load,
    sys_disk,
    sys_hostname,
    sys_memory,
    sys_net_rx_tx,
    sys_uptime,
)


def test_read_float():
    assert read_float("/proc/uptime", 0.0) > 0.0
    assert read_float("/nonexistent/path", 7.5) == 7.5


def test_read_int():
    assert read_int("/proc/uptime", 0) >= 0
    assert read_int("/nonexistent/path", 3) == 3


def test_uptime_positive():
    assert sys_uptime() > 0.0


def test_hostname_is_nonempty():
    assert sys_hostname()


def test_cpu_load_bounds():
    load = sys_cpu_load()
    assert 0.0 <= load <= 100.0


def test_memory_shape():
    available, total = sys_memory()
    assert total > 0.0
    assert 0.0 <= available <= total + 1.0


def test_battery_optional():
    value = sys_battery()
    assert value is None or 0 <= value <= 100


def test_disk_shape():
    used_pct, free_gb = sys_disk()
    assert 0.0 <= used_pct <= 100.0
    assert free_gb >= 0.0


def test_net_counters_are_counts():
    rx, tx = sys_net_rx_tx()
    assert rx >= 0
    assert tx >= 0
