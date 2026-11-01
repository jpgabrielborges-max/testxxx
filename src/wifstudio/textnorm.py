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
    (r"kg", " quilos"),
    (r"%", " por cento"),
]


def _int_words(s: str) -> str:
    return num2words(int(s), lang="pt_BR")


def _num_sub(m: re.Match) -> str:
    raw = m.group(0)
    if "," in raw:
        whole, dec = raw.split(",", 1)
        return f"{_int_words(whole.replace('.', ''))} vírgula {' '.join(_int_words(d) for d in dec)}"
    return _int_words(raw.replace(".", ""))


def normalize_pt(text: str) -> str:
    out = text
    for pat, rep in _UNITS:
        out = re.sub(rf"(?<=\d)\s?{pat}", rep, out)
    out = re.sub(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?", _num_sub, out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def count_words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if re.search(r"\w", w)])
