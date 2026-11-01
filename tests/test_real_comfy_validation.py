"""Valida os workflows contra um ComfyUI REAL (sem fixtures). Rode com o ComfyUI aberto:
    set WIF_REAL_COMFY_URL=http://127.0.0.1:8188   (PowerShell: $env:WIF_REAL_COMFY_URL="http://127.0.0.1:8188")
Não precisa de GPU nem de pesos: só confirma que o servidor aceita a estrutura. Com os modelos instalados, o job executa de verdade."""
import copy
import os
from pathlib import Path

import pytest

from wifstudio.comfy import ComfyClient, ComfyError, load_workflow, patch_workflow, validate_against_server

URL = os.environ.get("WIF_REAL_COMFY_URL")
pytestmark = pytest.mark.skipif(not URL, reason="defina WIF_REAL_COMFY_URL para testar contra um ComfyUI real")
ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "flux_schnell_api": {"positive_prompt": "a red apple", "seed": 1, "width": 768, "height": 1344, "filename_prefix": "wif/t", "steps": 4},
    "wan22_ti2v_5b_api": {"input_image": "example.png", "prompt": "slow zoom in", "seed": 1, "filename_prefix": "wif/v",
                          "width": 704, "height": 1280, "length": 49, "fps": 24, "steps": 20},
}


@pytest.mark.parametrize("name", list(CASES))
def test_workflow_accepted_by_real_comfyui(name):
    c = ComfyClient(URL, 0.5)
    wf, mp = load_workflow(ROOT / "workflows" / f"{name}.json")
    assert validate_against_server(wf, c.object_info()) == []
    w = patch_workflow(wf, mp, CASES[name])
    pid = c.submit(w)  # lança ComfyError se o validador real recusar
    assert pid
    try:
        c.wait(pid, 600 if name.startswith("wan") else 300)
    except ComfyError as exc:
        # sem os pesos o ComfyUI falha ao CARREGAR o modelo, nunca por estrutura inválida
        assert "header" in str(exc) or "No such file" in str(exc) or "safetensors" in str(exc).lower(), str(exc)


@pytest.mark.parametrize("name", list(CASES))
def test_real_validator_rejects_broken_workflow(name):
    """Controle negativo: prova que o servidor valida de verdade (senão 'aceito' não significaria nada)."""
    c = ComfyClient(URL, 0.5)
    wf, mp = load_workflow(ROOT / "workflows" / f"{name}.json")
    bad = copy.deepcopy(patch_workflow(wf, mp, CASES[name]))
    ks = next(i for i, n in bad.items() if n["class_type"] == "KSampler")
    bad[ks]["inputs"]["steps"] = "muitos"
    with pytest.raises(ComfyError, match="recusou"):
        c.submit(bad)
