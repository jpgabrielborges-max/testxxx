"""Smoke da interface com o AppTest do Streamlit: renderiza sem exceção, preenche o formulário, gera o roteiro (Ollama falso)
e aprova. Também cobre os botões da barra lateral."""
import os
import shutil
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app_env(tmp_path, fake_ollama, fake_comfy, monkeypatch):
    shutil.copytree(ROOT / "workflows", tmp_path / "workflows")
    (tmp_path / "config.yaml").write_text(
        (ROOT / "config.yaml").read_text().replace("http://127.0.0.1:11434", fake_ollama.url).replace("http://127.0.0.1:8188", fake_comfy.url),
        encoding="utf-8")
    monkeypatch.setattr("wifstudio.config.ROOT", tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", fake_ollama.url)
    monkeypatch.setenv("COMFYUI_HOST", fake_comfy.url)
    return tmp_path


def test_app_flow(app_env):
    import wifstudio.config as C
    orig = C.load_config
    C.load_config = lambda root=None, **kw: orig(app_env, **kw)
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
        assert not at.exception, at.exception
        assert any("Gerar roteiro" in b.label for b in at.button)
        at.text_area[0].set_value("E se a gravidade da Terra desaparecesse por 5 segundos?").run()
        gen = next(b for b in at.button if "Gerar roteiro" in b.label)
        gen.click().run()
        assert not at.exception, at.exception
        for _ in range(60):  # espera o job terminar (thread)
            time.sleep(0.5)
            at.run()
            if any("Editar e aprovar" in b.label for b in at.button):
                break
        assert any("Editar e aprovar" in b.label for b in at.button), [m.value for m in at.error] + [str(e) for e in at.exception]
        ap = next(b for b in at.button if "Editar e aprovar" in b.label)
        ap.click().run()
        assert not at.exception, at.exception
        assert any("aprovado" in m.value or "Roteiro salvo" in m.value for m in list(at.markdown) + list(at.success))
    finally:
        C.load_config = orig
