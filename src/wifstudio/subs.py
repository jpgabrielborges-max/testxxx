"""Legendas: ASS com destaque palavra a palavra (burn-in) e SRT por bloco."""
from __future__ import annotations

import re
from pathlib import Path

_END_PUNCT = re.compile(r"[.!?…:;,]$")


def _ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ts_ass(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ts_srt(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def chunk_words(words: list[dict], max_words: int = 4) -> list[list[dict]]:
    """Agrupa palavras em blocos curtos, quebrando também na pontuação."""
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or _END_PUNCT.search(w["w"]):
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and len(cur) == 1 and len(chunks[-1]) < max_words:
            chunks[-1].extend(cur)  # evita bloco de uma palavra só
        else:
            chunks.append(cur)
    return chunks


def _break_lines(texts: list[str], max_chars: int) -> str:
    line = " ".join(texts)
    if len(line) <= max_chars or len(texts) < 2:
        return line
    best, best_diff = 1, 10**9
    for i in range(1, len(texts)):
        diff = abs(len(" ".join(texts[:i])) - len(" ".join(texts[i:])))
        if diff < best_diff:
            best, best_diff = i, diff
    return " ".join(texts[:best]) + r"\N" + " ".join(texts[best:])


def timeline_words(scenes_words: list[tuple[float, list[dict]]]) -> list[dict]:
    """[(início_da_cena, palavras_da_cena)] -> palavras com tempos absolutos."""
    out = []
    for start, words in scenes_words:
        for w in words:
            out.append({"w": w["w"], "s": start + w["s"], "e": start + w["e"]})
    return out


def build_chunks(words: list[dict], max_words: int, max_chars: int, uppercase: bool, total: float) -> list[dict]:
    chunks = chunk_words(words, max_words)
    events = []
    for i, ch in enumerate(chunks):
        start = ch[0]["s"]
        nxt = chunks[i + 1][0]["s"] if i + 1 < len(chunks) else None
        end = ch[-1]["e"] + 0.12
        if nxt is not None:
            end = min(end, nxt - 0.01) if nxt - ch[-1]["e"] < 0.4 else end
            end = max(end, ch[-1]["e"])
        end = min(max(end, start + 0.25), total)
        texts = [(w["w"].upper() if uppercase else w["w"]) for w in ch]
        events.append({"start": start, "end": end, "words": ch, "text": _break_lines(texts, max_chars)})
    return events


def write_srt(events: list[dict], path: Path) -> None:
    lines = []
    for i, ev in enumerate(events, start=1):
        lines += [str(i), f"{_ts_srt(ev['start'])} --> {_ts_srt(ev['end'])}", ev["text"].replace(r"\N", "\n"), ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def write_ass(events: list[dict], path: Path, style: dict, width: int, height: int, uppercase: bool = False) -> None:
    bold = -1 if style.get("bold", True) else 0
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.get('font', 'Arial')},{style.get('font_size', 78)},{_ass_color(style.get('primary_color', '#FFD60A'))},{_ass_color(style.get('secondary_color', '#FFFFFF'))},{_ass_color(style.get('outline_color', '#000000'))},&H64000000,{bold},0,0,0,100,100,0,0,1,{style.get('outline', 5)},{style.get('shadow', 2)},2,60,60,{style.get('margin_bottom', 380)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for ev in events:
        parts = []
        ws = ev["words"]
        for j, w in enumerate(ws):
            nxt = ws[j + 1]["s"] if j + 1 < len(ws) else ev["end"]
            dur_cs = max(1, int(round((max(nxt, w["e"]) - w["s"]) * 100)))
            word = (w["w"].upper() if uppercase else w["w"]).replace("{", "").replace("}", "")
            parts.append(f"{{\\k{dur_cs}}}{word}")
        text = " ".join(parts)
        # reaplica a quebra de linha calculada
        if r"\N" in ev["text"]:
            n_first = len(ev["text"].split(r"\N")[0].split())
            toks = text.split(" ")
            text = " ".join(toks[:n_first]) + r"\N" + " ".join(toks[n_first:])
        rows.append(f"Dialogue: 0,{_ts_ass(ev['start'])},{_ts_ass(ev['end'])},Default,,0,0,0,,{text}")  # 10 campos = Format acima
    Path(path).write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
