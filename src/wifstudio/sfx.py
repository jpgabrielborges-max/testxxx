"""Efeitos sonoros procedurais (numpy/scipy) e mixagem do episódio: narração + SFX + trilha opcional com ducking.
Sons suaves de propósito (sem cliques ou tiques que machucam em alto-falante de celular)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

SR = 48000
_rng = np.random.default_rng(7)


def _lp(x, f, o=2):
    b, a = signal.butter(o, f / (SR / 2), "low")
    return signal.lfilter(b, a, x)


def _hp(x, f, o=2):
    b, a = signal.butter(o, f / (SR / 2), "high")
    return signal.lfilter(b, a, x)


def _fade(x, fi=0.02, fo=0.08):
    n = len(x)
    env = np.ones(n)
    a, b = int(fi * SR), int(fo * SR)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if b:
        env[-b:] = np.minimum(env[-b:], np.linspace(1, 0, b))
    return x * env


def make_sfx(name: str) -> np.ndarray:
    t = lambda d: np.arange(int(d * SR)) / SR  # noqa: E731
    if name == "whoosh":
        d = 0.9
        n = _rng.normal(0, 1, len(t(d)))
        tt = t(d)
        env = np.sin(np.pi * tt / d) ** 2
        y = _lp(_hp(n, 400), 3500 + 3000 * 0) * env
    elif name == "impact":
        d = 1.3
        tt = t(d)
        f = 90 * np.exp(-tt * 5) + 35
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 3.2)
        y += _lp(_rng.normal(0, 1, len(tt)), 600) * np.exp(-tt * 9) * 0.5
    elif name == "rumble":
        d = 2.2
        tt = t(d)
        y = _lp(_rng.normal(0, 1, len(tt)), 140, 3) * (np.sin(np.pi * tt / d) ** 1.5) * 2.2
    elif name == "riser":
        d = 1.6
        tt = t(d)
        n = _rng.normal(0, 1, len(tt))
        y = _hp(_lp(n, 5000), 600) * (tt / d) ** 2
    else:
        return np.zeros(1)
    y = _fade(y)
    return (y / (np.abs(y).max() + 1e-9) * 0.8).astype(np.float32)


def _load_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").copy()


def mix_episode(placements: list[dict], total_s: float, out_wav: Path, music: Path | None = None,
                music_volume: float = 0.10, sfx_volume: float = 0.35) -> Path:
    """placements: [{start, duration, narration_wav, sfx}] em tempo de linha do tempo. Devolve out_wav (48 kHz estéreo)."""
    n = int(total_s * SR) + SR // 2
    voice = np.zeros(n, np.float32)
    fx = np.zeros(n, np.float32)
    for p in placements:
        a = _load_audio(Path(p["narration_wav"]))
        i = int(p["start"] * SR)
        voice[i:i + len(a)] += a[: max(0, n - i)]
        if p.get("sfx"):
            s = make_sfx(p["sfx"])
            if p["sfx"] == "riser":  # termina junto com a cena
                j = max(0, int((p["start"] + p["duration"]) * SR) - len(s))
            else:
                j = i
            fx[j:j + len(s)] += s[: max(0, n - j)]
    mix = voice + fx * sfx_volume
    if music and Path(music).exists():
        m = _load_audio(Path(music))
        reps = int(np.ceil(n / max(len(m), 1)))
        m = np.tile(m, reps)[:n]
        env = _lp(np.abs(voice), 8, 1)
        duck = 1.0 - 0.65 * np.clip(env / 0.12, 0, 1)
        m = _fade(m, fi=1.0, fo=1.5) * duck * music_volume
        mix = mix + m
    peak = float(np.abs(mix).max())
    if peak > 0.97:
        mix = mix / peak * 0.97
    stereo = np.stack([mix, mix], axis=1)[: int(total_s * SR)]
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_wav, stereo, SR, subtype="PCM_16")
    return out_wav
