"""Modo "roteiro pronto": o texto do usuário é preservado palavra por palavra, qualquer gênero, automação em um botão,
modo supervisionado e a correção da animação velha. Ollama/ComfyUI são falsos; TTS, FFmpeg e legendas são reais."""
import re
from pathlib import Path

import pytest
from conftest import needs_kokoro

from wifstudio import render
from wifstudio.jobs import JobRunner
from wifstudio.llm import LLMError
from wifstudio.pipeline import Studio, file_hash
from wifstudio.planner import approve, apply_edits, edited_scenes
from wifstudio.schemas import Settings
from wifstudio.script import narrated_words, prepare_script, segment_text, split_episodes, verify_preserved

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("roteiro_*.txt"))


def run_job(name, fn, expect="done"):
    r = JobRunner()
    ctx = r.start(name, fn)
    r.wait(900)
    assert ctx.state.status == expect, f"{name}: {ctx.state.status} {ctx.state.error} {ctx.state.message}\n" + "\n".join(ctx.state.logs[-12:])
    return ctx


def script_project(st, text, **kw):
    return st.store.create(Settings(source="roteiro", script_text=text, **kw))


# ------------------------------------------------------------------ divisão em cenas (código, sem LLM)

@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_every_example_is_preserved_word_for_word(path):
    eps = prepare_script(path.read_text(encoding="utf-8"), 2.6, 3.0, 7.5)
    for ep in eps:
        assert verify_preserved(ep.body, ep.segments) == []
        assert ep.segments and all(1 <= len(sg.text.split()) <= int(7.5 * 2.6 * 1.35) for sg in ep.segments)
    # toda palavra narrada do arquivo (fora marcadores/direções) aparece nas cenas, na ordem
    total_in = sum(len(narrated_words(e.body)) for e in eps)
    total_out = sum(len(sg.text.split()) for e in eps for sg in e.segments)
    assert total_in == total_out


def test_markers_directions_and_abbreviations():
    txt = "[abertura aérea]\nO Dr. Silva chegou. Ninguém falou nada!\n\n---\nEpisódio 2: A volta\nFim."
    eps = split_episodes(txt)
    assert [e.title for e in eps] == ["", "A volta"] and eps[1].body == "Fim."
    segs = segment_text(eps[0].body)
    assert segs[0].notes == ["abertura aérea"]
    assert "Dr. Silva" in segs[0].text  # abreviação não quebra a frase
    assert all("[" not in sg.text for sg in segs)


def test_long_sentence_is_split_without_losing_words():
    long = "Quando ele finalmente chegou, depois de caminhar por horas, sem comida, sem água, sem esperança de ajuda, " * 3 + "caiu."
    segs = segment_text(long, 2.6, 3.0, 7.5)
    assert len(segs) > 1 and verify_preserved(long, segs) == []


def test_verify_detects_tampering():
    segs = segment_text("Um dois três quatro cinco seis sete oito.")
    segs[0].text = segs[0].text.replace("três", "TRES")
    assert verify_preserved("Um dois três quatro cinco seis sete oito.", segs)


# ------------------------------------------------------------------ interpretação com o LLM (falso) sem tocar no texto

def test_script_plan_keeps_text_verbatim_and_validates_llm(cfg, fake_ollama):
    text = (ROOT / "examples" / "roteiro_zumbi_pov.txt").read_text(encoding="utf-8")
    fake_ollama.wrong_indices_first = True  # o LLM erra os índices na primeira vez: tem de ser recusado e refeito
    st = Studio(cfg)
    p = script_project(st, text, genre="apocalipse_zumbi", visual_style="terror_sombrio", tone="auto")
    ctx = run_job("plano", lambda c: st.stage_plan(p, c))
    assert any("devolva exatamente os índices" in l for l in ctx.state.logs), "índices errados deveriam gerar nova tentativa"
    assert [e.number for e in p.episodes] == [1, 2]
    for ep in p.episodes:
        src = [e for e in split_episodes(text) if e.number == ep.number][0]
        assert " ".join(" ".join(s.narration for s in ep.scenes).split()) == " ".join(" ".join(narrated_words(src.body)).split())
        assert ep.source_text == src.body
        assert all(s.narration == s.original_narration for s in ep.scenes)
    names = {c.name for c in p.memory.characters}
    locs = {l.name for l in p.memory.locations}
    assert names == {"Rafa", "Bidu"} and locs == {"Avenida deserta", "Mercado"}
    assert all(set(s.characters) <= names and (not s.location or s.location in locs) for e in p.episodes for s in e.scenes)
    first = p.episodes[0].scenes[0]
    assert first.notes and "Câmera subjetiva" in first.notes[0]  # direção de cena virou nota, não narração
    assert "Câmera subjetiva" not in first.narration
    # prompt de imagem leva a aparência dos personagens e o cenário (consistência)
    from wifstudio.prompts import compose_image_prompt
    prompt = compose_image_prompt(p, first)
    assert "30-year-old man" in prompt and "setting:" in prompt, "aparência do personagem e cenário devem entrar no prompt"
    assert "horror" in prompt, "o estilo escolhido (terror sombrio) deve entrar no prompt"
    assert fake_ollama.unloaded


def test_llm_cannot_invent_characters(cfg, fake_ollama, monkeypatch):
    import fake_servers
    orig = fake_servers.scene_visuals
    monkeypatch.setattr(fake_servers, "scene_visuals", lambda payload, chars, locs, wrong=False: {
        "scenes": [{**s, "characters": ["Fantasma Inventado"]} for s in orig(payload, chars, locs)["scenes"]]})
    st = Studio(cfg)
    st.llm.max_retries = 1
    p = script_project(st, (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8"))
    r = JobRunner()
    ctx = r.start("plano", lambda c: st.stage_plan(p, c))
    r.wait(120)
    assert ctx.state.status == "error" and "personagens inexistentes" in ctx.state.error


def test_text_edits_are_explicit_and_tracked(cfg):
    st = Studio(cfg)
    p = script_project(st, (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8"))
    run_job("plano", lambda c: st.stage_plan(p, c))
    sc = p.episodes[0].scenes[0]
    with pytest.raises(PermissionError):
        apply_edits(p, {sc.id: {"narration": "Texto trocado."}}, st.store, allow_text_edit=False)
    assert sc.narration == sc.original_narration and edited_scenes(p) == []
    apply_edits(p, {sc.id: {"narration": "Texto trocado com 5 palavras."}}, st.store, allow_text_edit=True)
    assert edited_scenes(p) == [sc.id] and "cinco" in sc.spoken_text and "5" in sc.narration


# ------------------------------------------------------------------ animação velha após regenerar imagem (bug da auditoria)

def test_regenerating_image_discards_old_animation(cfg, fake_comfy):
    st = Studio(cfg)
    p = script_project(st, (ROOT / "examples" / "roteiro_animais.txt").read_text(encoding="utf-8"), mode="completo")
    run_job("plano", lambda c: st.stage_plan(p, c))
    approve(p, st.store)
    run_job("imagens", lambda c: st.stage_images(p, c))
    run_job("anim", lambda c: st.stage_animations(p, c))
    sc = p.episodes[0].scenes[1]
    old_anim = st.store.abs(p.id, sc.files.animation)
    assert sc.status["animation"] == "done" and old_anim.exists()
    assert sc.files.animation_source == file_hash(st.store.abs(p.id, sc.files.image))
    n_img = len(fake_comfy.workflows)
    run_job("regen", lambda c: st.regenerate_scene(p, sc.id, c, "image"))
    assert len(fake_comfy.workflows) == n_img + 1
    assert sc.status["image"] == "done"
    assert sc.status["animation"] == "pending" and sc.files.animation is None and not old_anim.exists(), "animação velha ficou"
    others = [s for s in p.episodes[0].scenes if s.id != sc.id]
    assert all(s.status["animation"] == "done" for s in others), "as outras cenas não devem perder a animação"
    # só a cena regenerada é animada de novo
    n = len(fake_comfy.workflows)
    run_job("anim2", lambda c: st.stage_animations(p, c))
    assert len(fake_comfy.workflows) == n + 1 and sc.status["animation"] == "done"


def test_edit_of_image_prompt_invalidates_animation(cfg, fake_comfy):
    st = Studio(cfg)
    p = script_project(st, (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8"), mode="completo")
    run_job("plano", lambda c: st.stage_plan(p, c))
    approve(p, st.store)
    run_job("imagens", lambda c: st.stage_images(p, c))
    run_job("anim", lambda c: st.stage_animations(p, c))
    sc = p.episodes[0].scenes[0]
    assert sc.files.animation
    apply_edits(p, {sc.id: {"image_prompt": "a completely different frame, red door, night"}}, st.store)
    assert sc.files.animation is None and sc.status["animation"] == "pending" and sc.status["image"] == "pending"


@needs_kokoro
def test_render_ignores_stale_animation(cfg, fake_comfy):
    st = Studio(cfg)
    p = script_project(st, (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8"), mode="completo")
    run_job("plano", lambda c: st.stage_plan(p, c))
    approve(p, st.store)
    run_job("imagens", lambda c: st.stage_images(p, c))
    run_job("anim", lambda c: st.stage_animations(p, c))
    sc = p.episodes[0].scenes[0]
    stale = st.store.abs(p.id, sc.files.animation)
    # simula o estado corrompido que o bug produzia: imagem nova no disco, animação da imagem antiga ainda marcada como pronta
    img = st.store.abs(p.id, sc.files.image)
    fake_comfy.make_image(img, "outra imagem totalmente diferente")
    assert sc.files.animation_source != file_hash(img)
    ctx = run_job("render", lambda c: st.stage_render(p, c))
    logs = "\n".join(ctx.state.logs)
    assert f"{sc.id}: a animação existente é de uma imagem antiga e foi IGNORADA" in logs
    assert f"Clipe {sc.id} (imagem com" in logs and f"Clipe {p.episodes[0].scenes[1].id} (animação IA)" in logs
    assert stale.exists()


# ------------------------------------------------------------------ um botão: roteiro -> MP4

@needs_kokoro
def test_run_all_automatic_from_pasted_script(cfg, fake_comfy):
    text = (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8")
    st = Studio(cfg)
    p = script_project(st, text, visual_style="terror_sombrio", review_mode="automatico", mode="completo")
    ctx = run_job("completo", lambda c: st.run_all(p, c))
    assert ctx.state.stage_count == 5 and ctx.state.stage_index == 5
    logs = "\n".join(ctx.state.logs)
    for etapa in ("Plano de cenas", "Imagens", "Narração em português", "Animação das cenas", "Edição e vídeo final"):
        assert etapa in logs
    p = st.store.load(p.id)
    ep = p.episodes[0]
    mp4 = st.store.abs(p.id, ep.final_video)
    rep = render.verify_reel(mp4, sum(st.scene_duration(ep, s) for s in ep.scenes), tol=0.05)
    assert rep["ok"], [c for c in rep["checks"] if not c["ok"]]
    # as legendas dizem EXATAMENTE o texto do usuário (palavra por palavra, com a grafia dele)
    srt = st.store.abs(p.id, ep.final_srt).read_text(encoding="utf-8")
    srt_words = " ".join(l for l in srt.splitlines() if l and "-->" not in l and not l.isdigit()).split()
    assert srt_words == narrated_words(text), "as legendas não reproduzem o roteiro do usuário"
    # tudo animado (escopo "todas") e sfx/trilha usados
    assert all(s.status["animation"] == "done" for s in ep.scenes)
    assert any(s.sfx for s in ep.scenes)
    # rodar de novo não refaz nada
    n = len(fake_comfy.workflows)
    run_job("completo2", lambda c: st.run_all(p, c))
    assert len(fake_comfy.workflows) == n


@needs_kokoro
def test_run_all_supervised_pauses_at_each_gate(cfg, fake_comfy):
    text = (ROOT / "examples" / "roteiro_animais.txt").read_text(encoding="utf-8")
    st = Studio(cfg)
    p = script_project(st, text, review_mode="supervisionado", mode="rapido")
    ctx = run_job("1", lambda c: st.run_all(p, c), expect="paused")
    assert "aprove o plano" in ctx.state.message.lower() or "Aprovar plano" in ctx.state.message
    assert not fake_comfy.workflows, "nenhuma imagem deve ser gerada antes de aprovar o plano"
    p = st.store.load(p.id)
    approve(p, st.store)
    ctx = run_job("2", lambda c: st.run_all(p, c), expect="paused")
    assert "Aprovar imagens" in ctx.state.message and fake_comfy.workflows
    p = st.store.load(p.id)
    assert all(s.status["image"] == "done" for s in p.episodes[0].scenes) and not p.episodes[0].final_video
    p.images_approved = True
    st.store.save(p)
    run_job("3", lambda c: st.run_all(p, c))
    assert st.store.load(p.id).episodes[0].final_video


def test_run_all_stops_with_clear_error_when_comfy_down(cfg):
    st = Studio(cfg)
    st.comfy.host = "http://127.0.0.1:9"  # nada escutando
    p = script_project(st, (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8"))
    ctx = run_job("completo", lambda c: st.run_all(p, c), expect="error")
    assert "ComfyUI inacessível" in ctx.state.error
