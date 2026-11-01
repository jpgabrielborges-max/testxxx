import json
import time

import pytest
from conftest import needs_kokoro

from fake_servers import FakeOllama
from wifstudio import render
from wifstudio.jobs import JobRunner
from wifstudio.llm import LLMError, OllamaClient
from wifstudio.pipeline import StageError, Studio
from wifstudio.planner import apply_edits, approve
from wifstudio.schemas import Settings


def _parse_srt(text):
    import re
    out = []
    for m in re.finditer(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)\n(.*?)(?:\n\n|\Z)", text, re.S):
        g = [int(x) for x in m.groups()[:8]]
        out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, m.group(9)))
    return out


def _text_pixels(mp4, t):
    """Conta pixels amarelos (palavra falada) ou brancos puros (palavras a falar) na faixa das legendas."""
    import subprocess
    import numpy as np
    from PIL import Image
    import io
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", "-f", "image2pipe",
                          "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    a = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)[1150:1650]
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    yellow = (r > 235) & (g > 190) & (g < 235) & (b < 80)
    return int(yellow.sum())  # só o amarelo: as imagens falsas de teste têm círculos brancos que confundiriam


def run_job(name, fn, studio=None):
    r = JobRunner()
    ctx = r.start(name, fn)
    r.wait(600)
    assert ctx.state.status == "done", f"{name}: {ctx.state.status} {ctx.state.error}\n" + "\n".join(ctx.state.logs[-15:])
    return ctx


def new_project(studio, **kw):
    s = Settings(hypothesis=kw.pop("hypothesis", "E se a gravidade da Terra desaparecesse por 5 segundos?"), **kw)
    return studio.store.create(s)


def test_script_generation_validated_and_retry(cfg, fake_ollama):
    fake_ollama.first_overlong = True  # primeiro episódio vem longo demais -> o aplicativo precisa pedir de novo
    st = Studio(cfg)
    p = new_project(st, duration_s=30, episodes=1)
    ctx = run_job("roteiro", lambda c: st.stage_script(p, c))
    assert fake_ollama.episode_calls == 2, "deveria ter repetido por estourar o orçamento de palavras"
    assert any("total de palavras" in l for l in ctx.state.logs)
    ep = p.episodes[0]
    assert 6 <= len(ep.scenes) <= 8
    assert ep.scenes[0].narration.startswith("E se a gravidade")
    assert {s.fact_type for s in ep.scenes} == {"fato", "especulacao"}
    assert fake_ollama.unloaded, "o modelo do Ollama deve ser descarregado ao fim"
    # persistiu
    assert st.store.load(p.id).episodes[0].scenes[0].id == "e01s01"


def test_llm_gives_up_after_retries(cfg, fake_ollama):
    fake_ollama.first_overlong = False
    llm = OllamaClient(fake_ollama.url, "qwen2.5:14b", max_retries=1)
    from wifstudio.schemas import EpisodeDraft
    with pytest.raises(LLMError, match="2 tentativas"):
        llm.chat_structured("s", "u", EpisodeDraft, check=lambda d: ["sempre inválido"])


def test_series_of_four_keeps_memory(cfg, fake_ollama):
    st = Studio(cfg)
    p = new_project(st, duration_s=30, episodes=4)
    run_job("roteiro", lambda c: st.stage_script(p, c))
    assert [e.number for e in p.episodes] == [1, 2, 3, 4]
    assert len(p.memory.episode_recaps) == 4
    last_prompt = [r for r in fake_ollama.requests if r["format"]["title"] == "EpisodeDraft"][-1]["messages"][1]["content"]
    assert "Episódio 3" in last_prompt, "o prompt do episódio 4 deve carregar o resumo do episódio 3"
    assert "Feche a história" in last_prompt


def test_requires_approval_and_edits_invalidate(cfg):
    st = Studio(cfg)
    p = new_project(st)
    run_job("roteiro", lambda c: st.stage_script(p, c))
    r = JobRunner()
    ctx = r.start("imagens", lambda c: st.stage_images(p, c))
    r.wait(30)
    assert ctx.state.status == "error" and "Aprove o roteiro" in ctx.state.error
    sc = p.episodes[0].scenes[1]
    sc.status.update({"image": "done", "audio": "done", "clip": "done"})
    apply_edits(p, {sc.id: {"narration": "Em 5 segundos, nada muda."}}, st.store)
    assert sc.narration == "Em cinco segundos, nada muda."
    assert sc.status["audio"] == "pending" and sc.status["clip"] == "pending" and sc.status["image"] == "done"
    apply_edits(p, {sc.id: {"image_prompt": "novo prompt"}}, st.store)
    assert sc.status["image"] == "pending"


def test_images_per_scene_error_is_reported(cfg, fake_comfy):
    fake_comfy.fail_on = "floating in mid air"  # cena 3 falha
    st = Studio(cfg)
    p = new_project(st)
    run_job("roteiro", lambda c: st.stage_script(p, c))
    approve(p, st.store)
    r = JobRunner()
    ctx = r.start("imagens", lambda c: st.stage_images(p, c))
    r.wait(120)
    assert ctx.state.status == "error" and "e01s03" in ctx.state.error and "CUDA out of memory" in ctx.state.error
    done = [s.id for s in p.episodes[0].scenes if s.status["image"] == "done"]
    errs = [s.id for s in p.episodes[0].scenes if s.status["image"] == "error"]
    assert errs == ["e01s03"] and len(done) == 6, "as demais cenas continuam e o erro não é escondido"
    assert ctx.state.done == ctx.state.total == 7, "progresso reflete trabalho real"
    # regenerar só a cena com erro, agora sem falha
    fake_comfy.fail_on = None
    n_before = len(fake_comfy.workflows)
    run_job("regen", lambda c: st.regenerate_scene(p, "e01s03", c, "image"))
    assert len(fake_comfy.workflows) == n_before + 1
    assert p.scene("e01s03")[1].status["image"] == "done" and p.scene("e01s03")[1].seed_bump == 1


def test_workflow_incompatible_is_blocked(cfg, fake_comfy):
    fake_comfy.object_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0] = ["outro.safetensors"]
    st = Studio(cfg)
    p = new_project(st)
    run_job("roteiro", lambda c: st.stage_script(p, c))
    approve(p, st.store)
    r = JobRunner()
    ctx = r.start("imagens", lambda c: st.stage_images(p, c))
    r.wait(30)
    assert ctx.state.status == "error" and "flux1-schnell-fp8.safetensors" in ctx.state.error
    assert not fake_comfy.workflows


def test_cancel_stops_job(cfg, fake_comfy):
    fake_comfy.delay = 1.0
    st = Studio(cfg)
    p = new_project(st)
    run_job("roteiro", lambda c: st.stage_script(p, c))
    approve(p, st.store)
    r = JobRunner()
    ctx = r.start("imagens", lambda c: st.stage_images(p, c))
    time.sleep(0.8)
    ctx.cancel()
    r.wait(30)
    assert ctx.state.status == "cancelled"
    assert fake_comfy.interrupted >= 1
    assert all(s.status["image"] != "running" for s in p.episodes[0].scenes)


@needs_kokoro
def test_acceptance_end_to_end_gravity_30s(cfg, fake_comfy):
    """Teste de aceitação do pedido: 30 s, 1 episódio, pt-BR, cinematográfico, vertical.
    Ollama e ComfyUI são FALSOS; TTS (Kokoro pt-BR), alinhamento, legendas, FFmpeg e ffprobe são REAIS."""
    st = Studio(cfg)
    p = new_project(st, duration_s=30, episodes=1, visual_style="cinematografico", tone="misterioso", voice="pf_dora")
    run_job("roteiro", lambda c: st.stage_script(p, c))
    approve(p, st.store)
    run_job("imagens", lambda c: st.stage_images(p, c))
    ctx = run_job("video", lambda c: st.stage_render(p, c))  # gera a narração que falta e monta o MP4
    ep = p.episodes[0]
    mp4 = st.store.abs(p.id, ep.final_video)
    assert mp4.exists()
    rep = render.verify_reel(mp4, 30)
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    assert rep["ok"], [c for c in rep["checks"] if not c["ok"]]
    assert 25 <= rep["duration"] <= 35
    assert ctx.state.done == ctx.state.total
    # sincronia: cada cena começa quando o áudio da cena começa (tempo acumulado das durações)
    srt = st.store.abs(p.id, ep.final_srt).read_text()
    assert srt.count("-->") >= 10
    # legendas REALMENTE queimadas no vídeo, no momento certo (checagem por pixel, não só pelo arquivo .srt)
    blocks = _parse_srt(srt)
    for b in (blocks[1], blocks[len(blocks) // 2], blocks[-2]):
        mid = (b[0] + b[1]) / 2
        assert _text_pixels(mp4, mid) > 150, f"sem legenda visível em t={mid:.2f}s ({b[2]!r})"
    gaps = [(a[1], b[0]) for a, b in zip(blocks, blocks[1:]) if b[0] - a[1] > 0.12]
    assert gaps, "esperava ao menos um intervalo sem legenda"
    g = gaps[0]
    assert _text_pixels(mp4, (g[0] + g[1]) / 2) < 30, "legenda aparecendo fora do tempo da fala"
    # cópias em outputs/
    assert (cfg.path("paths.outputs_dir") / p.id / mp4.name).exists()
    # relatório salvo
    assert (mp4.parent / "relatorio_verificacao.json").exists()
    # retomar projeto do disco
    again = st.store.load(p.id)
    assert again.episodes[0].final_video == ep.final_video
    # regenerar uma cena e remontar sem refazer o resto
    n_img = len(fake_comfy.workflows)
    run_job("regen", lambda c: st.regenerate_scene(p, "e01s02", c, "image"))
    assert len(fake_comfy.workflows) == n_img + 1
    run_job("video2", lambda c: st.stage_render(p, c))
    assert render.verify_reel(st.store.abs(p.id, p.episodes[0].final_video), 30)["ok"]


@needs_kokoro
def test_full_mode_with_animation_and_fallback(cfg, fake_comfy):
    st = Studio(cfg)
    p = new_project(st, duration_s=30, mode="completo")
    run_job("roteiro", lambda c: st.stage_script(p, c))
    approve(p, st.store)
    run_job("imagens", lambda c: st.stage_images(p, c))
    p.episodes[0].scenes[0].animate = True
    p.episodes[0].scenes[3].animate = True
    run_job("anim", lambda c: st.stage_animations(p, c))
    sc = p.episodes[0].scenes
    assert sc[0].status["animation"] == "done" and sc[0].files.animation.endswith(".mp4")
    assert len(fake_comfy.uploads) == 2 and fake_comfy.freed >= 2
    assert sc[1].status["animation"] == "pending"
    run_job("video", lambda c: st.stage_render(p, c))
    rep = render.verify_reel(st.store.abs(p.id, p.episodes[0].final_video), 30)
    assert rep["ok"], rep
