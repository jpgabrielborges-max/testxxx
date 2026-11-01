"""Narração local. Backends: Kokoro (padrão, ONNX, CPU, vozes pt-BR), Piper e Chatterbox (opcionais)."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf

KOKORO_PT_VOICES = {
    "pf_dora": "Feminina, pt-BR (padrão)",
    "pm_alex": "Masculina, pt-BR",
    "pm_santa": "Masculina, pt-BR, grave",
}


class TTSError(RuntimeError):
    pass


class Backend(Protocol):
    sr: int

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray: ...


def trim_silence(a: np.ndarray, sr: int, thr: float = 0.01, lead: float = 0.03, tail: float = 0.09) -> np.ndarray:
    nz = np.where(np.abs(a) > thr)[0]
    if len(nz) == 0:
        return a
    return a[max(0, nz[0] - int(lead * sr)): nz[-1] + int(tail * sr)]


class KokoroBackend:
    sr = 24000

    def __init__(self, model: Path, voices: Path, lang: str = "pt-br"):
        if not Path(model).exists() or not Path(voices).exists():
            raise TTSError(f"Modelos do Kokoro não encontrados em {model} / {voices}. Rode o setup.ps1 (baixa ~350 MB).")
        from kokoro_onnx import Kokoro

        self._k = Kokoro(str(model), str(voices))
        self.lang = lang

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray:
        audio, sr = self._k.create(text, voice=voice, speed=speed, lang=self.lang)
        self.sr = sr
        return np.asarray(audio, dtype=np.float32)


class PiperBackend:
    sr = 22050

    def __init__(self, executable: str, model: Path):
        if not Path(model).exists():
            raise TTSError(f"Modelo do Piper não encontrado: {model}")
        self.exe, self.model = executable, str(model)

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "o.wav"
            cmd = [self.exe, "--model", self.model, "--output_file", str(out), "--length_scale", f"{1.0 / max(speed, 0.1):.3f}"]
            try:
                p = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=300)
            except FileNotFoundError as exc:
                raise TTSError(f"Piper não encontrado ('{self.exe}'). Ajuste tts.piper.executable no config.yaml.") from exc
            if p.returncode != 0 or not out.exists():
                raise TTSError(f"Piper falhou: {p.stderr.decode(errors='replace')[:400]}")
            a, sr = sf.read(out, dtype="float32")
            self.sr = sr
            return a if a.ndim == 1 else a.mean(axis=1)


class ChatterboxBackend:
    """Roda scripts/chatterbox_worker.py no Python do ambiente SEPARADO do Chatterbox (evita conflito de dependências)."""

    sr = 24000

    def __init__(self, python: str, worker: Path, reference_wav: str = ""):
        if not python or not Path(python).exists():
            raise TTSError("tts.chatterbox.python não aponta para um Python existente. Veja a seção Chatterbox no README.")
        self.python, self.worker, self.ref = python, str(worker), reference_wav

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "o.wav"
            cmd = [self.python, self.worker, "--text", text, "--out", str(out), "--lang", "pt"]
            if self.ref:
                cmd += ["--ref", self.ref]
            p = subprocess.run(cmd, capture_output=True, timeout=900)
            if p.returncode != 0 or not out.exists():
                raise TTSError(f"Chatterbox falhou: {p.stderr.decode(errors='replace')[-600:]}")
            a, sr = sf.read(out, dtype="float32")
            self.sr = sr
            a = a if a.ndim == 1 else a.mean(axis=1)
        if abs(speed - 1.0) > 0.01:  # ajuste de velocidade simples por reamostragem de tempo via ffmpeg atempo
            a = _atempo(a, sr, speed)
        return a


def _atempo(a: np.ndarray, sr: int, speed: float) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-filter:a", f"atempo={speed:.3f}", "-f", "f32le", "-"],
                       input=a.astype("<f4").tobytes(), capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype="<f4").copy()


def make_backend(cfg) -> Backend:
    t = cfg.section("tts")
    name = t.get("backend", "kokoro")
    if name == "kokoro":
        k = t.get("kokoro", {})
        return KokoroBackend(cfg.root / k.get("model", "models/kokoro/kokoro-v1.0.onnx"),
                             cfg.root / k.get("voices", "models/kokoro/voices-v1.0.bin"), k.get("lang", "pt-br"))
    if name == "piper":
        p = t.get("piper", {})
        return PiperBackend(p.get("executable", "piper"), cfg.root / p.get("model", ""))
    if name == "chatterbox":
        c = t.get("chatterbox", {})
        return ChatterboxBackend(c.get("python", ""), cfg.root / "scripts" / "chatterbox_worker.py", c.get("reference_wav", ""))
    raise TTSError(f"Backend de TTS desconhecido: {name}")


def synth_to_wav(backend: Backend, text: str, voice: str, speed: float, out: Path) -> float:
    """Sintetiza, remove silêncio nas pontas, grava WAV 48 kHz mono e devolve a duração (s) da fala."""
    a = trim_silence(backend.synth(text, voice, speed), backend.sr)
    if len(a) < backend.sr * 0.2:
        raise TTSError(f"A TTS devolveu áudio quase vazio para: {text[:60]!r}")
    peak = float(np.abs(a).max()) or 1.0
    a = a / peak * 0.89
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(backend.sr), "-ac", "1", "-i", "-",
                        "-ar", "48000", "-ac", "1", str(out)], input=(a * 32767).astype("<i2").tobytes(), capture_output=True)
    if p.returncode != 0:
        raise TTSError(f"ffmpeg falhou ao gravar {out.name}: {p.stderr.decode(errors='replace')[:300]}")
    return len(a) / backend.sr
