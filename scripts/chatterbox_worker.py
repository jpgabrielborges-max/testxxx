"""Worker do Chatterbox Multilingual. Roda no Python do AMBIENTE SEPARADO do Chatterbox, nunca no do aplicativo.

  python chatterbox_worker.py --text "Olá" --out saida.wav --lang pt [--ref minha_voz.wav]

ATENCAO: este worker NAO foi testado no ambiente de desenvolvimento (sem GPU). O README explica a instalação.
O áudio gerado pelo Chatterbox leva marca d'água inaudível (Perth) e a licença do código é MIT; confira a licença
da versão que você instalar antes de uso comercial.
"""
import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default="pt")
    ap.add_argument("--ref", default="")
    a = ap.parse_args()
    import torch
    import torchaudio as ta
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    kw = {"language_id": a.lang}
    if a.ref:
        kw["audio_prompt_path"] = a.ref
    wav = model.generate(a.text, **kw)
    ta.save(a.out, wav.cpu(), model.sr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
