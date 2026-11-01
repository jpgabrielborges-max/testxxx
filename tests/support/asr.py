"""Reconhecimento de fala OFFLINE (Whisper multilíngue via sherpa-onnx) usado para provar que a TTS fala português.
Só para testes: o aplicativo em si usa faster-whisper. O modelo vem de github.com/k2-fsa/sherpa-onnx (release asr-models)."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf


def norm_words(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", t).split()


def wer(ref: str, hyp: str) -> float:
    r, h = norm_words(ref), norm_words(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), int)
    d[:, 0] = range(len(r) + 1)
    d[0, :] = range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[-1, -1] / max(1, len(r))


class Asr:
    def __init__(self, model_dir: Path, name: str = "small", language: str = "pt"):
        import sherpa_onnx

        m = Path(model_dir)
        self.rec = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(m / f"{name}-encoder.int8.onnx"), decoder=str(m / f"{name}-decoder.int8.onnx"),
            tokens=str(m / f"{name}-tokens.txt"), language=language, task="transcribe", num_threads=4)

    def transcribe(self, wav: Path) -> str:
        a, sr = sf.read(wav, dtype="float32")
        if a.ndim > 1:
            a = a.mean(axis=1)
        s = self.rec.create_stream()
        s.accept_waveform(sr, a)
        self.rec.decode_stream(s)
        return s.result.text.strip()
