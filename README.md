# Open Orion

Autonomous terminal-based AI agent (JARVIS-style) for Linux. Speaks natural language,
translates it into safe Bash commands, and manages your machine — with a guarded,
confirmation-based execution flow.

## Architecture

```
main.py                      CLI entry point (python -m orion also works)
├── orion/
│   ├── cli.py               REPL: dispatch, confirmation prompts, rendering
│   ├── gui.py               JARVIS-style desktop UI (pure stdlib tkinter)
│   ├── agent.py             shared control-loop + action dispatch (Host protocol)
│   ├── config.py            pydantic-settings: providers, models, safety toggles
│   ├── security.py          static risk classifier + hard-block/risky patterns
│   ├── executor.py          sandboxed shell runner + read/list file tools
│   ├── llm.py               Ollama (local) and LiteLLM (OpenAI/Anthropic) providers
│   ├── models.py            JSON action contract + parser
│   ├── prompt.py            system prompt + environment fingerprint
│   ├── context.py           bounded conversation history (ring buffer)
│   ├── memory.py            persistent note store (survives restarts)
│   ├── system.py            real /proc telemetry shared by the desktop UIs
│   ├── themes.py            shared color palettes + saved UI prefs
│   └── voice.py             local STT (faster-whisper) + TTS (piper/Kokoro/XTTS)
├── jarvis_hud.py            standalone futuristic HUD (tkinter)
└── tests/                   pytest suite for the core modules
```

The agent is a **control loop**: the LLM emits a single JSON action
(`run | read | ls | screenshot | remember | ask | done`), a local static guard
classifies the command, the user confirms risky actions, the command executes,
and the output is fed back into context for the next step. The CLI and both
desktop UIs share one dispatch implementation (`orion.agent`) so the safety/
confirmation flow is identical everywhere.

## Quickstart

```bash
# 1. Local model (recommended)
ollama serve
ollama pull qwen3.5:9b   # or llama3.1:8b

# 2. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # optional; defaults work with local Ollama

# 3. Run
python main.py                 # or: python -m orion
```

Cloud API (OpenAI/Anthropic) requires `litellm` (already in requirements):

```bash
ORION_PROVIDER=api ORION_API_MODEL=openai/gpt-4o-mini ORION_API_KEY=sk-... python main.py
```

### Install as a package (optional)

The project ships a `pyproject.toml` with console entry points, so you can
install it properly instead of running from the repo:

```bash
pip install -e ".[all]"        # everything; or [voice], [api]
orion                          # CLI REPL
orion-gui                      # JARVIS-style deck
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
./.venv-gui/bin/python jarvis_hud.py      # launch the HUD
./.venv-gui/bin/python jarvis_hud.py --no-voice
./.venv-gui/bin/python jarvis_hud.py --dry-run
```

### Themes

Both GUIs share one palette system. Pick a palette with `--theme <name>`,
the `ORION_THEME` env var, or `ORION_THEME=` in `.env` (CLI flag wins, then
the env var, then `.env`). Built-in palettes:

| Name        | Look                                   |
|-------------|----------------------------------------|
| `jarvis`    | Iron-Man cyan deck (GUI default)       |
| `orion`     | Purple arc-reactor HUD (HUD default)   |
| `matrix`    | Green phosphor on black                |
| `solarized` | Solarized dark                         |
| `nord`      | Nord                                   |
| `amber`     | Warm amber-on-black retro console      |

```bash
./.venv-gui/bin/python jarvis_hud.py --theme matrix
./.venv-gui/bin/python jarvis_hud.py --list-themes
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

### Settings menu (in the GUI)

Hit the **⚙ SETTINGS** button in the deck's footer (or type `/settings`).
The Control Deck panel lets you:

- **PALETTE** — click any built-in theme to switch instantly; each button
  is rendered in the palette it selects. The choice is saved for the next
  launch (stored in `~/.local/share/open-orion/ui.json`).
- **OPTIONS** — toggle `dry-run`, `vision`, and `talk` (voice mode) live.

Switching palettes rebuilds the deck in-place and preserves your conversation
transcript and command log. The standalone HUD (`jarvis_hud.py`) reads the
same saved palette on launch.

Every readout is real, read live from the machine — nothing is fake:

| Readout            | Source                                              |
|--------------------|-----------------------------------------------------|
| SYSTEM STATUS      | `ACTIVE` once the LLM provider connects             |
| CPU LOAD           | sampled from `/proc/stat`                           |
| MEMORY             | `MemTotal` / `MemAvailable` from `/proc/meminfo`    |
| POWER              | battery capacity from `/sys/class/power_supply`, else real CPU load |
| UPTIME             | `/proc/uptime`                                      |
| HOSTNAME          | `socket.gethostname()`                            |
| CONNECTION        | `OLLAMA` or `API`, from the active provider         |
| ENCRYPTION        | `LOCAL` for Ollama, `TLS-1.3` for cloud APIs        |
| NET RATE          | RX/TX deltas from `/proc/net/dev`                   |
| DISK /            | `shutil.disk_usage("/")`                            |
| AI CORE panel     | provider, model, kernel, safety, disk, context turns

The HUD runs the **real Orion agent loop** — your typed or spoken input goes
through `orion.llm` → JSON action (`parse_action`) → `orion.executor`, with
the guarded execution, context history, slash commands and voice from the
same modules the CLI uses. The center emblem switches to `THINKING` and
pulses while the model works.

> Note: Tk is a system package. If your venv Python lacks it, either
> `sudo pacman -S tk` or use the bundled `./.venv-gui` (Python 3.12 with Tk).
> The launcher picks a working interpreter automatically.

## Talk mode (voice)

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

- Commands run via `bash -c` as your current user — the agent inherits your permissions.
- Inspect system state with read-only commands (`ps`, `df`, `journalctl`, `ls`, …); the
  model prefers these unless a mutation is required.
