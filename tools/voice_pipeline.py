#!/usr/bin/env python3
"""OrionVoicePipe — low-latency streaming TTS pipeline (standalone tool).

This is a legacy, self-contained script kept separate from the packaged
``orion.voice`` module. It is NOT wired into the main agent: it streams the
LLM directly, so no JSON-action contract, safety guard, or confirmation flow
is involved.

Pipeline: LLM stream (local Ollama 9B) -> sentence splitting -> zero-shot
voice synthesis (F5-TTS cloned from orion_raw.mp3) -> Orion DSP effect ->
immediate playback.

Plays each completed sentence as soon as it is synthesized, so audio starts
long before the model finishes the whole response. No training: F5-TTS does
zero-shot voice cloning from the reference clip at inference time.

Usage (from the repository root):
    python tools/voice_pipeline.py "tell me about yourself"
    python tools/voice_pipeline.py --interactive
    python tools/voice_pipeline.py --llm "how do you see humanity?" --no-play
"""

from __future__ import annotations

import argparse
import re
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3.5:9b"
REF_AUDIO = Path(__file__).resolve().parents[1] / "orion_raw.mp3"

# Spoken-style persona; keep it short so the 9B model stays on voice.
PERSONA = """
You are Orion, a warm, respectful, and capable voice assistant.
Always be polite and courteous, never condescending. Speak in short, natural
sentences suited to being read aloud. Never use markdown, lists, or emojis.
Reply only with plain spoken prose, 2-5 sentences.
""".strip()


# --------------------------------------------------------------------------
# 1. LLM stream
# --------------------------------------------------------------------------

def stream_llm(
    prompt: str,
    base_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.1,
) -> list[str]:
    """Yield text deltas as Ollama streams /api/chat. Cleans up on exit."""
    client = httpx.Client(timeout=None)
    try:
        with client.stream(
            "POST",
            f"{base_url.rstrip('/')}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": PERSONA},
                    {"role": "user", "content": prompt},
                ],
                "stream": True,
                "think": False,  # bypass reasoning tokens for low first-token latency
                "options": {"temperature": temperature},
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                data = __import__("json").loads(line)
                if data.get("done"):
                    return
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
    finally:
        client.close()


# --------------------------------------------------------------------------
# 2. Sentence assembly
# --------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


class SentenceAssembler:
    """Accumulate token chunks into completed sentences as they arrive."""

    def __init__(self, max_len: int = 280) -> None:
        self._buf = ""
        self.max_len = max_len

    def feed(self, chunk: str) -> list[str]:
        self._buf += chunk
        out: list[str] = []
        while True:
            parts = _SENT_SPLIT.split(self._buf, maxsplit=1)
            if len(parts) == 1:
                break
            sentence = parts[0].strip()
            self._buf = parts[1]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest = self._buf.strip()
        self._buf = ""
        return [rest] if rest else []


# --------------------------------------------------------------------------
# 3. TTS backends
# --------------------------------------------------------------------------

def _writes16(path: Path, wav, sr: int) -> None:
    import numpy as np
    import soundfile as sf

    try:
        samples = wav.detach().cpu().numpy()
    except AttributeError:
        samples = np.asarray(wav)
    samples = samples.reshape(-1) if samples.ndim > 1 else samples
    sf.write(str(path), samples, sr)


class F5Engine:
    """Zero-shot voice cloning from the reference clip (fast on CUDA)."""

    name = "f5"

    def __init__(self, ref_audio: Path, ref_text: str = "", device: str | None = None, nfe: int = 16) -> None:
        from f5_tts.api import F5TTS  # noqa: PLC0415

        self.ref = str(ref_audio)
        self.ref_text = ref_text
        self.nfe = nfe
        self.device = device or ("cuda" if self._cuda() else None)
        self._tts = F5TTS(device=self.device, ode_method="euler")

    @staticmethod
    def _cuda() -> bool:
        try:
            import torch  # noqa: PLC0415

            return bool(torch.cuda.is_available())
        except ImportError:
            return False

    def synthesize(self, text: str, out_path: Path) -> None:
        wav, sr, _ = self._tts.infer(
            ref_file=self.ref,
            ref_text=self.ref_text,
            gen_text=text,
            nfe_step=self.nfe,
            cfg_strength=2.0,
            speed=1.0,
            remove_silence=True,
        )
        _writes16(out_path, wav, sr)


class PiperEngine:
    """Fast CPU fallback (single pre-set voice; cannot clone a reference)."""

    name = "piper"

    def __init__(self, model_onnx: Path | None = None) -> None:
        self.bin = shutil.which("piper") or self._in_venv_piper()
        self.model = model_onnx or self._find_voice()
        if not self.bin:
            raise RuntimeError("piper binary not found")
        if not self.model or not self.model.exists():
            raise RuntimeError("no piper voice .onnx found; pass --piper-model")

    @staticmethod
    def _in_venv_piper() -> str | None:
        cand = Path(sys.prefix) / "bin" / "piper"
        return str(cand) if cand.exists() else None

    @staticmethod
    def _find_voice() -> Path | None:
        root = Path.home() / ".local/share/piper_voices"
        pref = ("en_US/ryan/medium/en_US-ryan-medium.onnx", "en_US/lessac/medium/en_US-lessac-medium.onnx")
        for rel in pref:
            cand = root / rel
            if cand.exists():
                return cand
        matches = sorted(root.glob("**/*.onnx"))
        return matches[0] if matches else None

    def synthesize(self, text: str, out_path: Path) -> None:
        subprocess.run(
            [
                self.bin,
                "--model", str(self.model),
                "--output_file", str(out_path),
                "--sentence_silence", "0.4",
            ],
            input=text,
            text=True,
            capture_output=True,
            timeout=120,
            check=True,
        )


def make_engine(args, ref_text: str):
    if args.engine == "piper":
        return PiperEngine(args.piper_model)
    try:
        return F5Engine(REF_AUDIO, ref_text=ref_text, device=args.device, nfe=args.nfe)
    except Exception as exc:  # noqa: BLE001 - fall back to piper on any F5 failure
        print(f"[voice] F5 unavailable ({exc}); falling back to piper")
        return PiperEngine(args.piper_model)


def transcribe_reference(ref_audio: Path) -> str:
    """Transcribe the reference clip locally (faster-whisper, already cached)."""
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415

        model = WhisperModel("small.en", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(ref_audio), beam_size=5, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[voice] reference transcription failed: {exc}")
        return ""


# --------------------------------------------------------------------------
# 4. Orion DSP effect (the classic metallic chain — see git history; the
#    packaged orion/voice.py uses a gentler "humanize" chain instead)
# --------------------------------------------------------------------------

_ORION_FILTER = (
    "asetrate=22050*0.88,"
    "aresample=22050,"
    "atempo=1.1364,"
    "acrusher=bits=14:mode=log:aa=1:mix=0.20,"
    "acompressor=threshold=0.06:ratio=3:attack=5:release=120"
)


def apply_orion_effect(wav: Path) -> None:
    """Apply the metallic Orion DSP chain in place (no-op if ffmpeg missing)."""
    if shutil.which("ffmpeg") is None:
        return
    tmp = wav.with_suffix(".fx.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav), "-af", _ORION_FILTER, str(tmp)],
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        tmp.replace(wav)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
        sys.stderr.write(f"[voice] Orion effect skipped: {exc}\n")


# --------------------------------------------------------------------------
# 5. Pipeline (syntax -> synth -> effect -> play, sentence by sentence)
# --------------------------------------------------------------------------

def run_pipeline(
    engine,
    play: bool,
    remove_audio: bool,
    prompt: str | None = None,
    base_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_MODEL,
    out_dir: Path | None = None,
) -> None:
    text_bus: "queue.Queue[str | None]" = queue.Queue(maxsize=8)
    audio_bus: "queue.Queue[Path | None]" = queue.Queue(maxsize=8)
    paplay = shutil.which("paplay") if play else None
    stop = threading.Event()

    def synthesizer() -> None:
        while True:
            sentence = text_bus.get()
            if sentence is None:
                audio_bus.put(None)
                return
            wav = Path(tempfile.mkstemp(suffix=".wav", prefix="orion_")[1])
            try:
                engine.synthesize(sentence, wav)
                apply_orion_effect(wav)
                audio_bus.put(wav)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"[voice] synth failed: {exc}\n")
                wav.unlink(missing_ok=True)

    def player() -> None:
        while True:
            wav = audio_bus.get()
            if wav is None:
                return
            try:
                if paplay:
                    subprocess.run([paplay, str(wav)], capture_output=True, timeout=300)
                if out_dir:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    target = out_dir / f"{time.monotonic():.3f}.wav"
                    wav.replace(target)
            finally:
                if remove_audio and wav.exists():
                    wav.unlink(missing_ok=True)

    synth_thread = threading.Thread(target=synthesizer, daemon=True)
    player_thread = threading.Thread(target=player, daemon=True)
    synth_thread.start()
    player_thread.start()

    assembler = SentenceAssembler()
    started = time.monotonic()
    try:
        if prompt:
            chunks = stream_llm(prompt, base_url=base_url, model=model)
        else:
            chunks = [line.rstrip("\n") for line in sys.stdin]
            print("[voice] reading prompt from stdin…", file=sys.stderr)
        for chunk in chunks:
            for sent in assembler.feed(chunk):
                print(f"[sentence @{time.monotonic()-started:5.2f}s] {sent}")
                text_bus.put(sent)
        for tail in assembler.flush():
            print(f"[sentence @{time.monotonic()-started:5.2f}s] {tail}")
            text_bus.put(tail)
    except KeyboardInterrupt:
        print("\n[voice] interrupted", file=sys.stderr)
    finally:
        stop.set()
        text_bus.put(None)
        synth_thread.join(timeout=600)
        audio_bus.put(None)
        player_thread.join(timeout=600)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voice_pipeline", description="Orion low-latency streaming TTS")
    parser.add_argument("prompt", nargs="*", help="one-shot prompt (if omitted, read stdin)")
    parser.add_argument("--interactive", "-i", action="store_true", help="loop: prompt -> speak")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--engine", choices=["auto", "f5", "piper"], default="auto")
    parser.add_argument("--piper-model", default=None, type=Path)
    parser.add_argument("--nfe", type=int, default=16, help="F5 diffusion steps (lower=faster)")
    parser.add_argument("--device", default=None)
    parser.add_argument("--ref-text", default="", help="transcription of orion_raw.mp3 (cached if empty)")
    parser.add_argument("--no-play", action="store_true", help="synthesize + effect but don't play audio")
    parser.add_argument("--keep", action="store_true", help="keep generated .wav files (temp dir)")
    parser.add_argument("--out-dir", default=None, type=Path, help="save generated wavs to dir (implies --keep)")
    args = parser.parse_args(argv)

    ref_text = args.ref_text
    if not ref_text:
        print("[voice] transcribing reference clip…", file=sys.stderr)
        ref_text = transcribe_reference(REF_AUDIO)

    if args.engine == "auto":
        args.engine = "f5"

    out_dir = args.out_dir or (Path(tempfile.mkdtemp(prefix="orion_aud/")) if args.keep else None)

    engine = make_engine(args, ref_text)
    print(f"[voice] engine={engine.name} ref={REF_AUDIO.name}", file=sys.stderr)

    try:
        if args.interactive:
            while True:
                prompt = input("orion> ").strip()
                if prompt.lower() in ("/exit", "/quit", "q"):
                    break
                if prompt:
                    run_pipeline(
                        engine,
                        play=not args.no_play,
                        remove_audio=out_dir is None,
                        prompt=prompt,
                        base_url=args.base_url,
                        model=args.model,
                        out_dir=out_dir,
                    )
        else:
            prompt = " ".join(args.prompt).strip()
            run_pipeline(
                engine,
                play=not args.no_play,
                remove_audio=out_dir is None,
                prompt=prompt or None,
                base_url=args.base_url,
                model=args.model,
                out_dir=out_dir,
            )
    except EOFError:
        pass
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())