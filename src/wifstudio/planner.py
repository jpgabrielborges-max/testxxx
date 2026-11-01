"""Roteiro: bíblia da série, episódios e cenas. Usa o Ollama e valida tudo com Pydantic."""
from __future__ import annotations

from .jobs import JobContext
from .llm import OllamaClient
from .prompts import episode_prompt, plan_prompt, scene_budget
from .project import ProjectStore
from .schemas import Character, Episode, EpisodeDraft, Memory, Project, Scene, SeriesPlan
from .textnorm import count_words, normalize_pt


def _check_plan(plan: SeriesPlan, n_episodes: int) -> list[str]:
    probs = []
    if len(plan.episodes) != n_episodes:
        probs.append(f"o plano deve ter exatamente {n_episodes} episódio(s), veio {len(plan.episodes)}")
    return probs


def _make_episode_check(n_scenes: int, words: int, tol: float):
    lo, hi = round(words * (1 - tol)), round(words * (1 + tol))

    def check(d: EpisodeDraft) -> list[str]:
        probs = []
        if abs(len(d.scenes) - n_scenes) > 1:
            probs.append(f"são necessárias {n_scenes} cenas (±1), vieram {len(d.scenes)}")
        total = sum(count_words(normalize_pt(s.narration)) for s in d.scenes)
        if not lo <= total <= hi:
            probs.append(f"o total de palavras narradas deve ficar entre {lo} e {hi}, veio {total}"
                         + (" (corte texto)" if total > hi else " (acrescente conteúdo)"))
        return probs

    return check


def generate_script(project: Project, llm: OllamaClient, store: ProjectStore, cfg, ctx: JobContext) -> Project:
    s = project.settings
    wps = cfg.get("narration.words_per_second", 2.5)
    tol = cfg.get("narration.tolerance", 0.2)
    pad = cfg.get("tts.scene_pad_s", 0.35)
    last_pad = cfg.get("tts.last_pad_s", 0.9)
    n_scenes, words = scene_budget(s.duration_s, wps, pad, last_pad)
    ctx.plan(1 + s.episodes)
    ctx.log(f"Orçamento por episódio: {n_scenes} cenas, ~{words} palavras ({s.duration_s}s)")

    ctx.step("Criando bíblia da série e plano de episódios")
    ctx.check()
    system, user = plan_prompt(s)
    plan = llm.chat_structured(system, user, SeriesPlan, check=lambda p: _check_plan(p, s.episodes), log=ctx.log)
    project.series_title = plan.series_title
    project.memory = Memory(
        universe=plan.universe, characters=[Character(**c.model_dump()) for c in plan.characters],
        palette=plan.palette, locations=plan.locations, lighting=plan.lighting, camera_style=plan.camera_style,
    )
    project.episode_outline = [e.model_dump() for e in plan.episodes]
    project.episodes = []
    store.save(project)
    ctx.advance()

    for outline in project.episode_outline:
        ctx.check()
        n = outline["number"]
        ctx.step(f"Escrevendo episódio {n} de {s.episodes}")
        system, user = episode_prompt(project, outline, n_scenes, words, tol)
        draft = llm.chat_structured(system, user, EpisodeDraft, check=_make_episode_check(n_scenes, words, tol), log=ctx.log)
        scenes: list[Scene] = []
        for i, ls in enumerate(draft.scenes, start=1):
            narration = normalize_pt(ls.narration)
            scenes.append(Scene(
                id=f"e{n:02d}s{i:02d}", episode=n, narration=narration,
                est_duration=round(count_words(narration) / wps + (last_pad if i == len(draft.scenes) else pad), 2),
                image_prompt=ls.image_prompt, animation_prompt=ls.animation_prompt, visual_style=s.visual_style,
                camera_move=ls.camera_move, sfx=None if ls.sfx == "none" else ls.sfx, fact_type=ls.fact_type,
                characters=ls.characters,
            ))
        ep = Episode(number=n, title=draft.title, hook=draft.hook, summary=draft.summary,
                     next_hint=outline["ending_hint"], scenes=scenes)
        project.episodes.append(ep)
        project.memory.episode_recaps.append(f"Episódio {n} ({draft.title}): {draft.summary}")
        store.save(project)
        ctx.advance()

    if cfg.get("ollama.unload_after_use", True):
        llm.unload()
        ctx.log("Modelo do Ollama descarregado da VRAM")
    return project


def apply_edits(project: Project, edits: dict[str, dict], store: ProjectStore) -> Project:
    """edits = {scene_id: {narration?, image_prompt?, animation_prompt?, camera_move?, sfx?}}.
    A narração editada é normalizada (números por extenso). Mudar texto invalida áudio e clipe da cena;
    mudar prompt de imagem invalida imagem, animação e clipe."""
    for sid, patch in edits.items():
        _, sc = project.scene(sid)
        if "narration" in patch and patch["narration"].strip() != sc.narration:
            sc.narration = normalize_pt(patch["narration"])
            sc.status["audio"] = "pending"
            sc.status["clip"] = "pending"
        if "image_prompt" in patch and patch["image_prompt"].strip() != sc.image_prompt:
            sc.image_prompt = patch["image_prompt"].strip()
            sc.status.update({"image": "pending", "animation": "pending", "clip": "pending"})
        for k in ("animation_prompt", "camera_move", "sfx"):
            if k in patch and patch[k] != getattr(sc, k):
                setattr(sc, k, patch[k] or None if k == "sfx" else patch[k])
                sc.status["clip"] = "pending"
    store.save(project)
    return project


def approve(project: Project, store: ProjectStore, episodes: list[int] | None = None) -> Project:
    for ep in project.episodes:
        if episodes is None or ep.number in episodes:
            ep.approved = True
    store.save(project)
    return project
