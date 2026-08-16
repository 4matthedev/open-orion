"""Unit tests for the sandboxed execution layer (shell + file tools)."""

from orion.core.config import AppSettings
from orion.core.executor import Executor


def _executor(**overrides) -> Executor:
    return Executor(AppSettings(**overrides))


def test_run_echo():
    result = _executor().run_shell("echo hello orion")
    assert result.returncode == 0
    assert result.stdout.strip() == "hello orion"
    assert result.timed_out is False


def test_run_stderr_capture():
    result = _executor().run_shell("echo oops >&2")
    assert result.returncode == 0
    assert result.stderr.strip() == "oops"


def test_bash_syntax_error_rejected_before_run():
    result = _executor().run_shell("if then fi")
    assert result.returncode == 2
    assert "syntax" in result.stderr.lower()


def test_output_truncated():
    ex = _executor(max_output_chars=10)
    result = ex.run_shell("printf '0123456789ABCDEF'")
    assert result.truncated is True
    assert "truncated" in result.stdout


def test_read_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello file")
    out = _executor().read_file(str(f))
    assert out == "hello file"


def test_read_missing_file(tmp_path):
    out = _executor().read_file(str(tmp_path / "nope.txt"))
    assert out.startswith("error:")


def test_list_dir(tmp_path):
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "a.txt").write_text("")
    out = _executor().list_dir(str(tmp_path))
    assert "listing" in out
    assert "a.txt" in out
    assert "b.txt" in out


def test_empty_command_is_safe_noop():
    result = _executor().run_shell("   ")
    assert result.returncode == 0
