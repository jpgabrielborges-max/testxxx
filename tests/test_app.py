"""Smoke da interface (AppTest do Streamlit): cola um roteiro, cria o plano com o Ollama falso, edita/aprova e confere que o
texto do usuário continua intacto na tela."""
import shutil
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8")


@pytest.fixture()
def app_env(tmp_path, fake_ollama, fake_comfy):
    shutil.copytree(ROOT / "workflows", tmp_path / "workflows")
    (tmp_path / "config.yaml").write_text(
        (ROOT / "config.yaml").read_text().replace("http://127.0.0.1:11434", fake_ollama.url).replace("http://127.0.0.1:8188", fake_comfy.url),
        encoding="utf-8")
    return tmp_path


def _wait_for(at, label, tries=60):
    for _ in range(tries):
        time.sleep(0.5)
        at.run()
        if any(label in b.label for b in at.button):
            return True
    return False


def test_ui_paste_script_plan_and_approve(app_env):
    import wifstudio.config as C
    orig = C.load_config
    C.load_config = lambda root=None, **kw: orig(app_env, **kw)
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
        assert not at.exception, at.exception
        labels = [b.label for b in at.button]
        assert any("Gerar vídeo completo" in l for l in labels) and any("Só criar o plano" in l for l in labels)
        # botão vazio: valida no clique, sem travar
        next(b for b in at.button if "Só criar o plano" in b.label).click().run()
        assert at.error, "deveria avisar que falta o roteiro"
        at.text_area(key="script_in").set_value(SCRIPT).run()
        assert any("palavras" in c.value and "episódio" in c.value for c in at.caption)
        next(b for b in at.button if "Só criar o plano" in b.label).click().run()
        assert not at.exception, at.exception
        assert _wait_for(at, "Aprovar plano"), [e.value for e in at.error] + [str(x) for x in at.exception]
        assert any("Texto preservado" in s.value for s in at.success)
        # narração vem travada (disabled) até o usuário autorizar a edição de texto
        narr = [t for t in at.text_area if t.label.startswith("Narração")]
        assert narr and all(t.proto.disabled for t in narr)
        assert " ".join(" ".join(t.value for t in narr).split()) == " ".join(SCRIPT.split())
        next(b for b in at.button if "Aprovar plano" in b.label).click().run()
        assert not at.exception, at.exception
        assert any("aprovado" in m.value for m in at.markdown)
    finally:
        C.load_config = orig
