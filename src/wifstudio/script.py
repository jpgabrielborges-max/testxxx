"""Roteiro fornecido pelo usuário: divisão em episódios e cenas feita em CÓDIGO (determinística), nunca pelo LLM.
Garantia central: o texto narrado de cada cena é um trecho EXATO do que o usuário colou. O LLM só descreve o visual."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Marcadores de estrutura (não são narrados): "---", "===", "### Episódio 2: Título", "Parte 3", "Capítulo 1 - ..."
_SEP = re.compile(r"^\s*(?:-{3,}|={3,}|\*{3,}|_{3,})\s*$")
_HEAD = re.compile(r"^\s*(?:#{1,4}\s*)?(?:epis[oó]dio|parte|cap[ií]tulo|cena)\s*(\d+|[ivxIVX]+)\b\s*[:\-–.]?\s*(.*)$", re.I)
# Direção de cena inteira entre colchetes: [Câmera sobe sobre a cidade vazia]. Não é narrada, vira nota visual.
_DIRECTION = re.compile(r"^\s*\[(.+)\]\s*$")
_ABBREV = {"dr", "dra", "sr", "sra", "srta", "prof", "profa", "av", "etc", "ex", "nº", "no", "pág", "vs", "cap", "jr", "sto", "sta"}
_SENT_END = re.compile(r"(?<=[.!?…])[\"”')\]]*\s+(?=[\"“'(\[]*[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ0-9¿¡—–-])")


@dataclass
class Segment:
    text: str                                  # trecho EXATO do roteiro (sem alterações)
    notes: list[str] = field(default_factory=list)
    paragraph: int = 0


@dataclass
class EpisodeText:
    number: int
    title: str
    body: str                                  # texto narrado (sem marcadores), parágrafos separados por linha em branco
    segments: list[Segment] = field(default_factory=list)


def split_episodes(script: str) -> list[EpisodeText]:
    """Separa o roteiro em episódios pelos marcadores. Sem marcadores = 1 episódio. Texto de cada episódio fica intacto."""
    lines = script.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    eps: list[tuple[str, list[str]]] = []
    cur_title, cur = "", []

    def flush():
        nonlocal cur, cur_title
        if "".join(cur).strip():
            eps.append((cur_title, cur))
        cur, cur_title = [], ""

    for ln in lines:
        m = _HEAD.match(ln)
        if _SEP.match(ln):
            flush()
        elif m and not ln.lstrip().startswith("["):
            flush()
            cur_title = (m.group(2) or "").strip()
        else:
            cur.append(ln)
    flush()
    return [EpisodeText(i + 1, t, "\n".join(ls).strip("\n")) for i, (t, ls) in enumerate(eps)]


def _sentences(par: str) -> list[str]:
    """Divide um parágrafo em frases sem perder nem alterar nenhum caractere (mantém abreviações unidas)."""
    parts = _SENT_END.split(par.strip())
    out: list[str] = []
    for p in parts:
        if out:
            last_word = re.sub(r"[^\wº]", "", out[-1].split()[-1].lower()) if out[-1].split() else ""
            if last_word in _ABBREV and out[-1].rstrip().endswith("."):
                out[-1] = out[-1] + " " + p
                continue
        out.append(p)
    return [o.strip() for o in out if o.strip()]


def _split_long(sentence: str, max_words: int) -> list[str]:
    """Frase longa demais para uma cena: corta nas vírgulas/ponto e vírgula/dois-pontos mais próximos do meio."""
    words = sentence.split()
    if len(words) <= max_words:
        return [sentence]
    cuts = [i + 1 for i, w in enumerate(words[:-1]) if re.search(r"[,;:–—]$", w)]
    if not cuts:  # sem pontuação interna: corta no meio mesmo (mantém todas as palavras)
        cuts = [len(words) // 2]
    mid = len(words) / 2
    c = min(cuts, key=lambda i: abs(i - mid))
    left, right = " ".join(words[:c]), " ".join(words[c:])
    return _split_long(left, max_words) + _split_long(right, max_words)


def segment_text(body: str, wps: float = 2.6, min_s: float = 3.0, max_s: float = 7.5) -> list[Segment]:
    """Agrupa frases consecutivas em cenas de min_s a max_s segundos de fala. Respeita parágrafos (uma cena não atravessa
    um parágrafo, a menos que o parágrafo seja curto demais). Direções [entre colchetes] viram notas da PRÓXIMA cena."""
    min_w, max_w = max(3, int(min_s * wps)), max(6, int(max_s * wps))
    segs: list[Segment] = []
    pending_notes: list[str] = []
    paragraphs = re.split(r"\n\s*\n", body)
    for pi, par in enumerate(paragraphs):
        text_lines = []
        for ln in par.split("\n"):
            d = _DIRECTION.match(ln)
            if d:
                pending_notes.append(d.group(1).strip())
            elif ln.strip():
                text_lines.append(ln.strip())
        flat = " ".join(text_lines)
        if not flat:
            continue
        units: list[str] = []
        for s in _sentences(flat):
            units += _split_long(s, max_w)
        buf: list[str] = []
        for u in units:
            n_buf = sum(len(x.split()) for x in buf)
            if buf and n_buf + len(u.split()) > max_w:
                segs.append(Segment(" ".join(buf), pending_notes, pi))
                pending_notes, buf = [], []
            buf.append(u)
        if buf:
            n_buf = sum(len(x.split()) for x in buf)
            # parágrafo curto: junta com o anterior se ainda couber, em vez de criar cena de 1-2 segundos
            if n_buf < min_w and segs and segs[-1].paragraph != pi and len(segs[-1].text.split()) + n_buf <= max_w and not pending_notes:
                segs[-1].text += " " + " ".join(buf)
            else:
                segs.append(Segment(" ".join(buf), pending_notes, pi))
                pending_notes = []
    if pending_notes and segs:  # direção no fim do texto: anexa à última cena
        segs[-1].notes += pending_notes
    # cena órfã (poucas palavras) gruda na anterior, desde que não estoure muito o limite
    tiny = max(4, min_w // 2)
    merged: list[Segment] = []
    for sg in segs:
        if merged and len(sg.text.split()) < tiny and len(merged[-1].text.split()) + len(sg.text.split()) <= int(max_w * 1.35):
            merged[-1].text += " " + sg.text
            merged[-1].notes += sg.notes
        else:
            merged.append(sg)
    return merged


def narrated_words(body: str) -> list[str]:
    """Palavras que a narração deve conter, na ordem (exclui linhas de direção entre colchetes)."""
    words: list[str] = []
    for ln in body.split("\n"):
        if _DIRECTION.match(ln):
            continue
        words += ln.split()
    return words


def verify_preserved(body: str, segments: list[Segment]) -> list[str]:
    """Lista de problemas. Vazia = os trechos, na ordem, reproduzem o roteiro palavra por palavra."""
    want = narrated_words(body)
    got = [w for s in segments for w in s.text.split()]
    if want == got:
        return []
    for i, (a, b) in enumerate(zip(want, got)):
        if a != b:
            return [f"texto diverge na palavra {i + 1}: roteiro tem {a!r}, cenas têm {b!r}"]
    return [f"quantidade de palavras difere: roteiro {len(want)}, cenas {len(got)}"]


def prepare_script(script: str, wps: float, min_s: float, max_s: float) -> list[EpisodeText]:
    eps = split_episodes(script)
    if not eps:
        raise ValueError("O roteiro está vazio.")
    for ep in eps:
        ep.segments = segment_text(ep.body, wps, min_s, max_s)
        problems = verify_preserved(ep.body, ep.segments)
        if problems:  # nunca deveria acontecer; se acontecer, é bug nosso e NÃO seguimos com texto alterado
            raise RuntimeError(f"Falha de integridade ao dividir o episódio {ep.number}: {problems[0]}")
        if not ep.segments:
            raise ValueError(f"O episódio {ep.number} não tem texto narrado.")
    return eps


def estimate_seconds(words: int, wps: float = 2.6) -> float:
    return words / wps
