"""Efeitos sonoros e trilha, tudo local e gratuito. Procedurais (numpy/scipy) por padrão; se existir um arquivo em
assets/sfx/<nome>*.wav|mp3|ogg|flac (ex.: sons CC0 que VOCÊ baixou), ele é usado no lugar do som sintético.
Sons suaves de propósito (sem cliques ou tiques que machucam em alto-falante de celular)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

SR = 48000


def _lp(x, f, o=2):
    b, a = signal.butter(o, f / (SR / 2), "low")
    return signal.lfilter(b, a, x)


def _hp(x, f, o=2):
    b, a = signal.butter(o, f / (SR / 2), "high")
    return signal.lfilter(b, a, x)


def _bp(x, f0, f1, o=2):
    b, a = signal.butter(o, [f0 / (SR / 2), f1 / (SR / 2)], "band")
    return signal.lfilter(b, a, x)


def _fade(x, fi=0.02, fo=0.08):
    n = len(x)
    env = np.ones(n)
    a, b = int(fi * SR), int(fo * SR)
    if a and a < n:
        env[:a] = np.linspace(0, 1, a)
    if b and b < n:
        env[-b:] = np.minimum(env[-b:], np.linspace(1, 0, b))
    return x * env


def _norm(y, peak=0.8):
    return (y / (np.abs(y).max() + 1e-9) * peak).astype(np.float32)


def make_sfx(name: str, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = lambda d: np.arange(int(d * SR)) / SR  # noqa: E731
    if name == "whoosh":
        d = 0.9
        tt = t(d)
        y = _lp(_hp(rng.normal(0, 1, len(tt)), 400), 3500) * np.sin(np.pi * tt / d) ** 2
    elif name == "impact":
        d = 1.3
        tt = t(d)
        y = np.sin(2 * np.pi * np.cumsum(90 * np.exp(-tt * 5) + 35) / SR) * np.exp(-tt * 3.2)
        y += _lp(rng.normal(0, 1, len(tt)), 600) * np.exp(-tt * 9) * 0.5
    elif name == "boom":
        d = 2.6
        tt = t(d)
        y = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-tt * 2.2) + 28) / SR) * np.exp(-tt * 1.6)
        y += _lp(rng.normal(0, 1, len(tt)), 400, 3) * np.exp(-tt * 4) * 0.9
    elif name == "rumble":
        d = 2.2
        tt = t(d)
        y = _lp(rng.normal(0, 1, len(tt)), 140, 3) * (np.sin(np.pi * tt / d) ** 1.5) * 2.2
    elif name == "riser":
        d = 1.6
        tt = t(d)
        y = _hp(_lp(rng.normal(0, 1, len(tt)), 5000), 600) * (tt / d) ** 2
    elif name == "heartbeat":
        d, bpm = 3.2, 78
        tt = t(d)
        y = np.zeros(len(tt))
        period = 60 / bpm
        for k in range(int(d / period)):
            for off, g in ((0.0, 1.0), (0.22, 0.65)):  # "tum-tum"
                i = int((k * period + off) * SR)
                n = int(0.16 * SR)
                if i + n < len(y):
                    e = np.exp(-np.arange(n) / (0.035 * SR))
                    y[i:i + n] += g * np.sin(2 * np.pi * 52 * np.arange(n) / SR) * e
    elif name == "wind":
        d = 3.5
        tt = t(d)
        lfo = 0.6 + 0.4 * np.sin(2 * np.pi * 0.35 * tt + 1.3)
        y = _bp(rng.normal(0, 1, len(tt)), 180, 1400) * lfo * np.sin(np.pi * tt / d) ** 0.7
    elif name == "drone":
        d = 4.0
        tt = t(d)
        y = sum(np.sin(2 * np.pi * f * tt) * g for f, g in ((55, 1.0), (82.4, 0.6), (110, 0.4), (110.6, 0.3))) * np.sin(np.pi * tt / d) ** 1.4
    else:
        return np.zeros(1, np.float32)
    return _norm(_fade(y))


def _load_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").copy()


def get_sfx(name: str, sfx_dir: Path | None) -> np.ndarray:
    """Arquivo do usuário em assets/sfx tem prioridade sobre o som procedural."""
    if sfx_dir and Path(sfx_dir).exists():
        for ext in ("wav", "mp3", "ogg", "flac"):
            cands = sorted(Path(sfx_dir).glob(f"{name}*.{ext}"))
            if cands:
                return _norm(_fade(_load_audio(cands[0])), 0.9)
    return make_sfx(name)


_DARK = ("sombri", "terror", "mister", "tens", "suspens", "medo", "horror", "apocal", "sinistr", "angust")
_BRIGHT = ("diver", "aleg", "comed", "leve", "fofo", "otimis", "divert")


def make_bed(total_s: float, tone: str = "") -> np.ndarray:
    """Colchão harmônico grave e discreto conforme o tom (menor = sombrio, maior = leve, suspenso = neutro)."""
    tn = (tone or "").lower()
    if any(k in tn for k in _DARK):
        freqs = [(55.0, 1.0), (110.0, 0.7), (130.81, 0.45), (164.81, 0.4)]          # lá menor
    elif any(k in tn for k in _BRIGHT):
        freqs = [(65.41, 1.0), (130.81, 0.6), (164.81, 0.5), (196.0, 0.45)]          # dó maior
    else:
        freqs = [(73.42, 1.0), (146.83, 0.6), (220.0, 0.45), (329.63, 0.25)]         # ré suspenso
    n = int(total_s * SR) + SR
    tt = np.arange(n) / SR
    y = np.zeros(n)
    for f, g in freqs:
        for det in (-0.35, 0.0, 0.35):  # leve desafinação = timbre de pad
            y += g * np.sin(2 * np.pi * (f + det) * tt + det * 3)
    y *= 0.75 + 0.25 * np.sin(2 * np.pi * 0.07 * tt)
    y = _lp(y, 900)
    return _norm(_fade(y, 1.5, 2.0), 0.9)[: int(total_s * SR)]


def mix_episode(placements: list[dict], total_s: float, out_wav: Path, music: Path | None = None,
                music_volume: float = 0.10, sfx_volume: float = 0.35, sfx_dir: Path | None = None,
                bed_tone: str | None = None, bed_volume: float = 0.07) -> Path:
    """placements: [{start, duration, narration_wav, sfx}] em tempo de linha do tempo. Devolve out_wav (48 kHz estéreo).
    Trilha: music (arquivo do usuário) tem prioridade; senão, se bed_tone != None, gera o colchão harmônico procedural."""
    n = int(total_s * SR) + SR // 2
    voice = np.zeros(n, np.float32)
    fx = np.zeros(n, np.float32)
    for p in placements:
        a = _load_audio(Path(p["narration_wav"]))
        i = int(p["start"] * SR)
        voice[i:i + len(a)] += a[: max(0, n - i)]
        if p.get("sfx"):
            s = get_sfx(p["sfx"], sfx_dir)
            s = _fade(s[: int((p["duration"] + 0.6) * SR)], 0.02, 0.15)  # não vaza para a cena seguinte
            j = max(0, int((p["start"] + p["duration"]) * SR) - len(s)) if p["sfx"] == "riser" else i
            fx[j:j + len(s)] += s[: max(0, n - j)]
    mix = voice + fx * sfx_volume
    under = None
    if music and Path(music).exists():
        m = _load_audio(Path(music))
        under = np.tile(m, int(np.ceil(n / max(len(m), 1))))[:n] * music_volume
    elif bed_tone is not None:
        b = make_bed(total_s, bed_tone)
        under = np.zeros(n, np.float32)
        under[: len(b)] = b * bed_volume
    if under is not None:
        env = _lp(np.abs(voice), 8, 1)
        duck = 1.0 - 0.65 * np.clip(env / 0.12, 0, 1)  # trilha abaixa quando a voz fala
        mix = mix + _fade(under, 1.0, 1.5) * duck
    peak = float(np.abs(mix).max())
    if peak > 0.97:
        mix = mix / peak * 0.97
    stereo = np.stack([mix, mix], axis=1)[: int(total_s * SR)]
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_wav, stereo, SR, subtype="PCM_16")
    return out_wav
