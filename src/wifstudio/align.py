"""Tempo de cada palavra. Whisper (faster-whisper, multilíngue) alinhado às palavras do ROTEIRO, com a técnica da skill
de referência (SequenceMatcher entre o texto do roteiro e o que o Whisper ouviu). Sem Whisper, cai para distribuição
proporcional DENTRO da fala da cena, cuja duração é exata porque vem da própria TTS."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

_norm = lambda w: re.sub(r"[^a-z0-9]+", "", __import__("unicodedata").normalize("NFKD", w.lower()).encode("ascii", "ignore").decode())  # noqa: E731


def script_words(text: str) -> list[str]:
    return [w for w in text.split() if _norm(w)]


def proportional(words: list[str], speech_start: float, speech_end: float) -> list[dict]:
    weights = [max(1, len(_norm(w))) + 1 for w in words]  # +1: custo fixo de cada palavra
    total = sum(weights)
    out, c = [], 0
    for w, wt in zip(words, weights):
        s = speech_start + (speech_end - speech_start) * c / total
        c += wt
        e = speech_start + (speech_end - speech_start) * c / total
        out.append({"w": w, "s": round(s, 3), "e": round(e, 3)})
    return out


def merge_with_heard(words: list[str], heard: list[dict], duration: float) -> list[dict]:
    """Mantém a grafia do roteiro com os tempos que o Whisper ouviu. Palavras não ouvidas dividem o intervalo vizinho."""
    A = [_norm(w) for w in words]
    B = [_norm(h["w"]) for h in heard]
    times: list[Optional[tuple[float, float]]] = [None] * len(words)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, A, B, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (heard[j1 + k]["s"], heard[j1 + k]["e"])
        elif tag == "replace":
            t0, t1 = heard[j1]["s"], heard[j2 - 1]["e"]
            L = [max(1, len(A[i])) for i in range(i1, i2)]
            tot, c = sum(L), 0
            for k, n in enumerate(L):
                times[i1 + k] = (round(t0 + (t1 - t0) * c / tot, 3), round(t0 + (t1 - t0) * (c + n) / tot, 3))
                c += n
    i = 0
    while i < len(words):
        if times[i] is None:
            j = i
            while j < len(words) and times[j] is None:
                j += 1
            t0 = times[i - 1][1] if i > 0 else 0.0
            t1 = times[j][0] if j < len(words) else duration
            for k in range(j - i):
                times[i + k] = (round(t0 + (t1 - t0) * k / (j - i), 3), round(t0 + (t1 - t0) * (k + 1) / (j - i), 3))
            i = j
        else:
            i += 1
    return [{"w": w, "s": s, "e": e} for w, (s, e) in zip(words, times)]  # type: ignore[misc]


class Aligner:
    def __init__(self, backend: str = "auto", model: str = "small", device: str = "auto", compute_type: str = "auto"):
        self.requested = backend
        self.model_name, self.device, self.compute = model, device, compute_type
        self._model = None
        self.active = "proportional"
        self.note = ""
        if backend in ("auto", "whisper"):
            try:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(model, device=device, compute_type=compute_type)
                self.active = "whisper"
            except Exception as exc:  # noqa: BLE001 - modelo ausente, sem internet, etc.
                if backend == "whisper":
                    raise
                self.note = f"Whisper indisponível ({type(exc).__name__}); usando alinhamento proporcional."

    @classmethod
    def from_config(cls, cfg) -> "Aligner":
        a = cfg.section("align")
        return cls(a.get("backend", "auto"), a.get("whisper_model", "small"), a.get("device", "auto"), a.get("compute_type", "auto"))

    def align_units(self, wav: Path, units: list[tuple[str, str]], speech_duration: float, lead: float = 0.03) -> list[dict]:
        """units = [(texto exibido, texto falado)]. Os tempos vêm da fala; o texto devolvido é o EXIBIDO (original do usuário)."""
        spoken_words: list[str] = []
        owner: list[int] = []
        for i, (_, sp) in enumerate(units):
            for w in sp.split():
                spoken_words.append(w)
                owner.append(i)
        timed = self._align_words(wav, spoken_words, speech_duration, lead)
        out = []
        for i, (shown, _) in enumerate(units):
            ts = [t for t, o in zip(timed, owner) if o == i]
            if ts:
                out.append({"w": shown, "s": ts[0]["s"], "e": ts[-1]["e"]})
        return out

    def align(self, wav: Path, text: str, speech_duration: float, lead: float = 0.03) -> list[dict]:
        return self._align_words(wav, script_words(text), speech_duration, lead)

    def _align_words(self, wav: Path, words: list[str], speech_duration: float, lead: float = 0.03) -> list[dict]:
        if not words:
            return []
        if self._model is not None:
            segs, _ = self._model.transcribe(str(wav), language="pt", word_timestamps=True, beam_size=5,
                                             vad_filter=False, condition_on_previous_text=False)
            heard = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                     for s in segs for w in (s.words or []) if _norm(w.word)]
            if heard:
                return merge_with_heard(words, heard, speech_duration + lead)
        return proportional(words, lead, lead + speech_duration)
