"""Shared agent control-loop used by the CLI and both desktop UIs.

The LLM emits a single JSON action (``run | read | ls | screenshot |
remember | ask | done``); this module owns the conversation context, the
persistent memory, the system prompt, and — crucially — the *dispatch logic*
that turns actions into executed commands with the static risk guard applied.

A concrete user interface provides a host of thin callbacks (print a line,
append to the log, ask for confirmation, ...) via the :class:`Host`
interface. Both ``orion.cli`` and the tkinter UIs render the same code path,
so the safety/confirmation flow can never drift between them.
"""

from __future__ import annotations

import os
from typing import Protocol

from .config import AppSettings
from .context import ContextHistory
from .executor import ExecResult, Executor
from .llm import LLMError
from .memory import Memory
from .models import ActionRequest, parse_action
from .prompt import system_prompt
from .security import (
    RiskAssessment,
    classify_command,
    needs_confirmation,
    sanitize_command,
)

VISION_PROMPT = (
    "Look at this screenshot of the user's actual screen and describe what is "
    "visible, concisely and helpfully."
)


class Host(Protocol):
    """The thin surface a user interface must implement."""

    def say(self, kind: str, text: str) -> None: ...
    def speak(self, text: str) -> None: ...
    def log(self, tag: str, text: str) -> None: ...
    def show_command(self, command: str, explanation: str) -> None: ...
    def show_output(self, title: str, body: str) -> None: ...
    def show_screenshot(self, path: str) -> None: ...
    def show_result(self, result: ExecResult, command: str) -> None: ...
    def confirm(self, command: str, level: str, reason: str) -> bool: ...
    def finish(self) -> None: ...


class Agent:
    """Owns context, memory, and the system prompt; dispatches actions."""

    def __init__(
        self,
        settings: AppSettings,
        provider,
        executor: Executor,
        memory: Memory | None = None,
        host: Host | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.executor = executor
        self.memory = memory or Memory()
        self.host = host
        self.context = ContextHistory(
            max_turns=settings.max_history_turns,
            max_chars=settings.max_context_chars,
        )
        self.rebuild_system()

    def rebuild_system(self) -> None:
        self.system = system_prompt(
            self.settings, memory=self.memory.to_prompt())

    def remember(self, text: str) -> int:
        nid = self.memory.remember(text.strip())
        self.rebuild_system()
        return nid

    def forget(self, note_id: int) -> bool:
        ok = self.memory.forget(note_id)
        if ok:
            self.rebuild_system()
        return ok

    def chat(
        self,
        line: str,
        keep_non_json: bool = True,
    ) -> dict:
        """Run one LLM turn: add the user message, call the model, parse.

        Returns ``{"kind": "action", "action": ActionRequest}`` on a parseable
        reply, ``{"kind": "text", "message": str}`` when the model returned
        non-JSON prose (the raw reply is kept in context when
        ``keep_non_json``), and ``{"kind": "error", "message": str}`` on LLM
        failure. The system prompt is rebuilt each turn so the environment
        fingerprint (cwd, memory notes, ...) is always current.
        """
        self.rebuild_system()
        self.context.add("user", line)
        try:
            reply = self.provider.chat(
                self.context.messages(), system=self.system)
        except LLMError as exc:
            return {"kind": "error", "message": str(exc)}
        try:
            action = parse_action(reply)
        except ValueError:
            if keep_non_json:
                self.context.add("assistant", reply)
            return {"kind": "text", "message": reply}
        self.context.add("assistant", reply)
        return {"kind": "action", "action": action}

    # -- action execution --------------------------------------------------
    # This is the single source of truth for what an action does. The UIs
    # only render via the Host callbacks; safety/confirmation cannot drift.

    def dispatch(self, action: ActionRequest) -> None:
        host = self.host
        if action.reasoning:
            host.log("reason", action.reasoning)

        if action.action == "ask":
            host.say("ask", action.message or "How can I help?")
            host.speak(action.message)
            host.finish()
            return

        if action.action == "done":
            msg = action.message or "Done."
            host.say("done", msg)
            host.log("ok", msg)
            host.speak(msg)
            host.finish()
            return

        if action.action == "read":
            path = action.path.strip() or os.getcwd()
            content = self.executor.read_file(path)
            host.say("info", "Here is %s:" % path)
            host.show_output("read %s" % path, content)
            self.context.add("user", "[tool result] read %s:\n%s" % (path, content))
            host.finish()
            return

        if action.action == "ls":
            path = action.path.strip() or os.getcwd()
            listing = self.executor.list_dir(path)
            host.say("info", "Directory listing for %s:" % path)
            host.show_output("ls %s" % path, listing)
            self.context.add("user", "[tool result] ls %s:\n%s" % (path, listing))
            host.finish()
            return

        if action.action == "screenshot":
            self._screenshot(action.path.strip() or None)
            return

        if action.action == "remember":
            note = action.message.strip()
            if not note:
                host.say("info", "I didn't catch anything to remember.")
            else:
                nid = self.remember(note)
                host.say("info", "Noted — saved to my permanent memory [#%d]." % nid)
                host.log("ok", "remembered #%d → %s" % (nid, note))
            host.finish()
            return

        self._run(action)

    def _screenshot(self, region: str | None) -> None:
        host = self.host
        path = self.executor.screenshot(region)
        if path.startswith("error"):
            host.say("info", path)
            host.log("err", path)
            self.context.add("user", "[tool result] %s" % path)
            host.finish()
            return
        host.say("info", "Screenshot captured: %s" % path)
        host.show_screenshot(path)
        self.context.add("user", "[tool result] screenshot saved to %s" % path)
        self.look_at(path)
        host.finish()

    def look_at(self, path: str) -> None:
        """Ask the (vision) model to describe a screenshot on screen now."""
        host = self.host
        if not self.settings.vision:
            return
        model = (self.settings.vision_model or "").strip() or self.provider.model
        try:
            desc = self.provider.chat(
                [{"role": "user", "content": VISION_PROMPT, "image": path}],
                system=self.system,
                model=model,
            )
        except LLMError as exc:
            host.log("warn", "vision unavailable (%s) — screenshot saved only" % exc)
        else:
            text = desc.strip().rstrip()
            if text:
                host.say("info", "Vision: %s" % text[:600])
                host.log("ok", "vision (%s): %s" % (model, text[:200]))
                self.context.add(
                    "user",
                    "[tool result] vision analysis (%s):\n%s" % (model, text[:1200]))

    def _run(self, action: ActionRequest) -> None:
        host = self.host
        command = sanitize_command(
            action.command, max_length=self.settings.max_command_length)
        if not command:
            host.say("info", "The model returned an empty command — no action taken.")
            host.log("warn", "empty command from model")
            host.finish()
            return

        host.show_command(command, action.explanation or "Run command")

        assessment: RiskAssessment = classify_command(command)

        if assessment.level == "forbidden":
            msg = "Hard-blocked: %s — refusing to run." % assessment.reason
            host.log("err", msg)
            host.speak("I can't do that. %s." % assessment.reason)
            self.context.add(
                "user",
                "[tool result] FORBIDDEN (%s): $ %s" % (assessment.reason, command),
            )
            host.finish()
            return

        if self.settings.dry_run:
            host.log("warn", "dry-run: not executing")
            self.context.add("user", "[tool result] DRY-RUN skipped: %s" % command)
            host.finish()
            return

        if needs_confirmation(
            self.settings, assessment.level, action.requires_confirmation
        ):
            if not host.confirm(command, assessment.level, assessment.reason):
                host.log("warn", "declined by user")
                self.context.add(
                    "user", "[tool result] DECLINED by user: $ %s" % command)
                host.finish()
                return

        result = self.executor.run_shell(command)
        host.show_result(result, command)
        self.context.add("user", result_feedback(command, result))
        host.speak(voice_summary(action, result))
        host.finish()


def result_feedback(command: str, result: ExecResult) -> str:
    head = "[tool result]\n$ %s\nexit=%d elapsed=%.2fs" % (
        command, result.returncode, result.elapsed)
    if result.timed_out:
        head += " (timed out)"
    return "%s\nstdout:\n%s\nstderr:\n%s" % (
        head, result.stdout, result.stderr)


def voice_summary(action: ActionRequest, result: ExecResult) -> str:
    summary = action.explanation or "Done."
    if result.stdout.strip():
        first = next(
            (ln for ln in result.stdout.splitlines() if ln.strip()), "")
        if first:
            summary += " Result: %s" % first[:120]
    summary += " Exit code %d." % result.returncode
    return summary


__all__ = ["Agent", "Host", "result_feedback", "voice_summary"]