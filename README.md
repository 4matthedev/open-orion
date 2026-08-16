# Open Orion

Autonomous terminal-based AI agent (JARVIS-style) for **Linux and Windows**.
Speaks natural language, translates it into safe shell commands (Bash on
Linux, PowerShell on Windows), and manages your machine — with a guarded,
confirmation-based execution flow.

## Architecture

```
main.py                      CLI entry point (python -m orion also works)
├── orion/
│   ├── cli/
│   │   ├── cli.py               REPL: dispatch, confirmation prompts, rendering
│   │   └── prompt.py            system prompt + environment fingerprint
│   ├── core/
│   │   ├── agent.py             shared control-loop + action dispatch (Host protocol)
│   │   ├── config.py            pydantic-settings: providers, models, safety toggles
│   │   ├── executor.py          sandboxed shell runner (bash / PowerShell) + file tools
│   │   ├── context.py           bounded conversation history (ring buffer)
│   │   ├── memory.py            persistent note store (survives restarts)
│   │   └── system.py            real telemetry shared by the desktop HUD
│   ├── providers/
│   │   ├── llm.py               Ollama (local) and LiteLLM (OpenAI/Anthropic) providers
│   │   ├── voice.py             local STT (faster-whisper) + TTS (piper/Kokoro/XTTS)
│   │   └── platform.py          cross-platform helpers (OS, data dirs, shell)
│   ├── utils/
│   │   ├── security.py          static risk classifier + hard-block/risky patterns
│   │   └── models.py            JSON action contract + parser
│   └── ui/
│       ├── jarvis_hud.py        futuristic HUD (tkinter)
│       └── themes.py            shared color palettes + saved UI prefs
├── scripts/
│   ├── install.sh / install.ps1 Linux & Windows one-command installers
│   ├── orion-hud.sh            Linux HUD launcher
│   ├── orion.bat / orion-hud.bat  Windows CLI / HUD launchers
├── assets/                      raw audio, samples, and voice-model files
├── tools/voice_pipeline.py      streaming voice-cloning tool (F5-TTS)
└── tests/                       pytest suite for the core modules
```

The agent is a **control loop**: the LLM emits a single JSON action
(`run | read | ls | screenshot | remember | ask | done`), a local static guard
classifies the command, the user confirms risky actions, the command executes,
and the output is fed back into context for the next step. On Linux commands
run through `bash`; on Windows they run through `powershell.exe`, and the
system prompt tells the model which shell it is talking to. The CLI and the
HUD share one dispatch implementation (`orion.core.agent`) so the safety/
confirmation flow is identical everywhere.

## Platforms

| Feature              | Linux | Windows |
|----------------------|-------|---------|
| CLI agent            | ✔     | ✔       |
| HUD (tkinter)        | ✔     | ✔ (a Python with Tk, e.g. from python.org) |
| System telemetry     | ✔ `/proc` | ✔ psutil / ctypes |
| Screenshots          | ✔ grim / import | ✔ PowerShell System.Drawing |
| Talk mode (voice)    | ✔ (PulseAudio/PipeWire) | — graceful, disables itself |

## Binaries & installers

Pre-built installers are attached to each [GitHub release](https://github.com/4matthedev/open-orion/releases), built automatically by CI (a tag push `v*` rebuilds and re-uploads them):

| Asset                                                       | What it is                                        |
|-------------------------------------------------------------|---------------------------------------------------|
| `open-orion-<ver>-windows-x86_64-setup.exe`                 | **Windows installer** (Inno Setup) — Start-menu + desktop shortcuts, custom app icon, and an optional task that installs **Ollama** and pulls the default **qwen3.5:4b** model so the first run just works |
| `open-orion_<ver>_amd64.deb`                                | Debian/Ubuntu package (`sudo apt install ./open-orion_<ver>_amd64.deb`) |
| `open-orion-<ver>-linux-x86_64.tar.gz`                      | Portable Linux bundle with `install.sh` — installs the app, adds a menu entry w/ icon, and bootstraps **Ollama** + pulls **qwen3.5:4b** (same model handling as the Windows installer) |

The Windows executables embed the Open Orion icon and full version metadata
(company/product/description), which keeps SmartScreen and Windows Defender
heuristics happy; CI also Authenticode-signs the exe's and installer when a
`WINDOWS_CERT_PFX` / `WINDOWS_CERT_PASSWORD` secret pair is configured.

## Quickstart

### One-command install

```bash
scripts/install.sh           # everything (CLI + cloud API + voice + pipeline)
```

The installer creates a `.venv`, installs Open Orion, and copies `.env.example`
to `.env`. Prefer smaller variants to keep the install light:

```bash
scripts/install.sh --core          # CLI core only (no cloud API, no voice)
scripts/install.sh --api           # + cloud API backends (LiteLLM)
scripts/install.sh --voice         # + local STT/TTS
scripts/install.sh --voice-pipeline  # + streaming voice-cloning tool (F5-TTS)
```

The GUI/HUD needs tkinter (a system package): `sudo pacman -S tk` on Arch, or
`sudo apt install python3-tk` on Debian/Ubuntu.

### Windows install (PowerShell)

```powershell
.\scripts\install.ps1                # everything; or:
.\scripts\install.ps1 -Core          # CLI core only
.\scripts\install.ps1 -Api           # + cloud API backends
.\scripts\install.ps1 -Voice         # + voice TTS/STT packages (talk mode still Linux-only)
.\scripts\install.ps1 -VoicePipeline # + streaming voice-cloning tool

.\scripts\orion.bat                  # CLI REPL
.\scripts\orion-hud.bat              # futuristic HUD
```

Use a Python build that bundles tkinter (the standard installer from
python.org does). Generated commands run through PowerShell; point the broker at
a local Ollama server or a cloud API exactly like on Linux. Windows data files
(memory, theme prefs, screenshots) live under `%LOCALAPPDATA%\open-orion`.
`main.py --gui` launches the HUD too (`scripts/orion.bat`/`scripts/orion-hud.bat` both work).

### Manual install

```bash
# 1. Local model (recommended)
ollama serve
ollama pull qwen3.5:4b   # pull any model you like — Orion auto-detects what's installed

# 2. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # optional; defaults work with local Ollama

# 3. Run
python main.py                 # or: python -m orion
```

> **Zero-config models:** Orion works with *whatever* you have pulled into
> Ollama — no model setting needed. At startup it auto-picks the best
> installed model, and you can switch anytime with `/model <name>` (or use
> the **MODEL LIST** dropdown in the HUD, or `/models` to see everything
> installed). Want a specific one? Set `ORION_OLLAMA_MODEL`.

Cloud API (OpenAI/Anthropic) requires `litellm` (already in requirements):

```bash
ORION_PROVIDER=api ORION_API_MODEL=openai/gpt-4o-mini ORION_API_KEY=sk-... python main.py
```

### Install as a package (optional)

The project ships a `pyproject.toml` with console entry points, so you can
install it properly instead of running from the repo:

```bash
pip install -e ".[all]"        # everything; or [voice], [api], [voice-pipeline]
orion                          # CLI REPL
orion-hud                      # futuristic HUD
```

## Tests

```bash
python -m pytest               # unit tests for security, parsing, context,
                               # memory, themes, executor, config, telemetry, LLM
```

## Safety model

Every generated command is statically classified before execution:

| Level      | Policy                                                        |
|------------|---------------------------------------------------------------|
| `safe`     | runs without a prompt                                         |
| `risky`    | requires interactive `Y/n` confirmation (`sudo`, `rm`, `chmod`, service stop, shutdown, `>` truncation, kill, …) |
| `forbidden`| always hard-blocked (`rm -rf /`, `dd`/`mkfs` on block devices, fork bombs, `curl | sudo sh`, overwriting `/etc/passwd`, …) |

Additional guardrails:
- `--dry-run` prints commands without executing; `ORION_DRY_RUN=true` in `.env`.
- `ORION_SAFETY_LEVEL=strict` prompts before *every* command; `confirm` (default)
  prompts for risky/destructive ones; `permissive` auto-runs risky ones; `auto`
  (or `python main.py --auto`) is fully autonomous — no prompts at all.
- `ORION_ALWAYS_CONFIRM=true` force-prompts before every command, overriding
  `auto`/`permissive` as a safety valve.
- Output is size-truncated (`ORION_MAX_OUTPUT_CHARS`) so context never explodes.
- Every command passes `bash -n` syntax validation before execution.

### Autonomous mode

```bash
python main.py --auto        # run every command without asking
```

In `auto` mode Open Orion has unfettered execution access as your user and never
interrupts you for confirmation — it simply tells you what it's doing. The
`forbidden` hard-blocks above (`rm -rf /`, block-device writes, fork bombs, …)
are intentionally left in place as a last line of defense against a hallucinated
catastrophic command; they cannot be disabled from the CLI.

The classifier is pattern-based (not perfect); in autonomous mode the model
itself is the primary guard, so run it only with models you trust.

## Usage

```text
orion> show me disk usage and the top 5 largest files in ~/Projects
orion> free up space in the apt cache        (confirmation offered)
orion> /status                               (provider/model/safety status)
orion> /models                               (list installed models)
orion> /model llama3.1:8b                    (hot-swap local model)
orion> /clear                                (reset context)
orion> /exit
```

## Desktop GUI (ORION HUD)

A futuristic purple HUD command deck built with **only the Python standard
library** (`tkinter`): animated HUD visualizer, boot sequence, conversation
transcript, real system telemetry, live command log, voice toggle and slash
commands.

Voice is **button-activated** — the HUD never listens on its own. Click the
**VOICE INTERFACE** mic button to record one utterance, speak, and it stops
when you go quiet (push to talk). Use `--no-voice` to disable the engine.

```bash
./scripts/orion-hud.sh                          # launch the HUD
.venv/bin/python -m orion.ui.jarvis_hud --no-voice
.venv/bin/python -m orion.ui.jarvis_hud --dry-run
```

### Themes

The HUD uses one palette system. Pick a palette with `--theme <name>`, the
`ORION_THEME` env var, or `ORION_THEME=` in `.env` (CLI flag wins, then the env
var, then `.env`). Built-in palettes:

| Name        | Look                                   |
|-------------|----------------------------------------|
| `orion`     | Purple arc-reactor HUD (default)       |
| `jarvis`    | Iron-Man cyan deck                     |
| `matrix`    | Green phosphor on black                |
| `solarized` | Solarized dark                         |
| `nord`      | Nord                                   |
| `amber`     | Warm amber-on-black retro console      |

```bash
.venv/bin/python -m orion.ui.jarvis_hud --theme matrix
.venv/bin/python -m orion.ui.jarvis_hud --list-themes
```

**Custom themes:** point `--theme` (or `ORION_THEME`) at a JSON file. Any key
you omit inherits from the `base` palette (default `jarvis`):

```json
{
  "base": "orion",
  "name": "my-amber",
  "bg": "#000000",
  "bg_panel": "#101010",
  "accent": "#ffb84d",
  "accent_dim": "#6e4a00",
  "accent_faint": "#332200",
  "text_dim": "#e8d8b8",
  "ok": "#7fd07f",
  "error": "#ff6b6b"
}
```

Available keys: `bg`, `bg_panel`, `bg_deep`, `bg_edge`, `grid`, `fg`,
`accent`, `accent_dim`, `accent_faint`, `accent_2`, `text_dim`, `text_faint`,
`ok`, `warn`, `error`, `command`, `muted`.

The palette picked at launch is saved for the next launch (stored in
`~/.local/share/open-orion/ui.json` or `%LOCALAPPDATA%\open-orion\ui.json` on
Windows).

Every readout is real, read live from the machine — nothing is fake (on
Windows the sources swap to psutil/ctypes equivalents):

| Readout            | Source                                              |
|--------------------|-----------------------------------------------------|
| SYSTEM STATUS      | `ACTIVE` once the LLM provider connects             |
| CPU LOAD           | sampled from `/proc/stat` (Windows: GetSystemTimes) |
| MEMORY             | `MemTotal` / `MemAvailable` from `/proc/meminfo` (Windows: GlobalMemoryStatusEx) |
| POWER              | battery capacity from `/sys/class/power_supply`, else real CPU load |
| UPTIME             | `/proc/uptime` (Windows: GetTickCount64)            |
| HOSTNAME          | `socket.gethostname()`                            |
| CONNECTION        | `OLLAMA` or `API`, from the active provider         |
| ENCRYPTION        | `LOCAL` for Ollama, `TLS-1.3` for cloud APIs        |
| NET RATE          | RX/TX deltas from `/proc/net/dev` (Windows: psutil) |
| DISK /            | `shutil.disk_usage("C:\\")` / `shutil.disk_usage("/")` |
| AI CORE panel     | provider, model, kernel, safety, disk, context turns + **MODEL LIST** dropdown (hot-swap any installed Ollama model) |

The HUD runs the **real Orion agent loop** — your typed or spoken input goes
through `orion.llm` → JSON action (`parse_action`) → `orion.executor`, with
the guarded execution, context history, slash commands and voice from the
same modules the CLI uses. The center emblem switches to `THINKING` and
pulses while the model works.

> Note: Tk is a system package. If your venv Python lacks it, install it with
> `sudo pacman -S tk` or `sudo apt install python3-tk` and recreate the venv.

## Talk mode (voice)

> **Windows note:** talk mode is Linux-only for now. The voice engine needs
> PulseAudio/PipeWire (`parec`, `paplay`, `pactl`); on Windows it detects the
> platform and disables itself with a clear message — the CLI and HUD keep
> working normally.

Speak to Open Orion and have it speak back — fully local:

```bash
python main.py --voice        # start with voice on
```

Inside the REPL, `/talk` toggles voice on/off. In voice mode Open Orion
records the microphone, transcribes with faster-whisper (`ORION_STT_MODEL`,
default `small.en`), and speaks its replies with Kokoro (`Kokoro-82M` — very
natural, local) or piper (fallback), playback via `paplay`.

First-time setup for the Kokoro model files (~/.local/share/kokoro/):

```bash
curl -L -o ~/.local/share/kokoro/kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o ~/.local/share/kokoro/voices-v1.0.bin  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Config knobs: `ORION_VOICE_ENABLED`, `ORION_STT_MODEL`, `ORION_STT_DEVICE`,
`ORION_TTS_ENGINE` (kokoro/piper/xtts), `ORION_TTS_KOKORO_VOICE`
(e.g. `am_michael`, `am_onyx`, `af_heart`, `bm_george`),
`ORION_TTS_KOKORO_SPEED`, `ORION_TTS_MODEL`, `ORION_TTS_LENGTH_SCALE`,
`ORION_VOICE_TIMEOUT`.

## Notes

- Commands run via `bash -c` (Linux) or `powershell.exe -Command` (Windows) as
  your current user — the agent inherits your permissions.
- Inspect system state with read-only commands (`ps`, `df`, `Get-ChildItem`,
  `journalctl`, `ls`, …); the model prefers these unless a mutation is required.

## Streaming voice-cloning pipeline

`tools/voice_pipeline.py` is a self-contained, low-latency streaming TTS tool
(F5-TTS, sentence-by-sentence playback) with zero-shot voice cloning from a
reference clip. Streams the LLM directly — it runs outside the agent loop and
has no safety guard:

```bash
python tools/voice_pipeline.py "tell me about yourself"
python tools/voice_pipeline.py --interactive
```
