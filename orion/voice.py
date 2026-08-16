"""Local voice loop: speech-to-text (faster-whisper) + text-to-speech (piper).

Everything runs on this machine. The microphone is streamed with ``parec``
(16 kHz mono PCM) and transcribed by faster-whisper; replies are synthesized
by piper and played back with ``paplay``. Multiple mics are probed so the one
that actually hears you is selected automatically — switchable with ``/mic``.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import wave
from pathlib import Path

from .config import AppSettings
from .platform import is_windows, kokoro_dir, piper_voices_dir

_TMP = Path(tempfile.gettempdir())


class VoiceError(RuntimeError):
    """Raised when the audio stack or voice engines are unavailable."""


class Voice:
    """Combines TTS output and STT input for the REPL."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._speak_lock = threading.Lock()
        self._speak_thread: threading.Thread | None = None
        self._stt_model = None
        self._mic_lock = threading.Lock()
        self._record_lock = threading.Lock()
        self.mic: str | None = None
        self._xtts = None
        self._kokoro = None
        self._kokoro_lock = threading.Lock()
        if is_windows():
            raise VoiceError(
                "voice mode is currently Linux-only (needs PulseAudio/PipeWire "
                "tools: parec, paplay, pactl). Run Open Orion under WSL or on a "
                "Linux machine for talk mode."
            )
        self._tts_model = self._resolve_tts_model()
        self._validate_tools()
        self._preload_tts()

    # -- availability ----------------------------------------------------

    def _validate_tools(self) -> None:
        missing = [tool for tool in ("paplay",) if shutil.which(tool) is None]
        if shutil.which("parec") is None and shutil.which("arecord") is None:
            missing.append("parec/arecord")
        if missing:
            raise VoiceError("missing audio tools: " + ", ".join(missing))
        if self.settings.tts_engine == "piper":
            if not self._piper_bin():
                raise VoiceError("piper-tts is not installed (pip install piper-tts)")
            if not self._tts_model:
                raise VoiceError(
                    "no piper voice found; run: python -m piper.download_voices en_US-ryan-medium"
                )
        elif self.settings.tts_engine == "kokoro":
            model, voices = self._kokoro_paths()
            if not model.exists() or not voices.exists():
                raise VoiceError(
                    "Kokoro model files missing; download them into %s/:\n"
                    "  curl -L -o %s/kokoro-v1.0.onnx "
                    "https://github.com/thewh1teagle/kokoro-onnx/releases/"
                    "download/model-files-v1.0/kokoro-v1.0.onnx\n"
                    "  curl -L -o %s/voices-v1.0.bin "
                    "https://github.com/thewh1teagle/kokoro-onnx/releases/"
                    "download/model-files-v1.0/voices-v1.0.bin"
                    % (kokoro_dir(), kokoro_dir(), kokoro_dir())
                )
            try:
                import kokoro_onnx  # noqa: PLC0415 - lazy, heavy import
            except ImportError as exc:
                raise VoiceError(
                    "Kokoro requires kokoro-onnx: pip install kokoro-onnx espeakng-loader"
                ) from exc
        else:  # xtts
            if not self.settings.tts_reference:
                raise VoiceError(
                    "XTTS requires a reference voice clip: set ORION_TTS_REFERENCE "
                    "to a .wav of the voice to clone"
                )
            try:
                import TTS  # noqa: PLC0415 - lazy, heavy import
            except ImportError as exc:
                raise VoiceError(
                    "XTTS requires coqui-tts: pip install coqui-tts"
                ) from exc

    def _kokoro_paths(self) -> tuple[Path, Path]:
        default_dir = kokoro_dir()
        model = Path(self.settings.tts_kokoro_model or default_dir / "kokoro-v1.0.onnx")
        voices = Path(self.settings.tts_kokoro_voices or default_dir / "voices-v1.0.bin")
        return model, voices

    @staticmethod
    def _piper_bin() -> str | None:
        if shutil.which("piper"):
            return shutil.which("piper")
        bindir = "Scripts" if is_windows() else "bin"
        candidate = Path(sys.prefix) / bindir / "piper"
        return str(candidate) if candidate.exists() else None

    def _resolve_tts_model(self) -> str | None:
        if self.settings.tts_model:
            return self.settings.tts_model
        preferred = (
            "en/en_US/ryan/medium/en_US-ryan-medium.onnx",
            "en/en_US/joe/medium/en_US-joe-medium.onnx",
            "en/en_GB/graham/medium/en_GB-graham-medium.onnx",
            "en/en_US/danny/low/en_US-danny-low.onnx",
        )
        root = piper_voices_dir()
        for rel in preferred:
            candidate = root / rel
            if candidate.exists():
                return str(candidate)
        matches = sorted(root.glob("**/*.onnx"))
        return str(matches[0]) if matches else None

    def _humanize(self, wav: Path) -> None:
        """Post-process a TTS wav in place for a warm, natural human voice.

        The old "orionize" effect (bit-crushing + a deep 12% pitch drop) made
        speech sound metallic and robotic; this just adds a very slight depth
        and a soft compressor so the level stays even. If ffmpeg is unavailable
        the voice is left unchanged.
        """
        if shutil.which("ffmpeg") is None:
            return
        tmp = wav.with_suffix(".fx.wav")
        filter_chain = (
            "asetrate=22050*0.96,"
            "aresample=22050,"
            "atempo=1.0417,"
            "acompressor=threshold=0.08:ratio=1.8:attack=10:release=180"
        )
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(wav),
                    "-af",
                    filter_chain,
                    str(tmp),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            tmp.replace(wav)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            sys.stderr.write(f"[voice] Orion effect skipped: {exc}\n")

    # -- microphone selection ----------------------------------------------

    def list_sources(self) -> list[dict]:
        """Return capture sources (excluding loopback monitors) with descriptions."""
        try:
            short = subprocess.run(
                ["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=10
            ).stdout
        except (subprocess.TimeoutExpired, OSError):
            return [{"name": "default", "description": "system default"}]
        sources: list[dict] = []
        for line in short.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "monitor" not in parts[1] and "output" not in parts[1]:
                sources.append({"name": parts[1], "description": parts[1]})
        try:
            detail = subprocess.run(
                ["pactl", "list", "sources"], capture_output=True, text=True, timeout=10
            ).stdout
        except (subprocess.TimeoutExpired, OSError):
            detail = ""
        current = None
        for line in detail.splitlines():
            if line.strip().startswith("Name: "):
                current = line.split(":", 1)[1].strip()
            elif line.strip().startswith("Description: "):
                for src in sources:
                    if src["name"] == current:
                        src["description"] = line.split(":", 1)[1].strip()
        return sources

    def probe(self, name: str, seconds: float = 1.2) -> float:
        """Record ``seconds`` from a source and return its average signal RMS.

        A suspended PipeWire/PulseAudio source wakes up silently, so the
        first ~250ms are discarded to avoid underestimating the signal.
        """
        proc = self._open_recorder(name)
        total, count = 0.0, 0
        try:
            warm_deadline = time.monotonic() + 0.25
            while time.monotonic() < warm_deadline:
                if not proc.stdout.read(3200):
                    break
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                frame = proc.stdout.read(3200)
                if not frame:
                    break
                total += _rms(frame, block_dc=True)
                count += 1
        finally:
            proc.kill()
            proc.wait(timeout=5)
        return total / count if count else 0.0

    def auto_select_mic(self, floor: float = 25.0) -> tuple[str | None, float]:
        """Pick the capture source with the strongest signal (the one that hears you)."""
        best_name, best_rms = None, 0.0
        for src in self.list_sources():
            try:
                rms = self.probe(src["name"])
            except (OSError, subprocess.SubprocessError):
                continue
            if rms > best_rms:
                best_name, best_rms = src["name"], rms
        if best_name and best_rms >= floor:
            return best_name, best_rms
        return None, 0.0

    def _ensure_mic(self) -> None:
        with self._mic_lock:
            if self.mic:
                return
            if self.settings.stt_device != "default":
                rms = self.probe(self.settings.stt_device)
                if rms >= 0.2:
                    self.mic = self.settings.stt_device
                    return
                sys.stderr.write(
                    f"[voice] mic '{self.settings.stt_device}' has no usable signal "
                    f"(rms {rms:.1f}); picking a live mic instead.\n"
                )
            selected, rms = self.auto_select_mic()
            if selected:
                self.mic = selected
                sys.stderr.write(f"[voice] listening on mic: {selected} (rms {rms:.0f})\n")
            else:
                sys.stderr.write("[voice] no usable mic found; using 'default'\n")
                self.mic = None

    def set_mic(self, name: str | None) -> None:
        with self._mic_lock:
            self.mic = name

    def _open_recorder(self, device: str | None) -> subprocess.Popen:
        if shutil.which("parec"):
            cmd = [
                "parec",
                "--device=" + (device or "default"),
                "--format=s16le",
                "--rate=16000",
                "--channels=1",
                "--raw",
            ]
        else:
            cmd = ["arecord", "-q", "-D", device or "default", "-f", "S16_LE", "-r", "16000", "-c", "1"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE)

    # -- text-to-speech ---------------------------------------------------

    def speak(self, text: str) -> None:
        """Synthesize and play text in a background thread (never blocks the REPL)."""
        text = " ".join(text.split())
        if not text:
            return
        if self._speak_thread and self._speak_thread.is_alive():
            return
        self._speak_thread = threading.Thread(target=self._speak, args=(text,), daemon=True)
        self._speak_thread.start()

    def _speak(self, text: str) -> None:
        with self._speak_lock:
            if self.settings.tts_engine == "xtts":
                self._speak_xtts(text)
            else:
                self._speak_synth(text)

    def _speak_synth(self, text: str) -> None:
        wav = _TMP / f"orion_speech_{os.getpid()}_{threading.get_ident()}.wav"
        try:
            data = self.synthesize(text)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            sys.stderr.write(f"[voice] TTS failed: {exc}\n")
            return
        try:
            wav.write_bytes(data)
            subprocess.run(["paplay", str(wav)], capture_output=True, timeout=120, check=True)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            sys.stderr.write(f"[voice] playback failed: {exc}\n")
        finally:
            wav.unlink(missing_ok=True)

    def synthesize(self, text: str) -> bytes:
        """Synthesize ``text`` to WAV bytes without playing them."""
        text = " ".join(text.split())
        if not text:
            return b""
        if self.settings.tts_engine == "kokoro":
            return self._synthesize_kokoro(text)
        wav = _TMP / f"orion_synth_{os.getpid()}_{threading.get_ident()}.wav"
        try:
            subprocess.run(
                [
                    self._piper_bin(),
                    "--model",
                    self._tts_model,
                    "--output_file",
                    str(wav),
                    "--sentence_silence",
                    "0.4",
                    "--length-scale",
                    str(self.settings.tts_length_scale),
                ],
                input=text,
                text=True,
                capture_output=True,
                timeout=60,
                check=True,
            )
            self._humanize(wav)
            return wav.read_bytes()
        finally:
            wav.unlink(missing_ok=True)

    def _synthesize_kokoro(self, text: str) -> bytes:
        """Synthesize with Kokoro-82M (kokoro-onnx) and return WAV bytes."""
        import numpy as np  # noqa: PLC0415 - lazy, heavy import

        kokoro = self._kokoro_model()
        samples, sample_rate = kokoro.create(
            text,
            voice=self.settings.tts_kokoro_voice,
            speed=self.settings.tts_kokoro_speed,
        )
        pcm = (samples * 32767.0).astype(np.int16)
        pcm = np.clip(pcm, -32768, 32767)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm.tobytes())
        return buffer.getvalue()

    def _kokoro_model(self):
        """Lazily load the Kokoro ONNX model (loaded once, reused for all speech).

        Prefers the CUDA provider when ``tts_device`` is ``cuda``; kokoro-onnx
        defaults to CPU (it probes for a package named ``onnxruntime-gpu`` which
        never exists), so we force the provider via the ``ONNX_PROVIDER``
        override and preload the NVIDIA wheel libraries directly.
        """
        if self._kokoro is None:
            with self._kokoro_lock:
                if self._kokoro is None:
                    from kokoro_onnx import Kokoro  # noqa: PLC0415 - lazy, heavy import

                    self._prepare_gpu_env()
                    model, voices = self._kokoro_paths()
                    try:
                        self._kokoro = Kokoro(str(model), str(voices))
                    except Exception as exc:  # noqa: BLE001 - GPU init may fail; fall back to CPU
                        os.environ.pop("ONNX_PROVIDER", None)
                        sys.stderr.write(f"[voice] GPU unavailable ({exc}); using CPU.\n")
                        self._kokoro = Kokoro(str(model), str(voices))
        return self._kokoro

    def _prepare_gpu_env(self) -> None:
        """Configure CUDA inference when the user asked for it (``tts_device=cuda``).

        LD_LIBRARY_PATH set at runtime is ignored (glibc caches it at process
        start), so we dlopen the NVIDIA wheel libraries with absolute paths and
        RTLD_GLOBAL — the CUDA provider then resolves them by soname.
        """
        if self.settings.tts_device != "cuda":
            return
        if os.environ.get("ONNX_PROVIDER"):
            return
        import ctypes  # noqa: PLC0415

        nvidia_root = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
        paths: list[Path] = []
        cu13 = nvidia_root / "cu13" / "lib"
        if cu13.exists():
            paths += sorted(cu13.glob("lib*.so.*"))
        cudnn = nvidia_root / "cudnn" / "lib"
        if cudnn.exists():
            paths += sorted(cudnn.glob("libcudnn*.so.9"))
        for lib in paths:
            try:
                ctypes.CDLL(str(lib), mode=os.RTLD_GLOBAL)
            except OSError:
                continue
        os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"

    def _speak_xtts(self, text: str) -> None:
        """Clone the reference voice with XTTS-v2, then apply the human finish."""
        if not self._xtts:
            sys.stderr.write("[voice] synthesizing…\n")
        self._wait_tts_ready()
        wav = _TMP / f"orion_speech_{os.getpid()}_{threading.get_ident()}.wav"
        try:
            model = self._xtts_model()
            model.tts_to_file(
                text=text,
                speaker_wav=self.settings.tts_reference,
                language=self.settings.tts_language,
                file_path=str(wav),
            )
            self._humanize(wav)
            subprocess.run(["paplay", str(wav)], capture_output=True, timeout=120, check=True)
        except Exception as exc:  # noqa: BLE001 - cloning/synthesis errors shouldn't kill the REPL
            sys.stderr.write(f"[voice] XTTS failed: {exc}\n")
        finally:
            wav.unlink(missing_ok=True)

    def _xtts_model(self):
        if self._xtts is None:
            from TTS.api import TTS  # noqa: PLC0415 - lazy, heavy import

            self._xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(
                self.settings.tts_device
            )
        return self._xtts

    def _preload_tts(self) -> None:
        """Load the TTS engine in the background at startup so speech isn't delayed."""
        if self.settings.tts_engine == "xtts":
            self._preload_thread = threading.Thread(
                target=self._warmup_xtts, daemon=True, name="xtts-preload"
            )
            self._preload_thread.start()
        elif self.settings.tts_engine == "kokoro":
            self._preload_thread = threading.Thread(
                target=self._warmup_kokoro, daemon=True, name="kokoro-preload"
            )
            self._preload_thread.start()

    def _warmup_kokoro(self) -> None:
        try:
            self._kokoro_model()
        except Exception as exc:  # noqa: BLE001 - preload is best-effort
            sys.stderr.write(f"[voice] Kokoro warmup failed: {exc}\n")

    def _warmup_xtts(self) -> None:
        try:
            model = self._xtts_model()
            model.tts_to_file(
                text="Ready.",
                speaker_wav=self.settings.tts_reference,
                language=self.settings.tts_language,
                file_path=str(_TMP / f"orion_warmup_{os.getpid()}.wav"),
            )
            (_TMP / f"orion_warmup_{os.getpid()}.wav").unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - preload is best-effort
            sys.stderr.write(f"[voice] XTTS warmup failed: {exc}\n")

    def _wait_tts_ready(self) -> None:
        """Block until the background TTS preload finishes (max a reasonable wait)."""
        thread = getattr(self, "_preload_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=120)

    def wait(self) -> None:
        """Block until current speech finishes (so it doesn't leak into the mic)."""
        if self._speak_thread:
            self._speak_thread.join(timeout=30)

    # -- speech-to-text -----------------------------------------------------

    def listen(self, timeout: int | None = None) -> str:
        """Record from the selected mic until you stop speaking, then transcribe.

        Uses an energy-based voice-activity detector on the raw PCM stream:
        it starts capturing when speech begins and stops ~1.2s after you go
        quiet (or after ``timeout`` seconds), then runs faster-whisper.
        Returns the transcribed text (possibly empty).
        """
        self.wait()
        self._ensure_mic()
        timeout = timeout or self.settings.voice_timeout
        with self._record_lock:
            frames = self._record_until_silence(timeout)
            if not frames:
                return ""
            wav = _TMP / f"orion_mic_{os.getpid()}.wav"
            try:
                _write_wav(frames, wav)
                segments, _info = self._stt().transcribe(str(wav), beam_size=5, vad_filter=True)
                return " ".join(segment.text.strip() for segment in segments).strip()
            finally:
                wav.unlink(missing_ok=True)

    def _record_until_silence(self, timeout: int) -> list[bytes]:
        """Stream 100ms PCM frames; capture from voice onset to trailing silence.

        The VAD threshold is derived from an adaptive noise floor instead of
        a fixed high bar: we warm the source up (a suspended input starts
        silent), take the median of the next frames as the resting level,
        and only treat frames that clearly exceed it as speech. Quiet mics
        (e.g. a low-output wired/wireless boom headset) therefore trigger
        while background noise still does not.
        """
        proc = self._open_recorder(self.mic)
        frame_size = 3200  # 100ms @ 16kHz mono 16-bit
        captured: list[bytes] = []
        lead: list[bytes] = []  # rolling buffer of pre-voice audio so onset isn't clipped
        started = False
        speech_hits = 0
        silence_frames = 0
        total = 0
        max_frames = timeout * 10
        absolute_floor = self.settings.stt_vad_floor
        try:
            # Discard the silent wake-up frames of a SUSPENDED source.
            for _ in range(3):
                if not proc.stdout.read(frame_size):
                    return captured
            # Establish the resting noise level from the next frames.
            baseline: list[float] = []
            for _ in range(5):
                frame = proc.stdout.read(frame_size)
                if not frame:
                    break
                baseline.append(_rms(frame, block_dc=True))
                lead.append(frame)
                total += 1
            if not baseline:
                baseline = [absolute_floor]
            noise_floor = sorted(baseline)[len(baseline) // 2]
            floor = max(noise_floor * 2.5, absolute_floor)

            while True:
                frame = proc.stdout.read(frame_size)
                if not frame:
                    break
                total += 1
                rms = _rms(frame, block_dc=True)
                voiced = rms > floor

                if not started:
                    lead.append(frame)
                    if len(lead) > 10:
                        lead.pop(0)
                    if voiced:
                        speech_hits += 1
                        if speech_hits >= 2:
                            started = True
                            captured.extend(lead)
                            lead.clear()
                    else:
                        speech_hits = 0
                        noise_floor = noise_floor * 0.9 + rms * 0.1
                        floor = max(noise_floor * 2.5, absolute_floor)
                else:
                    captured.append(frame)
                    if voiced:
                        silence_frames = 0
                    else:
                        silence_frames += 1
                        noise_floor = noise_floor * 0.9 + rms * 0.1
                        floor = max(noise_floor * 2.5, absolute_floor)
                    if silence_frames >= 12 or len(captured) >= max_frames:
                        break

                if (not started and total > max_frames) or \
                        (started and len(captured) >= max_frames):
                    break
        finally:
            proc.kill()
            proc.wait(timeout=5)
        return captured

    def _stt(self):
        if self._stt_model is None:
            from faster_whisper import WhisperModel  # noqa: PLC0415 - lazy, heavy import

            self._stt_model = WhisperModel(
                self.settings.stt_model,
                device="cpu",
                compute_type="int8",
            )
        return self._stt_model


def _write_wav(frames: list[bytes], path: Path) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"".join(frames))


def _rms(frame: bytes, block_dc: bool = False) -> float:
    """Root-mean-square amplitude of an int16 PCM frame.

    With ``block_dc`` the per-frame mean is subtracted first, so a constant
    DC offset / low hum doesn't inflate the level and defeat voice detection.
    """
    if len(frame) < 2:
        return 0.0
    samples = struct.unpack(f"<{len(frame) // 2}h", frame)
    n = len(samples)
    if not block_dc:
        return math.sqrt(sum(s * s for s in samples) / n)
    mean = sum(samples) / n
    return math.sqrt(sum((s - mean) ** 2 for s in samples) / n)
