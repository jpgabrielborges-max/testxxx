"""Prova que a narração é português do Brasil de verdade: TTS -> reconhecimento de fala (Whisper multilíngue offline via
sherpa-onnx) -> comparação com o texto. Inclui um CONTROLE: a mesma voz com fonemas de inglês tem de ir muito pior,
senão o teste não estaria discriminando nada."""
import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from conftest import KOKORO_DIR, needs_kokoro

ASR_DIR = Path(os.environ.get("WIF_TEST_ASR_DIR", "/home/claude/asr/sherpa-onnx-whisper-small"))
needs_asr = pytest.mark.skipif(not ASR_DIR.exists(), reason="modelo ASR ausente (defina WIF_TEST_ASR_DIR; veja tests/support/asr.py)")

SENTENCES = [
    "E se a gravidade da Terra sumisse por apenas cinco segundos?",
    "Os cientistas acreditam que ninguém sentiria o próprio peso.",
    "Era uma vez um cachorro chamado Rex, que atravessou a floresta inteira para encontrar a família.",
    "A cidade ficou em silêncio depois que os zumbis tomaram conta da avenida principal.",
    "Em mil novecentos e sessenta e nove, o homem pisou na Lua pela primeira vez.",
    "O detetive encontrou uma pista escondida atrás do relógio da sala.",
]


@pytest.fixture(scope="module")
def kokoro():
    from kokoro_onnx import Kokoro
    return Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))


@pytest.fixture(scope="module")
def asr():
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "support"))
    from asr import Asr
    return Asr(ASR_DIR)


def _mean_wer(kokoro, asr, voice, lang, tmp_path):
    from support.asr import wer
    from wifstudio.textnorm import normalize_pt
    vals = []
    for i, s in enumerate(SENTENCES):
        a, sr, _ = kokoro.create_timed(s, voice=voice, speed=1.0, lang=lang)
        p = tmp_path / f"{lang}_{voice}_{i}.wav"
        sf.write(p, a, sr)
        hyp = normalize_pt(asr.transcribe(p))  # o ASR escreve "5": normaliza igual ao texto para não contar formatação como erro
        vals.append(wer(s, hyp))
    return float(np.mean(vals))


@needs_kokoro
def test_phonemization_is_brazilian_portuguese(kokoro):
    pt = kokoro.tokenizer.phonemize("Ele disse a verdade por apenas cinco segundos.", "pt-br")
    en = kokoro.tokenizer.phonemize("Ele disse a verdade por apenas cinco segundos.", "en-us")
    assert pt != en
    assert "ɾ" in pt, "o r de 'por'/'verdade' deveria ser tepe (ɾ) em português"
    assert "ʒ" in kokoro.tokenizer.phonemize("gravidade de", "pt-br") or "dʒ" in kokoro.tokenizer.phonemize("gravidade", "pt-br"), \
        "palatalização brasileira (d + i = dʒi) ausente"


@needs_kokoro
@needs_asr
@pytest.mark.parametrize("voice", ["pf_dora", "pm_alex", "pm_santa"])
def test_voices_speak_portuguese(kokoro, asr, voice, tmp_path):
    w = _mean_wer(kokoro, asr, voice, "pt-br", tmp_path)
    print(f"WER {voice} pt-br = {w:.3f}")
    assert w <= 0.15, f"{voice}: o reconhecimento de fala entendeu mal o português (WER {w:.2f})"


@needs_kokoro
@needs_asr
def test_control_english_phonemes_are_much_worse(kokoro, asr, tmp_path):
    good = _mean_wer(kokoro, asr, "pf_dora", "pt-br", tmp_path)
    bad = _mean_wer(kokoro, asr, "pf_dora", "en-us", tmp_path)
    print(f"controle: pt-br {good:.2f} vs en-us {bad:.2f}")
    assert bad > good + 0.3


@needs_kokoro
def test_backend_rejects_foreign_voice(cfg):
    from wifstudio.tts import KokoroBackend, TTSError
    b = KokoroBackend(KOKORO_DIR / "kokoro-v1.0.onnx", KOKORO_DIR / "voices-v1.0.bin", "pt-br")
    with pytest.raises(TTSError, match="não é pt-BR"):
        b.synth("Olá", "af_heart", 1.0)
    with pytest.raises(TTSError, match="não existe"):
        b.synth("Olá", "voz_inventada", 1.0)


@needs_kokoro
def test_long_paragraph_is_not_truncated(kokoro, tmp_path):
    long_text = " ".join(SENTENCES * 2)  # ~1.000 caracteres: mais que o limite de fonemas de uma chamada do Kokoro
    a, sr, _ = kokoro.create_timed(long_text, voice="pf_dora", speed=1.0, lang="pt-br")
    single = sum(len(kokoro.create(s, voice="pf_dora", speed=1.0, lang="pt-br")[0]) for s in SENTENCES) * 2 / sr
    assert len(a) / sr > 0.8 * single, "áudio muito curto: o texto longo foi truncado"
