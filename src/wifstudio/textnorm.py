"""Normaliza texto narrado para pt-BR: números, porcentagens e unidades viram palavras (a TTS lê melhor e
as legendas ficam idênticas ao que é dito)."""
from __future__ import annotations

import re

from num2words import num2words

_UNITS = [
    (r"km/h", " quilômetros por hora"),
    (r"m/s", " metros por segundo"),
    (r"°C", " graus Celsius"),
    (r"km", " quilômetros"),
    (r"h(?![A-Za-zÀ-ÿ])", " horas"),
    (r"min(?![A-Za-zÀ-ÿ])", " minutos"),
    (r"kg", " quilos"),
    (r"%", " por cento"),
]


def _int_words(s: str) -> str:
    return num2words(int(s), lang="pt_BR").replace(",", "")  # vírgula do num2words viraria pausa na fala


def _num_sub(m: re.Match) -> str:
    raw = m.group(0)
    if "," in raw:
        whole, dec = raw.split(",", 1)
        return f"{_int_words(whole.replace('.', ''))} vírgula {' '.join(_int_words(d) for d in dec)}"
    return _int_words(raw.replace(".", ""))


_ORD = {"º": {"1": "primeiro", "2": "segundo", "3": "terceiro"}, "ª": {"1": "primeira", "2": "segunda", "3": "terceira"}}


def _ordinal(m: re.Match) -> str:
    n, mark = m.group(1), m.group(2)
    return _ORD[mark].get(n) or (_int_words(n) + (" " + ("avo" if mark == "º" else "ava")))  # simplificação: só 1 a 3 são exatos


def _money(m: re.Match) -> str:
    whole = m.group(1).replace(".", "")
    cents = m.group(2)
    out = f"{_int_words(whole)} {'real' if whole == '1' else 'reais'}"
    if cents and cents != "00":
        out += f" e {_int_words(cents)} centavos"
    return out


def normalize_pt(text: str) -> str:
    """Números, moeda, ordinais, porcentagem e unidades viram palavras. Só para a FALA: o texto do roteiro do usuário
    nunca é reescrito por aqui (a legenda mostra o original; ver scripts.py)."""
    out = text
    out = re.sub(r"R\$\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{2}))?", _money, out)
    out = re.sub(r"\b(\d+)\s?([ºª])", _ordinal, out)
    out = out.replace("&", " e ")
    for pat, rep in _UNITS:
        out = re.sub(rf"(?<=\d)\s?{pat}", rep, out)
    out = re.sub(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?", _num_sub, out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def count_words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if re.search(r"\w", w)])


# Unidades de legenda: palavras do texto ORIGINAL (como o usuário escreveu) emparelhadas com o que a voz fala.
_UNIT = re.compile(
    r"R\$\s?[\d.,]+|\d[\d.,]*\s?(?:km/h|m/s|°C|%|km|kg|º|ª)|\d+\s?(?:h|min)(?![A-Za-zÀ-ÿ])|\S+"
)


def display_units(text: str) -> list[tuple[str, str]]:
    """[(como aparece na legenda, como é falado)]. O texto exibido é sempre o original do roteiro."""
    out = []
    for m in _UNIT.finditer(text):
        shown = m.group(0)
        spoken = normalize_pt(shown)
        if re.search(r"\w", spoken):
            out.append((shown, spoken))
    return out


def norm_words(text: str) -> list[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", t).split()


def wer(ref: str, hyp: str) -> float:
    """Taxa de erro de palavras (0 = idêntico). O hipótese passa por normalize_pt para que '5' e 'cinco' contem como iguais."""
    r, h = norm_words(ref), norm_words(normalize_pt(hyp))
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[-1] / max(1, len(r))
