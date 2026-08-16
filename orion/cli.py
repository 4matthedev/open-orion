"""REPL control loop: user input -> LLM -> guard -> confirm -> execute -> feedback."""

from __future__ import annotations

import argparse
import os

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax

from . import __version__
from .agent import Agent
from .config import AppSettings, get_settings
from .executor import ExecResult, Executor
from .llm import LLMError, get_provider
from .memory import Memory
from .models import ActionRequest
from .platform import shell_name
from .voice import Voice, VoiceError

HELP_TEXT = """\
Commands:
  <natural language>  talk to the agent
  /talk              toggle voice mode (mic input + spoken replies)
  /mic               list mics, probe levels, pick one (or 'auto')
  /remember <note>   save a note to permanent memory
  /memory            list permanent memory
  /forget <id>       remove a permanent memory note
  /help              show this help
  /status            show provider / model / safety status
  /model <name>      hot-swap the model (e.g. /model llama3.1:8b)
  /context           print current conversation history
  /clear             reset conversation history
  /exit              quit
"""


class TalkState:
    """Runtime voice mode flag plus a speak() shortcut."""

    def __init__(self, voice: Voice | None) -> None:
        self.voice = voice
        self.active = False

    def speak(self, text: str) -> None:
        if self.active and self.voice and text:
            self.voice.speak(text)


class CLIHost:
    """Renders agent events (and asks for confirmation) in the terminal."""

    _LOGGED = {"reason": "dim", "ok": "green", "warn": "yellow", "err": "red"}
    _SH_LEXER = shell_name()

    def __init__(self, console: Console, talk: TalkState) -> None:
        self.console = console
        self.talk = talk

    def say(self, kind: str, text: str) -> None:
        if kind == "done":
            self.console.print(f"[green]✓[/green] {text}")
        else:
            self.console.print(f"[cyan]Orion:[/cyan] {text}")

    def speak(self, text: str) -> None:
        if text:
            self.talk.speak(text)

    def log(self, tag: str, text: str) -> None:
        color = self._LOGGED.get(tag, "dim")
        self.console.print(f"[{color}]{text}[/{color}]")

    def show_command(self, command: str, explanation: str) -> None:
        self.console.print(Panel(f"[bold]{explanation}[/bold]",
                                 title="Orion", border_style="cyan"))
        self.console.print(Syntax(command, self._SH_LEXER))

    def show_output(self, title: str, body: str) -> None:
        self.console.print(Panel(body, title=title, border_style="dim"))

    def show_screenshot(self, path: str) -> None:
        self.console.print(f"[dim]Screenshot: {path}[/dim]")

    def show_result(self, result: ExecResult, command: str) -> None:
        body = "\n".join(part for part in (result.stdout.rstrip(),
                                           result.stderr.rstrip()) if part)
        if body:
            self.console.print(Panel(body, title=f"$ {command[:60]}",
                                     border_style="dim"))
        color = "red" if result.returncode != 0 else "green"
        status = f"[{color}]{result.returncode}[/{color}]"
        extras = []
        if result.timed_out:
            extras.append("[red]timed out[/red]")
        if result.truncated:
            extras.append("[yellow]output truncated[/yellow]")
        suffix = f" {' '.join(extras)}" if extras else ""
        self.console.print(f"exit={status} elapsed={result.elapsed:.2f}s{suffix}")

    def confirm(self, command: str, level: str, reason: str) -> bool:
        self.console.print(
            f"[yellow]Risk ({level.upper()}): {reason}[/yellow]")
        self.console.print(Syntax(command, self._SH_LEXER))
        default = "n" if level == "risky" else "y"
        answer = Prompt.ask("Run this command?",
                            choices=["y", "n"], default=default)
        return answer == "y"

    def finish(self) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orion",
        description="Open Orion — autonomous Linux shell agent",
    )
    parser.add_argument("--provider", choices=["auto", "ollama", "api"], default=None)
    parser.add_argument("--model", default=None, help="override the active model")
    parser.add_argument("--dry-run", action="store_true", help="print commands but never execute")
    parser.add_argument("--auto", action="store_true",
                        help="fully autonomous: never prompt for confirmation "
                             "(catastrophic hard-blocks still enforced)")
    parser.add_argument("--voice", action="store_true", help="start with talk mode enabled (mic + spoken replies)")
    parser.add_argument("--cwd", default=None, help="working directory for generated commands")
    parser.add_argument("--version", action="version", version=f"open-orion {__version__}")
    args = parser.parse_args(argv)

    console = Console()

    settings = get_settings()
    if args.provider:
        settings.provider = args.provider
    if args.model:
        settings.ollama_model = args.model
        settings.api_model = args.model
    if args.dry_run:
        settings.dry_run = True
    if args.auto:
        settings.safety_level = "auto"
    if args.cwd:
        settings.working_dir = args.cwd

    if settings.working_dir:
        try:
            os.chdir(settings.working_dir)
        except OSError as exc:
            console.print(f"[red]cannot chdir to {settings.working_dir}: {exc}[/red]")
            return 2

    try:
        provider = get_provider(settings)
    except LLMError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    executor = Executor(settings)

    voice = None
    if args.voice or settings.voice_enabled:
        try:
            voice = Voice(settings)
            console.print("[green]voice engine ready[/green]")
        except VoiceError as exc:
            console.print(f"[yellow]voice unavailable: {exc}[/yellow]")
            voice = None

    console.print(
        Panel(
            f"[bold]Open Orion[/bold] v{__version__}\n"
            f"provider: {provider.name}\n"
            f"model:    {getattr(provider, 'model', '-')}\n"
            f"safety:   {settings.safety_level}\n"
            f"dry-run:  {settings.dry_run}\n"
            f"cwd:      {os.getcwd()}",
            title="Open Orion",
        )
    )

    try:
        run_repl(console, settings, provider, executor, voice, args.voice)
    finally:
        provider.close()
    return 0


def run_repl(
    console: Console,
    settings: AppSettings,
    provider,
    executor: Executor,
    voice: Voice | None = None,
    voice_on: bool = False,
) -> None:
    talk = TalkState(voice)
    talk.active = voice_on and voice is not None
    if talk.active:
        talk.speak("Voice mode enabled. I'm listening.")

    agent = Agent(settings, provider, executor,
                  memory=Memory(), host=CLIHost(console, talk))

    while True:
        if talk.active:
            try:
                line = Prompt.ask("[bold cyan]talk[/bold cyan] [dim](Enter to speak)[/dim]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/dim]")
                return
            if not line:
                console.print("[yellow]🎤 recording… (stop speaking to send)[/yellow]")
                try:
                    line = talk.voice.listen()
                except KeyboardInterrupt:
                    console.print("\n[dim]cancelled[/dim]")
                    line = ""
                except Exception as exc:  # noqa: BLE001 - mic failures shouldn't kill the REPL
                    console.print(f"[red]mic error: {exc}[/red]")
                    line = ""
                if line:
                    console.print(f"[bold yellow]🎤[/bold yellow] {line}")
                else:
                    console.print("[yellow]nothing heard — run /mic to pick the mic you speak into[/yellow]")
                    continue
        else:
            try:
                line = Prompt.ask("[bold cyan]orion[/bold cyan]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/dim]")
                return

        if not line:
            continue

        try:
            if line.startswith("/"):
                if _handle_special(console, settings, provider, agent, line, talk):
                    return
                continue

            outcome = agent.chat(line, keep_non_json=False)

            if outcome["kind"] == "error":
                console.print(f"[red]LLM error:[/red] {outcome['message']}")
                continue

            if outcome["kind"] == "text":
                action = _non_json_fallback(console, outcome["message"])
                if action is None:
                    agent.context.add(
                        "user",
                        f"[tool result] DECLINED raw model output: {outcome['message'][:500]}",
                    )
                    continue
                agent.context.add("assistant", outcome["message"])
                agent.dispatch(action)
                continue

            agent.dispatch(outcome["action"])
        except KeyboardInterrupt:
            console.print("\n[dim]bye[/dim]")
            return


def _non_json_fallback(console: Console, reply: str) -> ActionRequest | None:
    console.print(Panel(reply[:2000], title="[yellow]model returned non-JSON text[/yellow]"))
    choice = Prompt.ask("How should I interpret this?", choices=["run", "ask", "ignore"], default="ask")
    if choice == "run":
        return ActionRequest(
            action="run",
            command=reply.strip(),
            reasoning="non-json fallback",
            explanation="execute raw model output",
            risk="medium",
            requires_confirmation=True,
        )
    if choice == "ask":
        return ActionRequest(action="ask", message=reply.strip()[:2000])
    return None


def _mic_selector(console: Console, voice) -> None:
    console.print("[dim]probing microphones (speak or make noise into each)…[/dim]")
    sources = voice.list_sources()
    if not sources:
        console.print("[yellow]no capture sources found[/yellow]")
        return
    for i, src in enumerate(sources):
        rms = voice.probe(src["name"])
        marker = "  [green]← active[/green]" if voice.mic == src["name"] else ""
        console.print(f"  {i}. {src.get('description', src['name'])}  (signal {rms:.0f}){marker}")
    pick = Prompt.ask("choose mic number, or 'auto'", default="auto")
    if pick in ("", "auto"):
        name, rms = voice.auto_select_mic()
        if name:
            voice.set_mic(name)
            console.print(f"[green]auto-selected mic: {name} (signal {rms:.0f})[/green]")
        else:
            console.print("[yellow]no mic with a usable signal found[/yellow]")
        return
    try:
        name = sources[int(pick)]["name"]
        voice.set_mic(name)
        console.print(f"[green]mic set to {name}[/green]")
    except (ValueError, IndexError):
        console.print("[yellow]invalid selection[/yellow]")


def _handle_special(
    console: Console,
    settings: AppSettings,
    provider,
    agent: Agent,
    line: str,
    talk: TalkState | None = None,
) -> bool:
    parts = line.split(maxsplit=1)
    cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")

    if cmd in ("/exit", "/quit", "/q"):
        console.print("[dim]bye[/dim]")
        return True
    if cmd == "/help":
        console.print(Panel(HELP_TEXT, title="help"))
    elif cmd in ("/talk", "/voice"):
        if talk is None or talk.voice is None:
            console.print("[yellow]voice is not available[/yellow]")
        else:
            talk.active = not talk.active
            console.print(f"[green]talk {'on' if talk.active else 'off'}[/green]")
            if talk.active:
                talk.speak("Voice mode enabled. Press enter, then speak.")
    elif cmd == "/mic":
        if talk is None or talk.voice is None:
            console.print("[yellow]voice is not available[/yellow]")
        else:
            _mic_selector(console, talk.voice)
    elif cmd == "/status":
        mic_name = talk.voice.mic if talk and talk.voice else "-"
        console.print(
            Panel(
                f"provider: {provider.name}\n"
                f"model:    {getattr(provider, 'model', '-')}\n"
                f"ollama:   {settings.ollama_base_url}/{settings.ollama_model}\n"
                f"api:      {settings.api_model}\n"
                f"safety:   {settings.safety_level}"
                f"{' (always confirm)' if settings.always_confirm else ''}\n"
                f"dry-run:  {settings.dry_run}\n"
                f"talk:     {'on' if talk and talk.active else 'off'}\n"
                f"mic:      {mic_name}\n"
                f"cwd:      {os.getcwd()}",
                title="status",
            )
        )
    elif cmd == "/model":
        if not arg:
            console.print("[yellow]usage: /model <name>[/yellow]")
        else:
            provider.set_model(arg)
            console.print(f"[green]model set to {arg}[/green]")
    elif cmd == "/context":
        for message in agent.context.messages():
            console.print(f"[dim]{message['role']}:[/dim] {message['content'][:400]}")
    elif cmd == "/clear":
        agent.context.clear()
        console.print("[green]context cleared[/green]")
    elif cmd == "/remember":
        if not arg:
            console.print("[yellow]usage: /remember <note to save>[/yellow]")
        else:
            nid = agent.remember(arg)
            console.print(f"[green]remembered #{nid}: {arg}[/green]")
    elif cmd == "/memory":
        items = agent.memory.items()
        if not items:
            console.print("[dim]memory is empty[/dim]")
        else:
            console.print(Panel("\n".join(
                f"[dim]#{n['id']}[/dim] {n['text']}" for n in items
            ), title=f"permanent memory ({len(items)})"))
    elif cmd == "/forget":
        try:
            nid = int((arg.split()[0] if arg else ""))
        except (ValueError, IndexError):
            console.print("[yellow]usage: /forget <id>[/yellow]")
        else:
            if agent.forget(nid):
                console.print(f"[green]forgotten #{nid}[/green]")
            else:
                console.print(f"[yellow]no memory note #{nid}[/yellow]")
    else:
        console.print(f"[yellow]unknown command: {cmd} (try /help)[/yellow]")
    return False