"""Sandboxed execution layer.

Runs generated Bash commands in a subprocess with a hard timeout, captures and
size-truncates stdout/stderr, and exposes safe structured file tools (read/list).
The security policy itself lives in ``orion.security``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AppSettings
from .security import sanitize_command


@dataclass
class ExecResult:
    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    elapsed: float = 0.0
    timed_out: bool = False
    truncated: bool = False
    dry_run: bool = False


class Executor:
    """Owns subprocess execution and structured file tools."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    # -- shell execution ------------------------------------------------

    def run_shell(self, command: str, timeout: int | None = None, cwd: str | None = None) -> ExecResult:
        command = sanitize_command(command, max_length=self.settings.max_command_length)
        timeout = timeout or self.settings.shell_timeout
        cwd = cwd or self.settings.working_dir or os.getcwd()

        if command:
            check = subprocess.run(
                ["bash", "-n", "-c", command],
                capture_output=True,
                text=True,
            )
            if check.returncode != 0:
                return ExecResult(
                    command=command,
                    stderr=check.stderr.strip() or "bash syntax error",
                    returncode=2,
                    elapsed=0.0,
                )

        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or "", exc.stderr or ""
            returncode = 124
            timed_out = True

        elapsed = time.monotonic() - started
        truncated = len(stdout) > self.settings.max_output_chars or len(stderr) > self.settings.max_output_chars
        return ExecResult(
            command=command,
            stdout=self._truncate(stdout),
            stderr=self._truncate(stderr),
            returncode=returncode,
            elapsed=elapsed,
            timed_out=timed_out,
            truncated=truncated,
        )

    def _truncate(self, text: str) -> str:
        limit = self.settings.max_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n… output truncated at {limit} chars"

    # -- structured file tools ------------------------------------------

    def screenshot(self, region: str | None = None, timeout: int = 15) -> str:
        """Capture the screen to a PNG and return its absolute path.

        Uses ``grim`` on Wayland (with an optional ``slurp`` region like
        ``1920x1080+0+0``) and falls back to ImageMagick's ``import`` on X.
        Returns an ``error: ...`` string on failure.
        """
        out_dir = Path.home() / ".local/share/open-orion/screenshots"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"error creating screenshot dir: {exc}"
        out = out_dir / ("orion-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
        region = (region or "").strip()
        try:
            if shutil.which("grim"):
                cmd = ["grim"]
                if region and region.lower() not in ("fullscreen", "screen",
                                                     "all"):
                    cmd += ["-g", region]
                cmd.append(str(out))
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout)
                if proc.returncode != 0 and region:
                    cmd = ["grim", str(out)]
                    proc = subprocess.run(cmd, capture_output=True,
                                          text=True, timeout=timeout)
            elif shutil.which("import"):
                cmd = ["import", "-window", "root", str(out)]
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout)
            else:
                return ("error: no screenshot tool found — install grim "
                        "(Wayland) or ImageMagick's 'import' (X11)")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: screenshot failed: {exc}"
        if proc.returncode != 0 or not out.exists():
            return "error: screenshot failed: %s" % proc.stderr.strip()
        try:
            size = out.stat().st_size
        except OSError:
            size = 0
        if size <= 0:
            out.unlink(missing_ok=True)
            return "error: screenshot produced an empty image"
        return str(out)

    def read_file(self, path: str, limit: int | None = None) -> str:
        limit = limit or self.settings.max_output_chars
        try:
            target = Path(path).expanduser().resolve()
        except OSError as exc:
            return f"error resolving path: {exc}"
        if not target.exists():
            return f"error: {target} does not exist"
        if target.is_dir():
            return f"error: {target} is a directory (use ls)"
        try:
            raw = target.read_bytes()
        except OSError as exc:
            return f"error reading {target}: {exc}"
        text = raw.decode("utf-8", errors="replace")
        if len(text) > limit:
            text = text[:limit] + f"\n… output truncated at {limit} chars"
        return text

    def list_dir(self, path: str, limit: int = 200) -> str:
        try:
            target = Path(path).expanduser().resolve()
        except OSError as exc:
            return f"error resolving path: {exc}"
        if not target.exists():
            return f"error: {target} does not exist"
        if not target.is_dir():
            return f"error: {target} is not a directory"
        try:
            entries = sorted(target.iterdir(), key=lambda e: (e.is_dir() is False, e.name.lower()))
        except OSError as exc:
            return f"error listing {target}: {exc}"

        lines = [f"listing {target}:"]
        for entry in entries[:limit]:
            try:
                size = f"{entry.stat().st_size:,}" if entry.is_file() else ""
            except OSError:
                size = ""
            lines.append(f"  {'[dir]' if entry.is_dir() else '[file]'} {entry.name} {size}")
        if len(entries) > limit:
            lines.append(f"  … and {len(entries) - limit} more entries")
        return "\n".join(lines)
