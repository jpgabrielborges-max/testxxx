import json

import pytest

from wifstudio import subs
from wifstudio.align import merge_with_heard, proportional, script_words
from wifstudio.comfy import (ComfyClient, ComfyError, WorkflowMappingError, load_workflow, patch_workflow,
                             resolve_node, validate_against_server)
from wifstudio.schemas import Settings, slugify
from wifstudio.textnorm import count_words, normalize_pt
from fake_servers import OBJECT_INFO


def nid(wf, title):
    """id do nó pelo TÍTULO (os ids vêm do template oficial e não devem ser usados no código)."""
    return resolve_node(wf, {"title": title})


def test_normalize_numbers_pt():
    assert normalize_pt("Em 5 segundos") == "Em cinco segundos"
    assert normalize_pt("a 1.000 km/h") == "a mil quilômetros por hora"
    assert normalize_pt("uns 3,5 por cento") == "uns três vírgula cinco por cento"
    assert normalize_pt("20%") == "vinte por cento"
    assert count_words("um dois, três!") == 3


def test_settings_validation_by_source():
    with pytest.raises(Exception, match="Cole o seu roteiro"):
        Settings(source="roteiro", script_text="oi")
    with pytest.raises(Exception, match="ideia"):
        Settings(source="ideia", hypothesis="")
    with pytest.raises(Exception, match="estilo personalizado"):
        Settings(hypothesis="E se algo", visual_style="personalizado")
    assert Settings(source="roteiro", script_text="um dois três quatro").source == "roteiro"


def test_slugify_and_settings():
    assert slugify("E se todos os humanos desaparecessem?") == "e-se-todos-os-humanos-desaparecessem"
    with pytest.raises(Exception):
        Settings(hypothesis="E se algo", duration_s=20)  # só 30, 45 ou 60


def test_workflow_mapping_by_title(cfg):
    wf, mp = load_workflow(cfg.path("comfyui.workflows.image"))
    out = patch_workflow(wf, mp, {"positive_prompt": "um gato", "seed": 9, "width": 512, "height": 512,
                                  "filename_prefix": "x/y", "steps": 2})
    pos, ks, lat = nid(out, "WIF Positive Prompt"), nid(out, "WIF Sampler"), nid(out, "WIF Latent")
    assert out[pos]["inputs"]["text"] == "um gato" and out[ks]["inputs"]["seed"] == 9
    assert out[lat]["inputs"]["width"] == 512 and out[ks]["inputs"]["steps"] == 2
    assert wf[pos]["inputs"]["text"] == "PLACEHOLDER"  # original intacto


def test_mapping_is_not_tied_to_ids(cfg):
    """Troca os IDs dos nós: o mapeamento por título continua funcionando."""
    wf, mp = load_workflow(cfg.path("comfyui.workflows.image"))
    pos_old = nid(wf, "WIF Positive Prompt")
    ids = {old: f"n{900 - int(old)}" for old in wf}
    re = {ids[o]: {**n, "inputs": {k: ([ids[v[0]], v[1]] if isinstance(v, list) else v) for k, v in n["inputs"].items()}}
          for o, n in wf.items()}
    out = patch_workflow(re, mp, {"positive_prompt": "ok", "seed": 1, "width": 64, "height": 64, "filename_prefix": "p"})
    assert out[ids[pos_old]]["inputs"]["text"] == "ok"


def test_mapping_errors(cfg):
    wf, mp = load_workflow(cfg.path("comfyui.workflows.image"))
    with pytest.raises(WorkflowMappingError, match="obrigat"):
        patch_workflow(wf, mp, {"seed": 1})
    wf["99"] = dict(wf[nid(wf, "WIF Positive Prompt")])  # dois nós com o mesmo título -> ambíguo, não adivinha
    with pytest.raises(WorkflowMappingError, match="correspondeu a 2"):
        resolve_node(wf, {"title": "WIF Positive Prompt"})


def test_validate_against_server_finds_missing_model(cfg):
    wf, _ = load_workflow(cfg.path("comfyui.workflows.image"))
    assert validate_against_server(wf, OBJECT_INFO) == []
    wf[nid(wf, "WIF Checkpoint")]["inputs"]["ckpt_name"] = "outro.safetensors"
    probs = validate_against_server(wf, OBJECT_INFO)
    assert any("outro.safetensors" in p for p in probs)
    wf[nid(wf, "WIF Save Image")]["class_type"] = "NoQueExiste"
    assert any("NoQueExiste" in p for p in validate_against_server(wf, OBJECT_INFO))


def test_comfy_image_roundtrip(cfg, fake_comfy, tmp_path):
    c = ComfyClient.from_config(cfg)
    assert c.system_stats()["devices"][0]["name"] == "FAKE GPU"
    wf, mp = load_workflow(cfg.path("comfyui.workflows.image"))
    w = patch_workflow(wf, mp, {"positive_prompt": "a red apple", "seed": 3, "width": 768, "height": 1344, "filename_prefix": "t"})
    out = c.run(w, tmp_path / "img", timeout=20)
    assert out.suffix == ".png" and out.stat().st_size > 1000
    sent = fake_comfy.workflows[-1]
    assert sent[nid(sent, "WIF Positive Prompt")]["inputs"]["text"] == "a red apple"


def test_comfy_error_and_timeout(cfg, tmp_path):
    from fake_servers import FakeComfy
    s = FakeComfy(delay=0.1, fail_prompt_containing="BOOM")
    try:
        c = ComfyClient(s.url, 0.05)
        wf, mp = load_workflow(cfg.path("comfyui.workflows.image"))
        w = patch_workflow(wf, mp, {"positive_prompt": "BOOM", "seed": 1, "width": 64, "height": 64, "filename_prefix": "t"})
        with pytest.raises(ComfyError, match="CUDA out of memory"):
            c.run(w, tmp_path / "x", timeout=10)
        s.delay = 999  # nunca termina
        w = patch_workflow(wf, mp, {"positive_prompt": "slow", "seed": 1, "width": 64, "height": 64, "filename_prefix": "t"})
        with pytest.raises(ComfyError, match="Timeout"):
            c.run(w, tmp_path / "y", timeout=0.5)
        assert s.interrupted >= 1
        w["99"] = {"class_type": "Inexistente", "inputs": {}}
        with pytest.raises(ComfyError, match="recusou"):
            c.submit(w)
    finally:
        s.close()


def test_align_proportional_and_merge():
    words = script_words("E se a gravidade sumisse?")
    p = proportional(words, 0.03, 2.03)
    assert p[0]["s"] == 0.03 and abs(p[-1]["e"] - 2.03) < 0.01
    assert all(a["e"] <= b["s"] + 1e-6 for a, b in zip(p, p[1:]))
    heard = [{"w": "E", "s": 0.1, "e": 0.2}, {"w": "se", "s": 0.2, "e": 0.3}, {"w": "gravidade", "s": 0.5, "e": 1.1},
             {"w": "sumisse", "s": 1.2, "e": 1.8}]
    m = merge_with_heard(words, heard, 2.0)
    assert [x["w"] for x in m] == words  # mantém a grafia do roteiro
    assert m[3]["s"] >= m[2]["s"] and m[3]["e"] <= 1.8  # palavra "a" (não ouvida) cabe entre vizinhas


def test_subtitles_ass_and_srt(tmp_path):
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("E se a gravidade da Terra sumisse por cinco segundos?".split())]
    ev = subs.build_chunks(words, 4, 18, False, 10.0)
    assert all(len(e["words"]) <= 5 for e in ev)
    assert all(a["end"] <= b["start"] + 0.5 for a, b in zip(ev, ev[1:]))
    subs.write_ass(ev, tmp_path / "a.ass", {"font": "Arial", "primary_color": "#FFD60A"}, 1080, 1920)
    txt = (tmp_path / "a.ass").read_text()
    assert "PlayResX: 1080" in txt and "&H000AD6FF" in txt and "{\\k" in txt
    subs.write_srt(ev, tmp_path / "a.srt")
    assert "-->" in (tmp_path / "a.srt").read_text()
