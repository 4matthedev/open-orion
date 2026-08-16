"""Unit tests for the cross-platform helpers."""

from orion.platform import (
    data_dir,
    is_linux,
    is_windows,
    kokoro_dir,
    piper_voices_dir,
    screenshots_dir,
    shell_args,
    shell_name,
    syntax_check_args,
)


def test_os_detection_consistency():
    assert (is_windows() is True) != (is_linux() is True)


def test_shell_name_matches_platform():
    if is_windows():
        assert shell_name() == "powershell"
    else:
        assert shell_name() == "bash"


def test_shell_args_uses_shell_name():
    args = shell_args("echo hi")
    assert isinstance(args, list)
    assert args[-1] == "echo hi"


def test_syntax_check_windows():
    if is_windows():
        assert syntax_check_args("echo hi") is None
    else:
        args = syntax_check_args("echo hi")
        assert args is not None
        assert "bash" in args and "-n" in args


def test_data_dir_is_below_home_or_appdata():
    d = data_dir()
    assert str(d).endswith("open-orion")
    if is_windows():
        assert "AppData" in str(d) or "LOCALAPPDATA" in str(d).upper()
    else:
        assert ".local" in str(d)


def test_derived_dirs():
    assert screenshots_dir().name == "screenshots"
    assert kokoro_dir().name == "kokoro"
    assert piper_voices_dir().name == "piper_voices"
    for d in (screenshots_dir(), kokoro_dir(), piper_voices_dir()):
        assert "open-orion" in str(d)
