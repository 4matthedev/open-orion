"""Open Orion — JARVIS-style desktop UI (pure stdlib, tkinter).

A futuristic command deck for the Open Orion agent: boot sequence, live
conversation transcript, a terminal-style command log, a pulsing status orb,
voice toggle and slash-command support — all built with tkinter only.

Run with:  python -m orion.gui [options]
or:        python main.py --gui [options]
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

from . import __version__
from .agent import Agent
from .config import AppSettings, get_settings
from .executor import Executor
from .llm import LLMError, get_provider
from .memory import Memory
from .models import ActionRequest
from .themes import (
    ThemeError,
    load_theme,
    read_saved_theme,
    resolve_theme_name,
    save_theme,
    themes_list,
)
from .voice import Voice, VoiceError

# --------------------------------------------------------------------------
# Theme — palette resolved at startup from --theme / ORION_THEME / .env
# --------------------------------------------------------------------------

def apply_theme(cli: str | None = None, configured: str | None = None,
                default: str = "jarvis") -> None:
    """Load a palette and re-bind the module color constants.

    Called at import time (environment/defaults) and again from ``main()``
    once the CLI args and settings are known, so ``--theme``/``ORION_THEME``
    always win over an earlier import-time binding.
    """
    global BG, BG_PANEL, BG_EDGE, FG, CYAN, CYAN_DIM, BLUE
    global AMBER, GOLD, GREEN, RED, DIM, BOOT_LINES
    theme = load_theme(resolve_theme_name(cli, configured, default=default))
    BG         = theme.bg
    BG_PANEL   = theme.bg_panel
    BG_EDGE    = theme.bg_edge
    FG         = theme.fg
    CYAN       = theme.accent
    CYAN_DIM   = theme.accent_dim
    BLUE       = theme.accent_2
    AMBER      = theme.warn
    GOLD       = theme.command
    GREEN      = theme.ok
    RED        = theme.error
    DIM        = theme.muted
    BOOT_LINES = [
        ("# INITIALIZING OPEN ORION v%s" % __version__, CYAN),
        ("[ OK ] neural matrix loaded", GREEN),
        ("[ OK ] shell interface linked", GREEN),
        ("[ OK ] security grid armed", GREEN),
        ("[ OK ] memory buffer configured", GREEN),
    ]


apply_theme()

MONO      = "DejaVu Sans Mono"
SANS      = "DejaVu Sans"

GESTURE = (
    "Open Orion is running. All systems nominal. How may I assist you, sir?"
)


class Orb(tk.Canvas):
    """A breathing HUD status light."""

    def __init__(self, master, size: int = 26, **kw) -> None:
        super().__init__(master, width=size, height=size, highlightthickness=0,
                         bg=BG, **kw)
        self._size = size
        self._r = size // 2 - 2
        self._phase = 0.0
        self._busy = False
        self._stop = False
        self._after = self.after(50, self._tick)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    def destroy(self) -> None:  # noqa: D102
        self._stop = True
        if self._after:
            try:
                self.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        super().destroy()

    def _tick(self) -> None:
        if self._stop:
            return
        self.delete("all")
        self._phase += 0.12 if self._busy else 0.035
        if self._busy:
            depth = 0.35 + 0.65 * abs(time.time() % 0.9 / 0.9 - 0.5) * 2
            speed = 1.6 if int(time.time() * 2) % 2 else 1.0
            r = self._r * (0.9 + 0.1 * speed)
            hue = "#3fd9ff"
            color = _fade(CYAN, "#ffffff", 0.2 + 0.8 * depth)
        else:
            depth = 0.5 + 0.5 * (0.5 + 0.5 * _sin(self._phase))
            r = self._r * 0.85
            color = _fade(BG_EDGE, CYAN, depth)
            hue = CYAN_DIM
        halo = self._size // 2
        self.create_oval(halo - r, halo - r, halo + r, halo + r,
                         fill=color, outline="")
        self.create_oval(halo - r * 0.35, halo - r * 0.35,
                         halo + r * 0.35, halo + r * 0.35,
                         fill=_fade(BG, "#ffffff", depth * 0.8), outline="")
        self._after = self.after(50, self._tick)


def _sin(x: float) -> float:
    """Cheap sin approximation to avoid importing math in the hot loop."""
    x = x % (2 * 3.14159)
    if x < 3.14159:
        return 4 * x * (3.14159 - x) / (3.14159 * 3.14159)
    x -= 3.14159
    return -4 * x * (3.14159 - x) / (3.14159 * 3.14159)


def _fade(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ia = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    ib = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(int(ia[i] + (ib[i] - ia[i]) * t) for i in range(3))
    return "#%02x%02x%02x" % rgb


class Log:
    """Thread-safe, tag-aware text panel."""

    def __init__(self, text: tk.Text, tags: dict[str, dict],
                 recorder: list | None = None) -> None:
        self.text = text
        self.tags = tags
        self.recorder = recorder
        self._see = True
        for name, cfg in tags.items():
            text.tag_configure(name, **cfg)
        text.tag_configure("sep", foreground=BG_EDGE, font=(MONO, 8))
        text.configure(state="disabled", cursor="arrow")

    def append(self, tag: str, content: str, *, prefix: str = "") -> None:
        self.text.configure(state="normal")
        if prefix:
            self.text.insert("end", prefix + " ", (tag, "prefix"))
        for i, line in enumerate(content.rstrip().split("\n")):
            self.text.insert("end", line + "\n", tag)
        if self._see:
            self.text.see("end")
        self.text.configure(state="disabled")
        if self.recorder is not None:
            self.recorder.append((tag, content, prefix))


class OrionGUI(tk.Tk):
    """The command deck."""

    def __init__(self, settings: AppSettings, provider, executor: Executor,
                 voice: Voice | None) -> None:
        super().__init__()
        self.settings = settings
        self.provider = provider
        self.executor = executor
        self.voice = voice
        self.agent = Agent(settings, provider, executor,
                           memory=Memory(), host=self)
        self.context = self.agent.context
        self.memory = self.agent.memory
        self.busy = False
        self.talk_active = False
        self._q: queue.Queue = queue.Queue()
        self._nonce = 0
        self._transcript: list[tuple[str, str]] = []
        self._logbuf: list[tuple[str, str, str]] = []
        self._settings_win: tk.Toplevel | None = None

        self.title("OPEN ORION — Command Deck")
        self.configure(bg=BG)
        self.geometry("1000x720")
        self.minsize(720, 480)
        self._style_fonts()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(400, self._run_boot)
        self.after(60, self._poll_queue)

    # ------------------------------------------------------------------ UI

    def _style_fonts(self) -> None:
        self.f_mono = tkfont.Font(family=MONO, size=10)
        self.f_mono_b = tkfont.Font(family=MONO, size=10, weight="bold")
        self.f_title = tkfont.Font(family=SANS, size=16, weight="bold")
        self.f_small = tkfont.Font(family=SANS, size=9)
        self.f_input = tkfont.Font(family=SANS, size=12)

    def _build(self) -> None:
        self._header()
        self._body()
        self._footer()

    def _header(self) -> None:
        bar = tk.Frame(self, bg=BG, padx=16, pady=10)
        bar.pack(side="top", fill="x")

        title = tk.Label(bar, text="OPEN ORION", font=self.f_title,
                         fg=CYAN, bg=BG, anchor="w")
        title.pack(side="left")

        tk.Label(bar, text="COMMAND DECK", font=self.f_small,
                 fg=DIM, bg=BG).pack(side="left", padx=(10, 0), pady=(6, 0))

        self.orb = Orb(bar)
        self.orb.pack(side="right")

        self.stat_label = tk.Label(bar, text=self._status_text(),
                                   font=self.f_small, fg=DIM, bg=BG,
                                   anchor="e")
        self.stat_label.pack(side="right", padx=(0, 12), pady=(6, 0))

        tk.Frame(self, bg=CYAN_DIM, height=1).pack(side="top", fill="x")

    def _body(self) -> None:
        main = tk.PanedWindow(self, bg=BG, sashwidth=4, sashrelief="flat",
                              orient="horizontal")
        main.pack(side="top", fill="both", expand=True, padx=8, pady=6)

        left = tk.Frame(main, bg=BG_PANEL, highlightbackground=BG_EDGE,
                        highlightthickness=1)
        right = tk.Frame(main, bg=BG_PANEL, highlightbackground=BG_EDGE,
                         highlightthickness=1)
        main.add(left, minsize=320)
        main.add(right, minsize=320)

        # -- chat transcript ------------------------------------------------
        tk.Label(left, text="◆ CONVERSATION", font=self.f_small, fg=CYAN,
                 bg=BG_PANEL, anchor="w", padx=8, pady=4).pack(fill="x")
        self.chat = tk.Text(left, bg=BG_PANEL, fg=FG, font=self.f_mono,
                            wrap="word", bd=0, padx=10, pady=8,
                            highlightthickness=0, insertbackground=CYAN)
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat_sb = tk.Scrollbar(left, command=self.chat.yview,
                                    troughcolor=BG, bg=BG_EDGE,
                                    activebackground=CYAN, width=10)
        self.chat_sb.pack(side="right", fill="y")
        self.chat.configure(yscrollcommand=self.chat_sb.set)
        self.chat.tag_configure("you", foreground=BLUE,
                                font=self.f_mono_b, spacing1=6, spacing3=6)
        self.chat.tag_configure("orion", foreground=CYAN,
                                font=self.f_mono_b, spacing1=6, spacing3=6)
        self.chat.tag_configure("body", foreground=FG, font=self.f_mono)
        self.chat.tag_configure("sys", foreground=DIM, font=self.f_mono,
                                spacing1=4)
        self.chat.tag_configure("who", foreground=DIM, font=self.f_small)
        self.chat.tag_configure("hr", foreground=BG_EDGE, font=(MONO, 8))
        self.chat.configure(state="disabled")

        # -- command / output log ------------------------------------------
        tk.Label(right, text="◈ SYSTEM LOG", font=self.f_small, fg=AMBER,
                 bg=BG_PANEL, anchor="w", padx=8, pady=4).pack(fill="x")
        self.log_text = tk.Text(right, bg=BG_PANEL, fg=FG, font=self.f_mono,
                                wrap="word", bd=0, padx=10, pady=8,
                                highlightthickness=0, insertbackground=CYAN)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_sb = tk.Scrollbar(right, command=self.log_text.yview,
                                   troughcolor=BG, bg=BG_EDGE,
                                   activebackground=CYAN, width=10)
        self.log_sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=self.log_sb.set)
        self.log = Log(self.log_text, {
            "boot":     {"foreground": DIM},
            "ok":       {"foreground": GREEN},
            "warn":     {"foreground": GOLD},
            "err":      {"foreground": RED},
            "info":     {"foreground": CYAN},
            "cmd":      {"foreground": AMBER, "font": self.f_mono_b},
            "out":      {"foreground": GREEN},
            "stderr":   {"foreground": GOLD},
            "exit_ok":  {"foreground": GREEN, "font": self.f_small},
            "exit_err": {"foreground": RED, "font": self.f_small},
            "sep":      {"foreground": BG_EDGE},
        }, recorder=self._logbuf)

    def _footer(self) -> None:
        foot = tk.Frame(self, bg=BG, padx=10, pady=8)
        foot.pack(side="bottom", fill="x")

        btns = tk.Frame(foot, bg=BG)
        btns.pack(side="left", fill="y")

        self.talk_btn = self._button(btns, "🎤 TALK", self._toggle_talk,
                                     accent=CYAN)
        self.talk_btn.pack(side="left", padx=(0, 4))
        self.clear_btn = self._button(btns, "CLEAR", self._cmd_clear)
        self.clear_btn.pack(side="left", padx=4)
        self.status_btn = self._button(btns, "STATUS", self._cmd_status)
        self.status_btn.pack(side="left", padx=4)
        self.help_btn = self._button(btns, "HELP", self._cmd_help)
        self.help_btn.pack(side="left", padx=4)
        self.settings_btn = self._button(btns, "⚙ SETTINGS",
                                         self._toggle_settings)
        self.settings_btn.pack(side="left", padx=4)

        self.entry = tk.Entry(foot, font=self.f_input, bg=BG_PANEL, fg=FG,
                              insertbackground=CYAN, relief="flat", bd=0,
                              highlightbackground=BG_EDGE,
                              highlightcolor=CYAN, highlightthickness=1,
                              disabledbackground=BG_PANEL,
                              disabledforeground=DIM)
        self.entry.pack(side="left", fill="x", expand=True, padx=8)
        self.entry.bind("<Return>", self._on_submit)

        self.send_btn = self._button(foot, "TRANSMIT", self._on_submit,
                                     accent=GOLD)
        self.send_btn.pack(side="left")

        self.entry.focus_set()

    def _button(self, master, text: str, cmd, accent: str | None = None) -> tk.Button:
        btn = tk.Button(master, text=text, command=cmd, bg=BG_PANEL,
                        fg=accent or CYAN, activebackground=BG_EDGE,
                        activeforeground=CYAN, relief="flat", bd=0,
                        font=self.f_small, padx=10, pady=5, cursor="hand2",
                        highlightbackground=BG_EDGE, highlightthickness=1)
        btn.bind("<Enter>", lambda e, b=btn, a=accent: b.configure(fg=a or GOLD))
        btn.bind("<Leave>", lambda e, b=btn, a=accent: b.configure(fg=a or CYAN))
        return btn

    # ------------------------------------------------------- boot sequence

    def _run_boot(self) -> None:
        self._boot_idx = 0
        self._boot()

    def _boot(self) -> None:
        idx = self._boot_idx
        if idx < len(BOOT_LINES):
            text, tag = BOOT_LINES[idx]
            self.log.append(tag, text)
            self.log.append("sep", "─" * 60)
            self._boot_idx += 1
            self.after(260, self._boot)
            return
        if self.voice is None:
            self.log.append("warn", "[!!] voice engine offline")
        else:
            self.log.append("ok", "[ OK ] voice engine online")
        self.log.append("sep", "─" * 60)
        self.log.append("info", "provider: %s  model: %s" % (
            self.provider.name, getattr(self.provider, "model", "-")))
        self.log.append("info", "cwd:      %s" % os.getcwd())
        self.log.append("sep", "─" * 60)
        self._chat("orion", GESTURE)

    # -------------------------------------------------------- status / info

    def _status_text(self) -> str:
        return "%s · %s · talk:%s" % (
            self.provider.name,
            getattr(self.provider, "model", "-"),
            "ON" if self.talk_active else "off",
        )

    def _refresh_status(self) -> None:
        self.stat_label.configure(text=self._status_text())

    def _cmd_status(self) -> None:
        lines = [
            ("sys", "provider:  %s" % self.provider.name),
            ("sys", "model:     %s" % getattr(self.provider, "model", "-")),
            ("sys", "ollama:    %s/%s" % (self.settings.ollama_base_url,
                                          self.settings.ollama_model)),
            ("sys", "api:       %s" % self.settings.api_model),
            ("sys", "safety:    %s" % self.settings.safety_level),
            ("sys", "dry-run:   %s" % self.settings.dry_run),
            ("sys", "vision:    %s" % ("on" if self.settings.vision else "off")),
            ("sys", "memory:    %d note(s)" % len(self.memory)),
            ("sys", "talk:      %s" % ("on" if self.talk_active else "off")),
            ("sys", "mic:       %s" % (self.voice.mic if self.voice else "-")),
            ("sys", "cwd:       %s" % os.getcwd()),
            ("sys", "turns:     %s" % len(self.context)),
        ]
        for tag, text in lines:
            self.log.append(tag, text)

    def _cmd_help(self) -> None:
        for line in (
            "COMMANDS",
            "  <text>            talk to Orion",
            "  /talk             toggle voice mode",
            "  /mic              list / auto-pick microphone",
            "  /status           provider · model · safety",
            "  /model <name>     hot-swap the model",
            "  /remember <note>  save a note to permanent memory",
            "  /memory           list permanent memory",
            "  /forget <id>      remove a permanent memory note",
            "  /settings         open the control deck (palette + options)",
            "  /context          print conversation history",
            "  /clear            reset conversation history",
            "  /help             show this help",
            "  /exit             quit",
        ):
            self.log.append("info", line)

    def _cmd_clear(self) -> None:
        self.context.clear()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self.log.append("ok", "context cleared")

    def _cmd_model(self, name: str) -> None:
        self.provider.set_model(name)
        self.log.append("ok", "model set to %s" % name)
        self._refresh_status()

    # ---------------------------------------------------------- settings deck

    def _toggle_settings(self) -> None:
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._close_settings()
            return
        self._open_settings()

    def _close_settings(self) -> None:
        if self._settings_win is not None:
            try:
                self._settings_win.destroy()
            except tk.TclError:
                pass
            self._settings_win = None

    def _open_settings(self) -> None:
        self._close_settings()
        win = tk.Toplevel(self)
        self._settings_win = win
        win.title("OPEN ORION — Control Deck")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self)

        panel = tk.Frame(win, bg=BG_PANEL, padx=14, pady=12,
                         highlightbackground=BG_EDGE, highlightthickness=1)
        panel.pack(padx=10, pady=10)

        tk.Label(panel, text="⚙ CONTROL DECK", font=self.f_title, fg=CYAN,
                 bg=BG_PANEL, anchor="w").pack(fill="x")
        tk.Frame(panel, bg=CYAN_DIM, height=1).pack(fill="x", pady=(6, 10))

        # --- palette picker (preselected colors) ---------------------------
        tk.Label(panel, text="PALETTE", font=self.f_small, fg=CYAN,
                 bg=BG_PANEL, anchor="w").pack(fill="x")
        current = self._current_theme()
        grid = tk.Frame(panel, bg=BG_PANEL)
        grid.pack(fill="x", pady=(4, 10))
        names = themes_list()
        for i, name in enumerate(names):
            meta = load_theme(name)
            active = name == current
            label = ("✓ " if active else "") + name
            btn = tk.Button(grid, text=label, font=self.f_small,
                            command=lambda n=name: self._switch_theme(n),
                            bg=meta.bg_panel, fg=meta.accent,
                            activebackground=meta.bg_edge,
                            activeforeground=meta.accent,
                            relief="flat", bd=0, padx=8, pady=6, cursor="hand2",
                            highlightbackground=(CYAN if active
                                                 else meta.bg_edge),
                            highlightthickness=2 if active else 1)
            btn.grid(row=i // 3, column=i % 3, sticky="ew", padx=3, pady=3)
        for col in range(3):
            grid.columnconfigure(col, weight=1)
        tk.Label(panel, text="built-in palettes — click one to switch "
                             "(saved for next launch)",
                 font=self.f_small, fg=DIM, bg=BG_PANEL,
                 anchor="w").pack(fill="x", pady=(0, 8))

        # --- options ---------------------------------------------------------
        tk.Label(panel, text="OPTIONS", font=self.f_small, fg=CYAN,
                 bg=BG_PANEL, anchor="w").pack(fill="x")
        self._var_dry = tk.BooleanVar(value=self.settings.dry_run)
        self._var_vision = tk.BooleanVar(value=self.settings.vision)
        self._var_talk = tk.BooleanVar(value=self.talk_active)
        for text, var, cmd in (
            ("dry-run  — print commands, never execute",
             self._var_dry, self._set_dry_run),
            ("vision  — attach screenshots to the model",
             self._var_vision, self._set_vision),
            ("talk  — voice mode (mic in, spoken replies)",
             self._var_talk, self._set_talk),
        ):
            tk.Checkbutton(panel, text=text, variable=var, command=cmd,
                           bg=BG_PANEL, fg=FG, selectcolor=BG_PANEL,
                           activebackground=BG_PANEL, activeforeground=CYAN,
                           highlightthickness=0, font=self.f_small,
                           anchor="w", cursor="hand2").pack(fill="x", pady=1)

        tk.Label(panel, text="current palette: %s" % current,
                 font=self.f_small, fg=DIM, bg=BG_PANEL,
                 anchor="w").pack(fill="x", pady=(10, 6))

        tk.Button(panel, text="CLOSE", command=self._close_settings,
                  bg=BG_PANEL, fg=CYAN, activebackground=BG_EDGE,
                  activeforeground=CYAN, relief="flat", bd=0,
                  font=self.f_small, padx=14, pady=5, cursor="hand2",
                  highlightbackground=BG_EDGE,
                  highlightthickness=1).pack()

    def _current_theme(self) -> str:
        default = read_saved_theme() or "jarvis"
        return load_theme(resolve_theme_name(None, self.settings.theme,
                                             default=default)).name

    def _switch_theme(self, name: str) -> None:
        try:
            apply_theme(name)
        except ThemeError as exc:
            self.log.append("warn", "theme error: %s" % exc)
            return
        save_theme(name)
        self.settings.theme = name
        was_settings_open = (self._settings_win is not None
                             and self._settings_win.winfo_exists())
        self._rebuild_ui()
        self.log.append("ok", "palette switched to %s" % name)
        self.log.append("sep", "─" * 60)
        self._refresh_status()
        if was_settings_open:
            self._open_settings()

    def _rebuild_ui(self) -> None:
        """Re-build the whole deck after a palette swap, keeping content."""
        for child in self.winfo_children():
            child.destroy()
        self._style_fonts()
        self._build()
        self._restore_panels()
        if self.talk_active:
            self.talk_btn.configure(fg=GREEN)
        self._refresh_status()
        self.entry.focus_set()

    def _set_dry_run(self) -> None:
        self.settings.dry_run = self._var_dry.get()
        self.log.append("info", "dry-run set to %s" % self.settings.dry_run)

    def _set_vision(self) -> None:
        self.settings.vision = self._var_vision.get()
        self.log.append("info", "vision set to %s" % self.settings.vision)

    def _set_talk(self) -> None:
        want = self._var_talk.get()
        if want and self.voice is None:
            self._var_talk.set(False)
            self.log.append("warn", "voice engine unavailable")
            return
        if want != self.talk_active:
            self._toggle_talk()

    # ---------------------------------------------------------- voice toggles

    def _toggle_talk(self) -> None:
        if self.voice is None:
            self.log.append("warn", "voice engine unavailable")
            return
        self.talk_active = not self.talk_active
        self.talk_btn.configure(fg=GREEN if self.talk_active else CYAN)
        self._refresh_status()
        if self.talk_active:
            self.log.append("ok", "voice mode ON — press the mic button, speak, release")
            self._start_listen()
        else:
            self.log.append("info", "voice mode OFF")

    def _start_listen(self) -> None:
        if not self.talk_active or self.busy:
            return
        self.log.append("info", "🎤 listening…")
        threading.Thread(target=self._listen_worker, daemon=True).start()

    def _listen_worker(self) -> None:
        try:
            text = self.voice.listen()
        except Exception as exc:  # noqa: BLE001 - mic failures shouldn't kill the UI
            self.post("log", "err", "mic error: %s" % exc)
            return
        if text:
            self.post("log", "info", "🎤 %s" % text)
            self.post("submit", text)
        else:
            self.post("log", "warn", "nothing heard — try /mic to pick a microphone")

    # ------------------------------------------------------ main input path

    def _on_submit(self, _event=None) -> None:
        if self.busy:
            self.log.append("warn", "Orion is busy — wait for the current task")
            return
        line = self.entry.get().strip()
        if not line:
            return
        self.entry.delete(0, "end")
        self._chat("you", line)
        self.entry.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        threading.Thread(target=self._process, args=(line,), daemon=True).start()

    def _process(self, line: str) -> None:
        if line.startswith("/"):
            self._handle_special(line)
            return
        self.post("busy", True)
        outcome = self.agent.chat(line)
        if outcome["kind"] == "error":
            self.post("log", "err", "LLM error: %s" % outcome["message"])
            self.post("busy", False)
            self.post("reenable")
            return
        if outcome["kind"] == "text":
            self.post("chat", "orion", outcome["message"])
            self.post("busy", False)
            self.post("reenable")
            return
        self.post("dispatch", outcome["action"])

    def _handle_special(self, line: str) -> None:
        parts = line.split(maxsplit=1)
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")
        if cmd in ("/exit", "/quit", "/q"):
            self.post("close")
        elif cmd in ("/help", "?"):
            self.post("help")
        elif cmd == "/status":
            self.post("status")
        elif cmd == "/clear":
            self.post("clear")
        elif cmd in ("/settings", "/theme"):
            self.post("settings")
        elif cmd in ("/talk", "/voice"):
            self.post("talk")
        elif cmd == "/mic":
            self.post("mic")
        elif cmd == "/model":
            if arg:
                self.post("model", arg)
            else:
                self.post("log", "warn", "usage: /model <name>")
        elif cmd == "/remember":
            if not arg:
                self.post("log", "warn", "usage: /remember <note to save>")
            else:
                nid = self.agent.remember(arg)
                self.post("log", "ok", "remembered: %s" % arg)
        elif cmd == "/memory":
            items = self.memory.items()
            if not items:
                self.post("log", "dim", "memory is empty")
            else:
                self.post("log", "info", "PERMANENT MEMORY (%d)" % len(items))
                for n in items:
                    self.post("log", "dim", "#%d  %s" % (n["id"], n["text"][:150]))
        elif cmd == "/forget":
            try:
                nid = int((arg.split()[0] if arg else ""))
            except (ValueError, IndexError):
                self.post("log", "warn", "usage: /forget <id>")
            else:
                if self.agent.forget(nid):
                    self.post("log", "ok", "forgotten #%d" % nid)
                else:
                    self.post("log", "warn", "no memory note #%d" % nid)
        elif cmd == "/context":
            self.post("context")
        else:
            self.post("log", "warn", "unknown command: %s (try /help)" % cmd)
        self.post("reenable")

    def _submit_voice(self, text: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self._on_submit()

    def _cmd_mic(self) -> None:
        if self.voice is None:
            self.log.append("warn", "voice engine unavailable")
            return
        self.log.append("info", "probing microphones…")
        threading.Thread(target=self._mic_worker, daemon=True).start()

    def _mic_worker(self) -> None:
        sources = self.voice.list_sources()
        if not sources:
            self.post("log", "warn", "no capture sources found")
            return
        best_name, best_rms = self.voice.auto_select_mic()
        for src in sources:
            self.post("log", "info", "  • %s" % src.get("description", src["name"]))
        if best_name:
            self.voice.set_mic(best_name)
            self.post("log", "ok",
                      "auto-selected mic: %s (signal %.0f)" % (best_name, best_rms))
            self.post("refresh_status")
        else:
            self.post("log", "warn", "no mic with a usable signal found")

    def _cmd_context(self) -> None:
        for message in self.context.messages():
            self.log.append("info", "[%s] %s" % (message["role"],
                                                 message["content"][:300]))

    # ------------------------------------------------------------- dispatch

    def _dispatch(self, action: ActionRequest) -> None:
        self.agent.dispatch(action)

    # --- agent Host callbacks (called on the main thread) -----------------

    def say(self, kind: str, text: str) -> None:
        self._chat("orion", text)

    def speak(self, text: str) -> None:
        self._speak(text)

    def log(self, tag: str, text: str) -> None:
        if tag == "reason":
            self.log.append("info", "◆ %s" % text)
        elif tag == "ok":
            self.log.append("ok", "✓ %s" % text)
        elif tag == "err":
            self.log.append("err", text)
        elif tag == "warn":
            self.log.append("warn", text)
        else:
            self.log.append("info", text)

    def show_command(self, command: str, explanation: str) -> None:
        self._chat("orion", explanation)
        self.log.append("cmd", "$ %s" % command)

    def show_output(self, title: str, body: str) -> None:
        self._log_output(title, body, 0, 0.0)

    def show_screenshot(self, path: str) -> None:
        self.log.append("ok", "saved %s" % path)

    def show_result(self, result, command: str) -> None:
        self._log_output(command, result.stdout, result.returncode,
                         result.elapsed, result.stderr, result.timed_out,
                         result.truncated)

    def confirm(self, command: str, level: str, reason: str) -> bool:
        ok = messagebox.askyesno(
            "Open Orion — confirm",
            "%s risk: %s\n\n$ %s\n\nRun this command?" % (
                level.upper(), reason, command),
            parent=self, icon="warning",
        )
        if ok:
            self.log.append("warn", "⚠ %s-risk command accepted" % level)
        return ok

    def finish(self) -> None:
        self._finish_turn()

    def _log_output(self, command: str, stdout: str, returncode: int,
                    elapsed: float, stderr: str = "", timed_out: bool = False,
                    truncated: bool = False) -> None:
        for line in stdout.rstrip().split("\n"):
            if line:
                self.log.append("out", line)
        for line in stderr.rstrip().split("\n"):
            if line:
                self.log.append("stderr", line)
        extras = " ".join(
            s for s in (
                "timed out" if timed_out else "",
                "output truncated" if truncated else "",
            ) if s
        )
        tail = "exit=%d elapsed=%.2fs%s" % (returncode, elapsed,
                                            "  (%s)" % extras if extras else "")
        self.log.append("exit_ok" if returncode == 0 else "exit_err", tail)
        self.log.append("sep", "─" * 60)

    # ---------------------------------------------------------------- chat

    def _chat(self, who: str, text: str) -> None:
        self._transcript.append((who, text))
        self._render_chat_line(who, text)

    def _render_chat_line(self, who: str, text: str, *, see: bool = True) -> None:
        self.chat.configure(state="normal")
        if who == "you":
            self.chat.insert("end", "YOU", "you")
            self.chat.insert("end", "  %s\n" % text, "body")
        else:
            self.chat.insert("end", "ORION", "orion")
            self.chat.insert("end", "  %s\n" % text, "body")
        self.chat.insert("end", "─" * 40 + "\n", "hr")
        if see:
            self.chat.see("end")
        self.chat.configure(state="disabled")

    def _restore_panels(self) -> None:
        """Re-render the transcript and system log on a freshly built UI.

        Runs before the window is laid out, so ``see()`` is skipped while
        restoring (it can block Tk on un-mapped widgets) and re-enabled
        afterwards with one explicit scroll to the tail.
        """
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.log._see = False
        recorder = self.log.recorder
        self.log.recorder = None  # don't re-record while replaying the buffer
        try:
            for who, text in self._transcript:
                self._render_chat_line(who, text, see=False)
            self.chat.configure(state="disabled")
            for tag, content, prefix in list(self._logbuf):
                self.log.append(tag, content, prefix=prefix)
        finally:
            self.log.recorder = recorder
            self.log._see = True
        self.update_idletasks()
        try:
            self.chat.see("end")
            self.log.text.see("end")
        except tk.TclError:
            pass

    def _speak(self, text: str) -> None:
        if self.talk_active and self.voice and text:
            try:
                self.voice.speak(text)
            except Exception as exc:  # noqa: BLE001 - TTS shouldn't break the UI
                self.post("log", "warn", "speech failed: %s" % exc)

    def _finish_turn(self) -> None:
        self.post("busy", False)
        self.post("reenable")

    # ------------------------------------------------------- thread bridge

    def post(self, kind: str, *args) -> None:
        self._q.put((kind, args))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, args = self._q.get_nowait()
                self._apply(kind, args)
        except queue.Empty:
            pass
        if not self._closing:
            self._after_poll = self.after(60, self._poll_queue)

    def _apply(self, kind: str, args: tuple) -> None:
        handler = {
            "log": lambda: self.log.append(*args),
            "chat": lambda: self._chat(*args),
            "busy": lambda: self._set_busy(bool(args[0])),
            "reenable": self._reenable,
            "dispatch": lambda: self._dispatch(args[0]),
            "submit": lambda: self._submit_voice(args[0]),
            "talk": self._toggle_talk,
            "mic": self._cmd_mic,
            "help": self._cmd_help,
            "status": self._cmd_status,
            "clear": self._cmd_clear,
            "settings": self._toggle_settings,
            "model": lambda: self._cmd_model(args[0]),
            "context": self._cmd_context,
            "refresh_status": self._refresh_status,
            "close": self._on_close,
        }.get(kind)
        if handler:
            handler()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.orb.set_busy(busy)
        if busy:
            self.status_btn.configure(state="disabled")
            self.entry.configure(state="disabled")
        else:
            self.status_btn.configure(state="normal")
            self.entry.configure(state="normal")

    def _reenable(self) -> None:
        self.entry.configure(state="normal")
        self.send_btn.configure(state="normal")
        self.entry.focus_set()

    def _on_close(self) -> None:
        self._closing = True
        if self._after_poll:
            try:
                self.after_cancel(self._after_poll)
            except tk.TclError:
                pass
            self._after_poll = None
        try:
            self.provider.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    _closing = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orion-gui",
        description="Open Orion — JARVIS-style desktop UI (tkinter)",
    )
    parser.add_argument("--provider", choices=["auto", "ollama", "api"], default=None)
    parser.add_argument("--model", default=None, help="override the active model")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--voice", action="store_true",
                        help="start with talk mode enabled")
    parser.add_argument("--cwd", default=None,
                        help="working directory for generated commands")
    parser.add_argument("--theme", default=None,
                        help="HUD palette: %s, or a path to a custom JSON theme file"
                        % ", ".join(themes_list()))
    parser.add_argument("--list-themes", action="store_true",
                        help="list available palettes and exit")
    parser.add_argument("--version", action="version",
                        version=f"open-orion {__version__}")
    args = parser.parse_args(argv)

    if args.list_themes:
        print("available themes: %s" % ", ".join(themes_list()))
        return 0

    settings = get_settings()
    try:
        apply_theme(args.theme, settings.theme,
                    default=read_saved_theme() or "jarvis")
    except ThemeError as exc:
        print("open-orion: %s" % exc, file=sys.stderr)
        return 2
    if args.provider:
        settings.provider = args.provider
    if args.model:
        settings.ollama_model = args.model
        settings.api_model = args.model
    if args.dry_run:
        settings.dry_run = True
    if args.cwd:
        settings.working_dir = args.cwd

    if settings.working_dir:
        try:
            os.chdir(settings.working_dir)
        except OSError as exc:
            import tkinter.messagebox as messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Open Orion", "cannot chdir to %s:\n%s" % (
                settings.working_dir, exc))
            root.destroy()
            return 2

    try:
        provider = get_provider(settings)
    except LLMError as exc:
        print("open-orion: %s" % exc)
        return 1

    executor = Executor(settings)

    voice = None
    if args.voice or settings.voice_enabled:
        try:
            voice = Voice(settings)
        except VoiceError as exc:
            print("open-orion: voice unavailable: %s" % exc)
            voice = None

    app = OrionGUI(settings, provider, executor, voice)
    if args.voice or (voice is not None and settings.voice_enabled):
        app.after(600, app._toggle_talk)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
