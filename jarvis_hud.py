#!/usr/bin/env python3
"""ORION — Autonomous Assistant HUD.

Futuristic sci-fi command deck for the Open Orion agent. Pure tkinter (stdlib)
for the interface; real telemetry from /proc and the real agent loop
(provider -> JSON action -> executor, plus voice) from the orion package.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import platform as platform_mod
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

from orion import __version__
from orion.agent import Agent
from orion.config import get_settings
from orion.executor import Executor
from orion.llm import LLMError, get_provider
from orion.memory import Memory
from orion.system import (
    sys_battery,
    sys_cpu_load,
    sys_cpu_temp,
    sys_disk,
    sys_hostname,
    sys_memory,
    sys_net_rx_tx,
    sys_uptime,
)
from orion.themes import (
    ThemeError,
    load_theme,
    read_saved_theme,
    resolve_theme_name,
    themes_list,
)
from orion.voice import Voice, VoiceError

# ---------------------------------------------------------------------------
# Theme — palette resolved at startup from --theme / ORION_THEME / .env
# ---------------------------------------------------------------------------

def apply_theme(cli: str | None = None, configured: str | None = None,
                default: str = "orion") -> None:
    """Load a palette and re-bind the module color constants.

    Called at import time (environment/defaults) and again from ``main()``
    once the CLI args and settings are known, so ``--theme``/``ORION_THEME``
    always win over an earlier import-time binding.
    """
    global BG, BG_PANEL, BG_DEEP, GRID, PURPLE, PURPLE_DIM, PURPLE_FAINT
    global TEXT_DIM, TEXT_FAINT, OK, WARN, ERR
    theme = load_theme(resolve_theme_name(cli, configured, default=default))
    BG           = theme.bg
    BG_PANEL     = theme.bg_panel
    BG_DEEP      = theme.bg_deep
    GRID         = theme.grid
    PURPLE       = theme.accent
    PURPLE_DIM   = theme.accent_dim
    PURPLE_FAINT = theme.accent_faint
    TEXT_DIM     = theme.text_dim
    TEXT_FAINT   = theme.text_faint
    OK           = theme.ok
    WARN         = theme.warn
    ERR          = theme.error


apply_theme()

FPS_MS      = 16
MONO_FONTS  = ("Consolas", "Courier New", "DejaVu Sans Mono", "Liberation Mono",
               "Courier", "monospace")


def pick_font() -> str:
    available = set(tkfont.families())
    for name in MONO_FONTS:
        if name in available:
            return name
    return "TkFixedFont"


def fade(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ia = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    ib = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(ia[i] + (ib[i] - ia[i]) * t) for i in range(3))


# ---------------------------------------------------------------------------
# Orion agent wiring (real conversation loop — see orion/agent.py)
# ---------------------------------------------------------------------------


class HUD(tk.Tk):
    def __init__(self, settings=None, provider=None, executor=None,
                 voice=None) -> None:
        super().__init__(className="orionhud")
        self.title("Open Orion")
        self.configure(bg=BG)
        self.settings = settings or get_settings()
        self.provider = provider
        self.executor = executor
        self.voice = voice
        self.memory = Memory()
        self.agent = Agent(self.settings, self.provider, self.executor,
                           memory=self.memory, host=self)

        self._detect_screen()
        self.f_mono = pick_font()
        self._make_fonts()

        self._t0 = time.monotonic()
        self._frame = 0
        self._busy = False
        self._listen = False
        self._hud_pulse = 0.0
        self._arc_a = 0.0
        self._arc_b = 180.0
        self._dot_phase = 0.0
        self._q: queue.Queue = queue.Queue()
        self._shot_imgs: list = []
        self._last_battery = sys_battery()
        self._rx0, self._tx0 = sys_net_rx_tx()
        self._net_t0 = time.monotonic()

        self._build()
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", lambda e: self._set_fullscreen(False))
        self.bind("q", lambda e: self._on_close())
        self.bind("<Configure>", self._on_resize)

        self._fullscreen = False
        self.geometry("%dx%d+%d+%d" % (self.win_w, self.win_h,
                                       self.win_x, self.win_y))
        self.minsize(self.min_w, self.min_h)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._after_animate = self.after(FPS_MS, self._animate)
        self._after_poll = self.after(60, self._poll_queue)
        self.after(700, self._run_boot)
        self._after_telemetry = self.after(1600, self._tick_telemetry)
        self._after_feed = self.after(2000, self._tick_feed)

        if self.voice:
            self._refresh_mic_state()

    # ----------------------------------------------------- responsive sizing

    def _detect_screen(self) -> None:
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        if sw <= 0 or sh <= 0:
            sw, sh = 1280, 800
        base_w, base_h = 1280, 800
        self.scale = max(0.6, min(2.0, min(sw / base_w, sh / base_h)))
        self.win_w = min(sw, max(base_w, int(sw * 0.94)))
        self.win_h = min(sh, max(base_h, int(sh * 0.92)))
        self.win_x = max(0, (sw - self.win_w) // 2)
        self.win_y = max(0, (sh - self.win_h) // 2)
        self.min_w = 720
        self.min_h = 540

    def _make_fonts(self) -> None:
        self.f_mono_b = tkfont.Font(family=self.f_mono, weight="bold")
        self.f_title = tkfont.Font(family=self.f_mono, weight="bold")
        self.f_sub = tkfont.Font(family=self.f_mono, weight="bold")
        self.f_label = tkfont.Font(family=self.f_mono, weight="bold")
        self.f_small = tkfont.Font(family=self.f_mono)
        self.f_read = tkfont.Font(family=self.f_mono)
        self._rescale_fonts()

    def _rescale_fonts(self) -> None:
        s = self.scale
        self.f_mono_b.configure(size=max(8, int(10 * s)))
        self.f_title.configure(size=max(16, int(30 * s)))
        self.f_sub.configure(size=max(8, int(10 * s)))
        self.f_label.configure(size=max(7, int(9 * s)))
        self.f_small.configure(size=max(7, int(9 * s)))
        self.f_read.configure(size=max(6, int(8 * s)))

    def _on_resize(self, _event=None) -> None:
        try:
            w, h = self.winfo_width(), self.winfo_height()
            self._power_w = max(60, min(320, int(w * 0.13)))
            if w > 0 and h > 0:
                new_scale = max(0.6, min(2.0, min(w / 1280.0, h / 800.0)))
                if abs(new_scale - self.scale) > 0.02:
                    self.scale = new_scale
                    self._rescale_fonts()
                    if hasattr(self, "mic_canvas"):
                        m = int(110 * self.scale)
                        self.mic_canvas.configure(width=m, height=m)
                        self.input.configure(
                            width=max(24, int(34 * self.scale)))
        except tk.TclError:
            pass

    # ------------------------------------------------------------ layout

    def _build(self):
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._bg = tk.Frame(self, bg=BG)
        self._bg.grid(row=0, column=0, sticky="nsew")
        self._bg.grid_columnconfigure(0, weight=1)
        self._bg.grid_rowconfigure(2, weight=1)
        self._bg.grid_rowconfigure(3, weight=0)

        self._grid_canvas = tk.Canvas(self._bg, bg=BG, highlightthickness=0)
        self._grid_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._grid_canvas.tk.call("lower", self._grid_canvas._w)

        header = tk.Frame(self._bg, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(14, 4))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=1)
        self._build_header_left(header)
        self._build_header_right(header)

        mid = tk.Frame(self._bg, bg=BG)
        mid.grid(row=2, column=0, sticky="nsew", padx=24, pady=6)
        mid.grid_columnconfigure(0, weight=4, minsize=360)
        mid.grid_columnconfigure(1, weight=2, minsize=260)
        mid.grid_rowconfigure(0, weight=1)
        self.center = tk.Canvas(mid, bg=BG, highlightthickness=0, bd=0)
        self.center.grid(row=0, column=0, sticky="nsew")
        self.chat_panel = tk.Frame(mid, bg=BG_PANEL,
                                   highlightbackground=GRID,
                                   highlightthickness=1)
        self.chat_panel.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self._build_conversation()

        bottom = tk.Frame(self._bg, bg=BG)
        bottom.grid(row=3, column=0, sticky="ew", padx=24, pady=(4, 10))
        bottom.grid_columnconfigure(0, weight=3)
        bottom.grid_columnconfigure(1, weight=1)
        bottom.grid_columnconfigure(2, weight=3)
        self._build_network(bottom)
        self._build_mic(bottom)
        self._build_core(bottom)

        footer = tk.Frame(self._bg, bg=BG_DEEP)
        footer.grid(row=4, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="INITIALIZING…")
        tk.Label(footer, textvariable=self.status_var, font=self.f_small,
                 fg=TEXT_FAINT, bg=BG_DEEP, anchor="w", padx=16,
                 pady=4).pack(side="left", fill="x", expand=True)
        tk.Label(footer, text=f"ORION v{__version__}", font=self.f_small,
                 fg=GRID, bg=BG_DEEP, padx=16, pady=4).pack(side="right")

    def _panel(self, parent, title, **grid):
        frame = tk.Frame(parent, bg=BG_PANEL, highlightbackground=GRID,
                         highlightthickness=1, padx=14, pady=10)
        frame.grid(**grid)
        frame.grid_columnconfigure(0, weight=1)
        tk.Label(frame, text=title, font=self.f_label, fg=PURPLE_DIM,
                 bg=BG_PANEL, anchor="w").grid(row=0, column=0, sticky="ew")
        tk.Frame(frame, bg=PURPLE_FAINT, height=1).grid(row=1, column=0,
                                                        sticky="ew", pady=6)
        return frame

    def _build_header_left(self, parent):
        left = tk.Frame(parent, bg=BG)
        left.grid(row=0, column=0, sticky="nw")
        tk.Label(left, text="ORION", font=self.f_title, fg=PURPLE,
                 bg=BG).grid(row=0, column=0, sticky="w")
        tk.Label(left, text=f"AUTONOMOUS ASSISTANT V{__version__}", font=self.f_sub,
                 fg=PURPLE_DIM, bg=BG).grid(row=1, column=0, sticky="w",
                                            pady=(0, 6))

        self.readouts_left = {}
        for i, key in enumerate(("SYSTEM STATUS", "CPU LOAD", "MEMORY")):
            tk.Label(left, text=key, font=self.f_read, fg=TEXT_DIM,
                     bg=BG).grid(row=2 + i, column=0, sticky="w")
            lab = tk.Label(left, text="—", font=self.f_read, fg=PURPLE, bg=BG)
            lab.grid(row=2 + i, column=1, sticky="e", padx=(10, 0))
            self.readouts_left[key] = lab

    def _build_header_right(self, parent):
        right = tk.Frame(parent, bg=BG)
        right.grid(row=0, column=1, sticky="ne")

        power_row = tk.Frame(right, bg=BG)
        power_row.pack(anchor="e")
        self.power_label_name = tk.Label(power_row, text="POWER", font=self.f_read,
                                         fg=TEXT_DIM, bg=BG)
        self.power_label_name.pack(side="left")
        self.power_canvas = tk.Canvas(power_row, width=170, height=10,
                                      bg=BG, highlightthickness=0)
        self._power_w = 170
        self.power_canvas.pack(side="left", padx=8)
        self.power_label = tk.Label(power_row, text="—", font=self.f_read,
                                    fg=PURPLE, bg=BG)
        self.power_label.pack(side="left")

        self.uptime_label = tk.Label(right, text="UPTIME —", font=self.f_sub,
                                     fg=TEXT_DIM, bg=BG)
        self.uptime_label.pack(anchor="e", pady=(4, 6))

        self.readouts_right = {}
        for key in ("HOSTNAME", "CONNECTION", "ENCRYPTION"):
            row = tk.Frame(right, bg=BG)
            row.pack(fill="x", anchor="e")
            tk.Label(row, text=key, font=self.f_read, fg=TEXT_DIM,
                     bg=BG).pack(side="left")
            lab = tk.Label(row, text="—", font=self.f_read, fg=PURPLE, bg=BG)
            lab.pack(side="left", padx=(10, 0))
            self.readouts_right[key] = lab

    def _build_conversation(self):
        tk.Label(self.chat_panel, text="◆ TRANSMISSION", font=self.f_label,
                 fg=PURPLE, bg=BG_PANEL, anchor="w", padx=8,
                 pady=6).pack(fill="x")
        self.chat = tk.Text(self.chat_panel, bg=BG_PANEL, fg=TEXT_DIM,
                            font=self.f_mono, wrap="word", bd=0, padx=10,
                            pady=8, highlightthickness=0, insertbackground=PURPLE,
                            width=40, height=24, state="disabled", cursor="arrow")
        self.chat.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(self.chat_panel, command=self.chat.yview,
                          troughcolor=BG, bg=GRID, activebackground=PURPLE,
                          width=10)
        sb.pack(side="right", fill="y")
        self.chat.configure(yscrollcommand=sb.set)
        self.chat.tag_configure("you", foreground=TEXT_DIM,
                                font=self.f_mono_b, spacing1=6, spacing3=6)
        self.chat.tag_configure("orion", foreground=PURPLE,
                                font=self.f_mono_b, spacing1=6, spacing3=6)
        self.chat.tag_configure("body", foreground=TEXT_DIM, font=self.f_mono)
        self.chat.tag_configure("sys", foreground=TEXT_FAINT, font=self.f_mono)
        self.chat.tag_configure("ok", foreground=OK, font=self.f_mono)
        self.chat.tag_configure("err", foreground=ERR, font=self.f_mono)
        self.chat.tag_configure("hr", foreground=GRID, font=(self.f_mono, 8))

    def _build_network(self, parent):
        panel = self._panel(parent, "SYSTEM FEED", row=0, column=0,
                            sticky="nsew", padx=(0, 8))
        self.feed = tk.Text(panel, bg=BG_PANEL, fg=TEXT_DIM, font=self.f_small,
                            height=9, wrap="word", bd=0, highlightthickness=0,
                            state="disabled", cursor="arrow", padx=4)
        self.feed.grid(row=2, column=0, sticky="nsew")
        panel.grid_rowconfigure(2, weight=1)
        self.feed.tag_configure("ok", foreground=OK)
        self.feed.tag_configure("warn", foreground=WARN)
        self.feed.tag_configure("err", foreground=ERR)
        self.feed.tag_configure("dim", foreground=TEXT_FAINT)
        self.feed.tag_configure("head", foreground=PURPLE_DIM)
        self.feed.tag_configure("cmd", foreground=PURPLE)

    def _build_mic(self, parent):
        cell = tk.Frame(parent, bg=BG)
        cell.grid(row=0, column=1, sticky="n")
        tk.Label(cell, text="VOICE INTERFACE", font=self.f_label,
                 fg=PURPLE_DIM, bg=BG).pack(pady=(2, 6))
        msize = int(110 * self.scale)
        self.mic_canvas = tk.Canvas(cell, width=msize, height=msize, bg=BG,
                                    highlightthickness=0)
        self.mic_canvas.pack()
        self.mic_canvas.bind("<Button-1>", self._toggle_listen)
        self.mic_canvas.bind("<Enter>",
                             lambda e: self.mic_canvas.configure(cursor="hand2"))
        self.mic_state = tk.Label(cell, text="STANDBY", font=self.f_read,
                                  fg=TEXT_FAINT, bg=BG)
        self.mic_state.pack(pady=(2, 0))

        self.input = tk.Entry(cell, width=max(24, int(34 * self.scale)),
                              font=self.f_small, bg=BG_PANEL,
                              fg=TEXT_DIM, insertbackground=PURPLE, relief="flat",
                              bd=0, highlightbackground=GRID,
                              highlightcolor=PURPLE, highlightthickness=1)
        self.input.pack(pady=(8, 4), fill="x")
        self.input.insert(0, "speak or type a command…")
        self.input.bind("<FocusIn>",
                        lambda e: self._if_placeholder("clear"))
        self.input.bind("<FocusOut>",
                        lambda e: self._if_placeholder("restore"))
        self.input.bind("<Return>", self._on_send)

        self.send_btn = tk.Button(cell, text="TRANSMIT", command=self._on_send,
                                  bg=BG_PANEL, fg=PURPLE,
                                  activebackground=GRID, activeforeground=PURPLE,
                                  relief="flat", bd=0, font=self.f_label,
                                  padx=12, pady=4, cursor="hand2",
                                  highlightbackground=GRID, highlightthickness=1)
        self.send_btn.pack()

    def _build_core(self, parent):
        panel = self._panel(parent, "AI CORE DIAGNOSTICS", row=0, column=2,
                            sticky="nsew", padx=(8, 0))
        rows = [("PROVIDER", "—"), ("MODEL", "—"), ("KERNEL", "—"),
            ("SAFETY", "—"), ("DISK /", "—"), ("TURNS", "0")]
        self.diag = {}
        for i, (k, v) in enumerate(rows):
            tk.Label(panel, text=k, font=self.f_read, fg=TEXT_DIM,
                     bg=BG_PANEL).grid(row=2 + i, column=0, sticky="w")
            lab = tk.Label(panel, text=v, font=self.f_read, fg=PURPLE,
                           bg=BG_PANEL)
            lab.grid(row=2 + i, column=1, sticky="e", padx=(10, 0))
            self.diag[k] = lab
        panel.grid_rowconfigure(2 + len(rows), weight=1)

    def _if_placeholder(self, mode: str) -> None:
        cur = self.input.get()
        if mode == "clear" and cur == "speak or type a command…":
            self.input.delete(0, "end")
            self.input.configure(fg=TEXT_DIM)
        elif mode == "restore" and not cur.strip():
            self.input.insert(0, "speak or type a command…")
            self.input.configure(fg=TEXT_FAINT)

    # --------------------------------------------------------- hud canvas

    def _draw_grid(self):
        w = self._grid_canvas.winfo_width()
        h = self._grid_canvas.winfo_height()
        self._grid_canvas.delete("grid")
        step = 40
        off = self._frame % step
        for x in range(-step, w + step, step):
            self._grid_canvas.create_line(x + off, 0, x + off, h, fill=GRID,
                                          tags="grid")
        for y in range(-step, h + step, step):
            self._grid_canvas.create_line(0, y + off, w, y + off, fill=GRID,
                                          tags="grid")
        self._grid_canvas.tag_lower("grid")

    def _draw_hud(self):
        c = self.center
        c.delete("hud")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return
        # Centre the arc reactor on its own region with even breathing room:
        # the outermost tick ring (R*1.06) must never run into the canvas
        # edges, or the emblem looks cramped / cut off.
        cx, cy = w / 2, h / 2
        R = min(w, h) * 0.34
        pulse = 0.5 + 0.5 * math.sin(self._hud_pulse * (2.0 if self._busy else 1.0))
        rate = 2.6 if self._busy else 0.9
        self._arc_a = (self._arc_a + rate) % 360
        self._arc_b = (self._arc_b - rate * 0.7) % 360
        self._dot_phase = (self._dot_phase + rate) % 360

        for i, frac in enumerate((1.0, 0.78, 0.52)):
            r = R * frac
            if i == 0:
                c.create_oval(cx - r, cy - r, cx + r, cy + r,
                              outline=fade(PURPLE, BG, 0.45), width=1, tags="hud")
            else:
                c.create_oval(cx - r, cy - r, cx + r, cy + r,
                              outline=fade(PURPLE_FAINT, BG, 0.25), width=1,
                              dash=(2, 6), tags="hud")
        for i in range(60):
            a = i * 6.0
            r1, r2 = R * 1.02, R * 1.06
            x1 = cx + r1 * math.cos(math.radians(a))
            y1 = cy + r1 * math.sin(math.radians(a))
            x2 = cx + r2 * math.cos(math.radians(a))
            y2 = cy + r2 * math.sin(math.radians(a))
            on = i % 5 == 0
            col = fade(PURPLE, BG, 0.5) if on else fade(PURPLE_FAINT, BG, 0.3)
            c.create_line(x1, y1, x2, y2, fill=col, width=1, tags="hud")

        r = R * 0.89
        n = 40
        for i in range(n):
            a = (i * 360.0 / n) + self._dot_phase
            x = cx + r * math.cos(math.radians(a))
            y = cy + r * math.sin(math.radians(a))
            c.create_oval(x - 1.6, y - 1.6, x + 1.6, y + 1.6,
                          fill=PURPLE_DIM, outline="", tags="hud")
        for i in range(n):
            a = -(i * 360.0 / n) - self._dot_phase * 1.4
            x = cx + r * 0.92 * math.cos(math.radians(a))
            y = cy + r * 0.92 * math.sin(math.radians(a))
            c.create_oval(x - 1.1, y - 1.1, x + 1.1, y + 1.1,
                          fill=fade(PURPLE, BG, 0.35), outline="", tags="hud")

        col = fade(PURPLE, BG, 0.25 + 0.3 * pulse)
        c.create_arc(cx - R, cy - R, cx + R, cy + R, start=self._arc_a,
                     extent=58, style="arc", outline=col, width=3, tags="hud")
        c.create_arc(cx - R, cy - R, cx + R, cy + R, start=self._arc_a + 180,
                     extent=58, style="arc", outline=col, width=3, tags="hud")
        col2 = fade(PURPLE_FAINT, BG, 0.4)
        c.create_arc(cx - R * 0.78, cy - R * 0.78, cx + R * 0.78,
                     cy + R * 0.78, start=self._arc_b, extent=92,
                     style="arc", outline=col2, width=2, tags="hud")
        c.create_arc(cx - R * 0.78, cy - R * 0.78, cx + R * 0.78,
                     cy + R * 0.78, start=self._arc_b + 180, extent=92,
                     style="arc", outline=col2, width=2, tags="hud")

        er = R * 0.30
        glow = 10 if self._busy else 5
        base = fade(PURPLE, BG, 0.35 + 0.35 * pulse)
        c.create_oval(cx - er - glow, cy - er - glow, cx + er + glow,
                      cy + er + glow, fill=base, outline=PURPLE_DIM, width=2,
                      tags="hud")
        c.create_oval(cx - er * 0.92, cy - er * 0.92, cx + er * 0.92,
                      cy + er * 0.92, outline=PURPLE, width=2, tags="hud")
        fsize = max(12, int(R * 0.16))
        if getattr(self, "_emblem_font", None) is None:
            self._emblem_font = tkfont.Font(family=self.f_mono, size=fsize,
                                            weight="bold")
        elif self._emblem_font.cget("size") != fsize:
            self._emblem_font.configure(size=fsize)
        f = self._emblem_font
        state = "THINKING" if self._busy else "ONLINE"
        c.create_text(cx, cy - R * 0.02, text="ORION", fill=PURPLE, font=f,
                      tags="hud")
        c.create_text(cx, cy + R * 0.14, text=state, fill=PURPLE_DIM,
                      font=self.f_label, tags="hud")

    def _draw_power(self):
        self.power_canvas.delete("all")
        w = self._power_w
        self.power_canvas.configure(width=w)
        filled = w * self._power
        pulse = 0.5 + 0.5 * math.sin(self._hud_pulse * 1.5)
        self.power_canvas.create_rectangle(0, 3, w, 7, fill="#160a2e",
                                           outline="")
        col = fade(PURPLE, BG, 0.25 + 0.55 * pulse)
        for i in range(4):
            j = i + 1
            self.power_canvas.create_line(0, 5 - j, filled - j, 5 - j,
                                          fill=fade(col, BG, i * 0.28),
                                          width=1)
        self.power_canvas.create_rectangle(filled, 0, filled + 2, 10,
                                           fill=PURPLE, outline="")

    def _draw_mic(self):
        c = self.mic_canvas
        c.delete("all")
        s = self.scale
        cx = cy = int(55 * s)
        R = int(34 * s)
        pulse = 0.5 + 0.5 * math.sin(self._hud_pulse * (2.2 if self._listen else 1.0))
        if self._listen:
            ring = fade(PURPLE, BG, 0.5 + 0.5 * pulse)
            o = int((8 + 4 * pulse) * s)
            c.create_oval(cx - R - o, cy - R - o, cx + R + o, cy + R + o,
                          outline=ring, width=2, dash=(3, 4))
        c.create_oval(cx - R, cy - R, cx + R, cy + R, outline=PURPLE, width=2)
        glow = fade(PURPLE, BG, 0.30 + 0.45 * pulse)
        c.create_oval(cx - R + 3, cy - R + 3, cx + R - 3, cy + R - 3,
                      fill=glow, outline="")
        w2 = int(2 * s)
        c.create_rectangle(cx - 6 * s, cy - 22 * s, cx + 6 * s, cy - 4 * s,
                           fill=PURPLE, outline="")
        c.create_arc(cx - 6 * s, cy - 14 * s, cx + 6 * s, cy + 4 * s,
                     start=180, extent=180, style="arc", outline=PURPLE,
                     width=w2)
        c.create_line(cx, cy + 4 * s, cx, cy + 12 * s, fill=PURPLE, width=w2)
        c.create_arc(cx - 8 * s, cy + 6 * s, cx + 8 * s, cy + 20 * s,
                     start=0, extent=180, style="arc", outline=PURPLE,
                     width=w2)

    # ------------------------------------------------------------- anim

    def _animate(self):
        self._frame += 1
        self._hud_pulse += 0.05
        self._draw_grid()
        self._draw_power()
        self._draw_hud()
        self._draw_mic()
        self._after_animate = self.after(FPS_MS, self._animate)

    # ---------------------------------------------------------- telemetry

    def _tick_telemetry(self):
        if self._closing:
            return

        def worker():
            cpu = sys_cpu_load()
            mem_avail, mem_total = sys_memory()
            bat = sys_battery()
            temp = sys_cpu_temp()
            disk_pct, disk_free = sys_disk()
            rx, tx = sys_net_rx_tx()
            dt = time.monotonic() - self._net_t0
            rate_rx = (rx - self._rx0) / dt / 1024
            rate_tx = (tx - self._tx0) / dt / 1024
            self._rx0, self._tx0, self._net_t0 = rx, tx, time.monotonic()
            self._q.put(("telemetry", (cpu, mem_avail, mem_total, bat, temp,
                                       disk_pct, disk_free, rate_rx, rate_tx)))
            self._q.put(("feed", ("dim",
                         "  [sys] cpu %.1f%% · mem %.1f/%.1fGB%s%s" % (
                             cpu, mem_total - mem_avail, mem_total,
                             " · temp %.0f°C" % temp if temp else "",
                             " · bat %d%%" % bat if bat else ""))))
            self._q.put(("feed", ("dim",
                         "  [net] rx %4.0f KB/s · tx %4.0f KB/s" % (rate_rx,
                                                                    rate_tx))))
        threading.Thread(target=worker, daemon=True).start()
        self._after_telemetry = self.after(1800, self._tick_telemetry)

    def _tick_feed(self):
        if self._closing:
            return
        uptime = sys_uptime()
        h, rem = divmod(int(uptime), 3600)
        m, s = divmod(rem, 60)
        self._q.put(("feed", ("dim",
                     "  [up]  %02d:%02d:%02d" % (h, m, s))))
        self._after_feed = self.after(7000, self._tick_feed)

    # ------------------------------------------------------------ boot

    def _run_boot(self):
        self._log_feed("head", f"OPEN ORION v{__version__} — boot sequence")
        self._log_feed("ok", "  [ OK ] neural matrix linked")
        self._log_feed("ok", "  [ OK ] shell interface armed")
        self._log_feed("ok", "  [ OK ] provider: %s · model: %s" % (
            self.provider.name, getattr(self.provider, "model", "-")))
        self._log_feed("ok" if self.voice else "dim",
                       "  [%s] voice %s" % (
                           "OK" if self.voice else "…",
                           "engine online (%s)" % self.settings.tts_engine
                           if self.voice else "engine offline"))
        self._log_feed("dim", "  [sys] cwd %s" % os.getcwd())
        if len(self.memory):
            self._log_feed("dim", "  [mem] %d permanent note(s) loaded" % len(self.memory))
        self._chat("orion", "All systems nominal. How may I assist you?")
        self.status_var.set("STANDBY")

    # ------------------------------------------------------ interaction

    def _on_send(self, _event=None) -> None:
        if self._busy:
            self._log_feed("warn", "  [agent] busy — wait for current task")
            return
        line = self.input.get().strip()
        if not line or line == "speak or type a command…":
            return
        self._if_placeholder("restore")
        self.input.delete(0, "end")
        if line.startswith("/"):
            self._handle_special(line)
            return
        self._chat("you", line)
        self.status_var.set("PROCESSING…")
        threading.Thread(target=self._process, args=(line,), daemon=True).start()

    def _process(self, line: str) -> None:
        self._set_busy(True)
        result = self.agent.chat(line)
        if result["kind"] == "error":
            self._q.put(("chat", ("orion", "⚠ system fault: %s" % result["message"])))
            self._q.put(("feed", ("err", "  [agent] %s" % result["message"])))
        elif result["kind"] == "text":
            self._q.put(("chat", ("orion", result["message"])))
            self._q.put(("feed", ("dim", "  [agent] raw (non-JSON) reply shown")))
        else:
            self._q.put(("dispatch", result["action"]))
            return
        self._set_busy(False)
        self._q.put(("status", "STANDBY"))

    # --- agent Host callbacks (called on the main thread) -----------------

    def say(self, kind: str, text: str) -> None:
        self._chat("orion", text)
        if kind == "done":
            self._log_feed("ok", "  [done] %s" % text)

    def speak(self, text: str) -> None:
        self._speak(text)

    def log(self, tag: str, text: str) -> None:
        if tag == "reason":
            self._log_feed("dim", "  [agent] %s" % text)
        elif tag == "ok":
            self._log_feed("ok", "  %s" % text)
        elif tag == "err":
            self._log_feed("err", "  %s" % text)
        elif tag == "warn":
            self._log_feed("warn", "  %s" % text)
        else:
            self._log_feed("dim", "  %s" % text)

    def show_command(self, command: str, explanation: str) -> None:
        self._log_feed("cmd", "  $ %s" % command)
        self._chat("orion", explanation or "Running command")

    def show_output(self, title: str, body: str) -> None:
        self._log_output(title, body)

    def show_screenshot(self, path: str) -> None:
        self._show_screenshot(path)
        self._log_feed("ok", "  [vision] saved %s" % path)

    def show_result(self, result, command: str) -> None:
        self._log_output(command, result.stdout, result.stderr,
                         result.returncode, result.elapsed, result.timed_out,
                         result.truncated)

    def confirm(self, command: str, level: str, reason: str) -> bool:
        ok = messagebox.askyesno(
            "Orion — confirm",
            "%s risk: %s\n\n$ %s\n\nRun this command?" % (
                level.upper(), reason, command),
            parent=self, icon="warning",
        )
        if ok:
            self._log_feed("warn", "  [safety] %s-risk accepted" % level)
        return ok

    def finish(self) -> None:
        self._set_busy(False)
        self.status_var.set("STANDBY")

    def _dispatch(self, action) -> None:
        self.agent.dispatch(action)

    def _show_screenshot(self, path: str) -> None:
        try:
            img = tk.PhotoImage(file=path)
        except tk.TclError:
            return
        w = img.width()
        if w > 280 and w > 0:
            factor = max(1, int((w + 279) // 280))
            img = img.subsample(factor, factor)
        self._shot_imgs.append(img)
        self.chat.configure(state="normal")
        self.chat.image_create("end", image=img, padx=6, pady=4)
        self.chat.insert("end", "\n", "hr")
        self.chat.insert("end", "─" * 40 + "\n", "hr")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _log_output(self, label, stdout, stderr="", returncode=0, elapsed=0.0,
                    timed_out=False, truncated=False) -> None:
        for line in str(stdout).rstrip().split("\n"):
            if line:
                self._log_feed("ok", "  " + line)
        for line in str(stderr).rstrip().split("\n"):
            if line:
                self._log_feed("warn", "  " + line)
        extras = " ".join(s for s in (
            "timed out" if timed_out else "",
            "output truncated" if truncated else "",
        ) if s)
        self._log_feed("dim", "  [exit %d] %.2fs%s" % (
            returncode, elapsed, "  (%s)" % extras if extras else ""))

    def _handle_special(self, line: str) -> None:
        parts = line.split(maxsplit=1)
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")
        if cmd in ("/exit", "/quit", "/q"):
            self._on_close()
        elif cmd in ("/help", "?"):
            for line_ in ("COMMANDS", "  <text>        talk to Orion",
                      "  /talk          toggle voice",
                      "  /look          capture + describe my screen",
                      "  /mic           pick microphone",
                      "  /status        provider · model", "  /model <name>  hot-swap model",
                      "  /context       print history", "  /clear         reset history",
                      "  /remember <n>  save permanent memory",
                      "  /memory        list permanent memory",
                      "  /forget <id>   delete a memory note",
                      "  /exit          quit"):
                self._log_feed("head", line_)
        elif cmd == "/status":
            self._log_feed("head", "STATUS")
            self._log_feed("dim", "  provider %s · model %s" % (
                self.provider.name, getattr(self.provider, "model", "-")))
            self._log_feed("dim", "  cwd %s · dry-run %s" % (
                os.getcwd(), self.settings.dry_run))
            self._log_feed("dim", "  safety %s · vision %s · memory %d" % (
                self.settings.safety_level,
                "on" if self.settings.vision else "off", len(self.memory)))
            self._log_feed("dim", "  talk %s · turns %d" % (
                "on" if self._listen else "off", len(self.agent.context)))
        elif cmd == "/model":
            if arg:
                self.provider.set_model(arg)
                self._log_feed("ok", "  model set to %s" % arg)
            else:
                self._log_feed("warn", "  usage: /model <name>")
        elif cmd == "/clear":
            self.agent.context.clear()
            self.chat.configure(state="normal")
            self.chat.delete("1.0", "end")
            self.chat.configure(state="disabled")
            self._log_feed("ok", "  context cleared")
        elif cmd == "/context":
            for m in self.agent.context.messages():
                self._log_feed("dim", "  [%s] %s" % (m["role"], m["content"][:120]))
        elif cmd == "/remember":
            if not arg:
                self._log_feed("warn", "  usage: /remember <note to save>")
            else:
                nid = self.agent.remember(arg)
                if nid < 0:
                    self._log_feed("warn", "  memory unavailable")
                else:
                    self._log_feed("ok", "  remembered #%d" % nid)
        elif cmd == "/memory":
            items = self.memory.items()
            if not items:
                self._log_feed("dim", "  memory is empty")
            else:
                self._log_feed("head", "PERMANENT MEMORY (%d)" % len(items))
                for n in items:
                    self._log_feed("dim",
                                   "  #%d  %s" % (n["id"], n["text"][:150]))
        elif cmd == "/forget":
            try:
                nid = int(arg.split()[0] if arg else "")
            except (ValueError, IndexError):
                self._log_feed("warn", "  usage: /forget <id>")
            else:
                if self.agent.forget(nid):
                    self._log_feed("ok", "  forgotten #%d" % nid)
                else:
                    self._log_feed("warn", "  no memory note #%d" % nid)
        elif cmd in ("/talk", "/voice"):
            self._toggle_listen()
        elif cmd in ("/look", "/see"):
            self._cmd_look()
        elif cmd == "/mic":
            self._cmd_mic()
        else:
            self._log_feed("warn", "  unknown command: %s" % cmd)

    def _cmd_look(self) -> None:
        self.status_var.set("LOOKING…")
        self._log_feed("dim", "  [look] capturing screen")

        def worker():
            try:
                path = self.executor.screenshot(None)
            except Exception as exc:  # noqa: BLE001
                self._q.put(("feed", ("err", "  [look] failed: %s" % exc)))
                self._q.put(("status", "STANDBY"))
                return
            if path.startswith("error"):
                self._q.put(("feed", ("err", "  [look] %s" % path)))
                self._q.put(("status", "STANDBY"))
                return
            self._q.put(("look", path))
        threading.Thread(target=worker, daemon=True).start()

    def _cmd_mic(self) -> None:
        if self.voice is None:
            self._log_feed("warn", "  voice engine unavailable")
            return
        def worker():
            best, rms = self.voice.auto_select_mic()
            if best:
                self.voice.set_mic(best)
                self._q.put(("feed", ("ok",
                             "  mic auto-selected: %s (signal %.0f)" % (best, rms))))
            else:
                self._q.put(("feed", ("warn", "  no usable mic found")))
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_listen(self, _event=None) -> None:
        if self.voice is None:
            self._log_feed("warn", "  voice engine unavailable")
            return
        if self._listen:
            self._listen = False
            self.status_var.set("STANDBY")
            self._refresh_mic_state()
        else:
            self._begin_listen()

    def _begin_listen(self) -> None:
        if self.voice is None:
            return
        self._listen = True
        self.status_var.set("LISTENING…")
        self._refresh_mic_state()
        threading.Thread(target=self._listen_worker, daemon=True).start()

    def _refresh_mic_state(self) -> None:
        if self._listen:
            self.mic_state.config(text="LISTENING", fg=PURPLE)
        else:
            self.mic_state.config(text="STANDBY", fg=TEXT_FAINT)

    def _listen_worker(self) -> None:
        try:
            text = self.voice.listen()
        except Exception as exc:  # noqa: BLE001
            self._q.put(("feed", ("err", "  mic error: %s" % exc)))
            self._q.put(("listen_off", None))
            return
        if text:
            self._q.put(("feed", ("ok", "  🎤 %s" % text)))
            self._q.put(("submit", text))
        else:
            self._q.put(("feed", ("warn", "  nothing heard — try /mic")))
            self._q.put(("listen_off", None))

    def _speak(self, text: str) -> None:
        if self.voice and text:
            try:
                self.voice.speak(text)
            except Exception:  # noqa: BLE001 - speech must never crash the UI
                return

    def _chat(self, who: str, text: str) -> None:
        self.chat.configure(state="normal")
        if who == "you":
            self.chat.insert("end", "YOU", "you")
            self.chat.insert("end", "  %s\n" % text, "body")
        else:
            self.chat.insert("end", "ORION", "orion")
            self.chat.insert("end", "  %s\n" % text, "body")
        self.chat.insert("end", "─" * 40 + "\n", "hr")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _log_feed(self, tag: str, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.feed.configure(state="normal")
        self.feed.insert("end", stamp + " ", "head")
        self.feed.insert("end", text + "\n", tag)
        if int(self.feed.index("end-1c").split(".")[0]) > 40:
            self.feed.delete("1.0", "8.0")
        self.feed.see("end")
        self.feed.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy

    # ------------------------------------------------------- thread bridge

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, args = self._q.get_nowait()
                if kind == "telemetry":
                    self._apply_telemetry(args)
                elif kind == "chat":
                    self._chat(*args)
                elif kind == "feed":
                    self._log_feed(*args)
                elif kind == "dispatch":
                    self._dispatch(args)
                elif kind == "submit":
                    text = args[0] if isinstance(args, tuple) else args
                    self.input.delete(0, "end")
                    self.input.insert(0, text)
                    self._on_send()
                elif kind == "status":
                    self.status_var.set(args[0])
                elif kind == "listen_off":
                    self._listen = False
                    self._refresh_mic_state()
                elif kind == "look":
                    path = args[0] if isinstance(args, tuple) else args
                    self.show_screenshot(path)
                    self.agent.look_at(path)
                    self.status_var.set("STANDBY")
        except queue.Empty:
            pass
        if not self._closing:
            self._after_poll = self.after(60, self._poll_queue)

    def _apply_telemetry(self, data) -> None:
        cpu, mem_avail, mem_total, bat, _temp, disk_pct, disk_free, _rx, _tx = data
        uptime = sys_uptime()
        h, rem = divmod(int(uptime), 3600)
        m, s = divmod(rem, 60)
        self.uptime_label.config(text="UPTIME %02d:%02d:%02d" % (h, m, s))
        self.readouts_left["CPU LOAD"].config(text="%.1f%%" % cpu)
        self.readouts_left["MEMORY"].config(text="%.1f / %.1f GB" % (
            mem_total - mem_avail, mem_total))
        if bat is not None:
            self.power_label_name.config(text="BATTERY")
            self.power_label.config(text="%d%%" % bat)
            self._power = bat / 100.0
        else:
            self.power_label_name.config(text="CORE LOAD")
            self.power_label.config(text="%.0f%%" % cpu)
            self._power = cpu / 100.0
        self.readouts_right["HOSTNAME"].config(text=sys_hostname())
        self.readouts_right["CONNECTION"].config(
            text=self.provider.name.upper())
        self.readouts_right["ENCRYPTION"].config(
            text="LOCAL" if self.provider.name == "ollama" else "TLS-1.3")
        self.diag["PROVIDER"].config(text=self.provider.name.upper())
        self.diag["MODEL"].config(text=getattr(self.provider, "model", "-"))
        self.diag["KERNEL"].config(text=platform_mod.uname().release[:24])
        self.diag["SAFETY"].config(
            text="DRY-RUN" if self.settings.dry_run else "GUARDED",
            fg=WARN if self.settings.dry_run else OK)
        self.diag["DISK /"].config(text="%.0f%% used (%.0fG free)" % (
            disk_pct, disk_free))
        self.diag["TURNS"].config(text=str(len(self.agent.context)))
        self.readouts_left["SYSTEM STATUS"].config(
            text="ACTIVE", fg=OK)

    def _set_fullscreen(self, on: bool) -> None:
        self.attributes("-fullscreen", on)
        self._fullscreen = on

    def _toggle_fullscreen(self, _event=None) -> None:
        self._set_fullscreen(not self._fullscreen)

    def _on_close(self) -> None:
        self._closing = True
        for name in ("_after_animate", "_after_poll",
                     "_after_telemetry", "_after_feed"):
            aid = getattr(self, name, None)
            if aid:
                with contextlib.suppress(tk.TclError):
                    self.after_cancel(aid)
        try:
            self.provider.close()
        except Exception as exc:  # noqa: BLE001 - best-effort close
            self._log_feed("warn", "  [exit] provider shutdown failed: %s" % exc)
        self.destroy()

    _closing = False
    _power = 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orion-hud",
        description="ORION — Autonomous Assistant HUD (tkinter)",
    )
    parser.add_argument("--provider", choices=["auto", "ollama", "api"],
                        default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-voice", action="store_true",
                        help="disable the voice engine entirely (no mic button)")
    parser.add_argument("--cwd", default=None)
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
                    default=read_saved_theme() or "orion")
    except ThemeError as exc:
        print("orion-hud: %s" % exc, file=sys.stderr)
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
            print("orion-hud: cannot chdir to %s: %s" % (settings.working_dir, exc))
            return 2

    try:
        provider = get_provider(settings)
    except LLMError as exc:
        print("orion-hud: %s" % exc)
        return 1

    executor = Executor(settings)

    voice = None
    if not args.no_voice:
        try:
            voice = Voice(settings)
        except VoiceError as exc:
            print("orion-hud: voice unavailable: %s" % exc)

    app = HUD(settings, provider, executor, voice)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
