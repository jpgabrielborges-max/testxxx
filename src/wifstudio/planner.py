"""Planejamento: (A) interpreta o roteiro PRONTO do usuário sem alterar uma palavra, ou (B) cria o roteiro a partir de uma ideia.
Nos dois casos o resultado é o mesmo: bíblia visual + episódios + cenas validadas por Pydantic."""
from __future__ import annotations

from .jobs import JobContext
from .llm import OllamaClient
from .project import ProjectStore
from .prompts import analysis_prompt, episode_prompt, plan_prompt, scene_budget, visuals_prompt
from .schemas import (Character, Episode, EpisodeDraft, Location, Memory, Project, Scene, SceneVisualBatch, ScriptAnalysis,
                      SeriesPlan)
from .script import EpisodeText, Segment, prepare_script, verify_preserved
from .textnorm import count_words, normalize_pt

BATCH = 6  # cenas por chamada ao LLM (cabe folgado em 8k de contexto)


# ---------------------------------------------------------------- caminho A: roteiro do usuário

def _check_analysis(a: ScriptAnalysis, n_eps: int) -> list[str]:
    probs = []
    if len(a.episodes) != n_eps:
        probs.append(f"episodes deve ter exatamente {n_eps} item(ns), veio {len(a.episodes)}")
    names = [c.name.strip().lower() for c in a.characters]
    if len(set(names)) != len(names):
        probs.append("há personagens com o mesmo nome")
    lnames = [l.name.strip().lower() for l in a.locations]
    if len(set(lnames)) != len(lnames):
        probs.append("há cenários com o mesmo nome")
    return probs


def _make_visual_check(indices: list[int], characters: list[str], locations: list[str]):
    def check(b: SceneVisualBatch) -> list[str]:
        probs = []
        got = [s.index for s in b.scenes]
        if sorted(got) != sorted(indices):
            probs.append(f"devolva exatamente os índices {indices} (uma vez cada); vieram {got}")
        for s in b.scenes:
            if len(s.image_prompt.split()) < 8:
                probs.append(f"índice {s.index}: image_prompt curto demais (descreva sujeito, cenário, luz e clima)")
            bad = [c for c in s.characters if c not in characters]
            if bad:
                probs.append(f"índice {s.index}: personagens inexistentes {bad}. Permitidos: {characters}")
            if s.location and s.location not in locations:
                probs.append(f"índice {s.index}: cenário inexistente {s.location!r}. Permitidos: {locations}")
        return probs

    return check


def interpret_script(project: Project, llm: OllamaClient, store: ProjectStore, cfg, ctx: JobContext) -> Project:
    s = project.settings
    wps = cfg.get("narration.words_per_second", 2.5)
    pad, last_pad = cfg.get("tts.scene_pad_s", 0.35), cfg.get("tts.last_pad_s", 0.9)
    ctx.step("Dividindo o roteiro em episódios e cenas (sem alterar o texto)")
    eps: list[EpisodeText] = prepare_script(s.script_text, wps, cfg.get("script.min_scene_s", 3.0), cfg.get("script.max_scene_s", 7.5))
    total_scenes = sum(len(e.segments) for e in eps)
    for e in eps:
        words = sum(len(sg.text.split()) for sg in e.segments)
        est = words / wps
        ctx.log(f"Episódio {e.number}: {len(e.segments)} cenas, {words} palavras, ~{est:.0f}s de narração")
        if est > 90:
            ctx.log(f"AVISO: o episódio {e.number} tem ~{est:.0f}s. Reels aceita mais que 90s hoje, mas retenção cai; considere dividir em partes (marcador ---).")
    batches = sum((len(e.segments) + BATCH - 1) // BATCH for e in eps)
    ctx.plan(1 + batches)

    ctx.step("Interpretando o roteiro (gênero, personagens, cenários, estilo)")
    ctx.check()
    system, user = analysis_prompt(project, eps)
    a = llm.chat_structured(system, user, ScriptAnalysis, check=lambda x: _check_analysis(x, len(eps)), log=ctx.log)
    project.series_title = a.series_title
    project.memory = Memory(
        universe=a.universe, characters=[Character(**c.model_dump()) for c in a.characters],
        palette=a.palette, locations=[Location(**l.model_dump()) for l in a.locations], lighting=a.lighting,
        camera_style=a.camera_style, style_notes=a.style_notes,
        episode_recaps=[f"Episódio {x.number} ({x.title}): {x.summary}" for x in a.episodes])
    project.episode_outline = [{"number": x.number, "title": x.title, "premise": x.summary, "ending_hint": x.ending_hint,
                                "genre": a.genre, "tone": a.tone} for x in a.episodes]
    project.episodes = []
    store.save(project)
    ctx.log(f"Gênero deduzido: {a.genre} | tom: {a.tone} | {len(a.characters)} personagens | {len(a.locations)} cenários")
    ctx.advance()

    names = [c.name for c in project.memory.characters]
    locs = [l.name for l in project.memory.locations]
    sid = 0
    for et, summ in zip(eps, a.episodes):
        scenes: list[Scene] = []
        prev: dict | None = None
        for start in range(0, len(et.segments), BATCH):
            ctx.check()
            chunk = et.segments[start:start + BATCH]
            ctx.step(f"Visual das cenas {start + 1} a {start + len(chunk)} do episódio {et.number} (de {len(et.segments)})")
            payload = [{"index": start + i + 1, "text": sg.text, "notes": sg.notes} for i, sg in enumerate(chunk)]
            system, user = visuals_prompt(project, payload, prev)
            res = llm.chat_structured(system, user, SceneVisualBatch,
                                      check=_make_visual_check([p["index"] for p in payload], names, locs), log=ctx.log)
            by_idx = {v.index: v for v in res.scenes}
            for i, sg in enumerate(chunk):
                v = by_idx[start + i + 1]
                sid += 1
                n = start + i + 1
                narr = sg.text  # VERBATIM: nunca vem do LLM
                scene = Scene(
                    id=f"e{et.number:02d}s{n:02d}", episode=et.number, narration=narr, notes=sg.notes,
                    est_duration=round(count_words(normalize_pt(narr)) / wps + (last_pad if n == len(et.segments) else pad), 2),
                    image_prompt=v.image_prompt, animation_prompt=v.animation_prompt, visual_style=s.visual_style,
                    camera_move=v.camera_move, sfx=None if v.sfx == "none" else v.sfx, fact_type=v.fact_type,
                    characters=v.characters, location=v.location)
                scenes.append(scene)
                prev = {"location": v.location, "characters": v.characters, "image_prompt": v.image_prompt}
            ctx.advance()
        # integridade final: a narração das cenas reproduz o roteiro do usuário palavra por palavra
        problems = verify_preserved(et.body, [Segment(sc.narration) for sc in scenes])
        if problems:
            raise RuntimeError(f"Integridade do texto violada no episódio {et.number}: {problems[0]}")
        project.episodes.append(Episode(number=et.number, title=et.title or summ.title, summary=summ.summary,
                                        next_hint=summ.ending_hint, source_text=et.body, scenes=scenes))
        store.save(project)
    if cfg.get("ollama.unload_after_use", True):
        llm.unload()
        ctx.log("Modelo do Ollama descarregado da VRAM")
    ctx.log(f"Plano pronto: {len(project.episodes)} episódio(s), {total_scenes} cenas. Texto do usuário preservado.")
    return project


# ---------------------------------------------------------------- caminho B: ideia -> roteiro escrito pelo LLM

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
        palette=plan.palette, locations=[Location(**l.model_dump()) for l in plan.locations], lighting=plan.lighting,
        camera_style=plan.camera_style)
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
            narration = ls.narration.strip()
            scenes.append(Scene(
                id=f"e{n:02d}s{i:02d}", episode=n, narration=narration,
                est_duration=round(count_words(normalize_pt(narration)) / wps + (last_pad if i == len(draft.scenes) else pad), 2),
                image_prompt=ls.image_prompt, animation_prompt=ls.animation_prompt, visual_style=s.visual_style,
                camera_move=ls.camera_move, sfx=None if ls.sfx == "none" else ls.sfx, fact_type=ls.fact_type,
                characters=ls.characters, location=ls.location))
        ep = Episode(number=n, title=draft.title, hook=draft.hook, summary=draft.summary,
                     next_hint=outline["ending_hint"], scenes=scenes,
                     source_text="\n".join(sc.narration for sc in scenes))
        project.episodes.append(ep)
        project.memory.episode_recaps.append(f"Episódio {n} ({draft.title}): {draft.summary}")
        store.save(project)
        ctx.advance()

    if cfg.get("ollama.unload_after_use", True):
        llm.unload()
        ctx.log("Modelo do Ollama descarregado da VRAM")
    return project


def build_plan(project: Project, llm: OllamaClient, store: ProjectStore, cfg, ctx: JobContext) -> Project:
    """Despacha pelo modo escolhido: roteiro do usuário (preservado) ou ideia (LLM escreve)."""
    if project.settings.source == "roteiro":
        return interpret_script(project, llm, store, cfg, ctx)
    return generate_script(project, llm, store, cfg, ctx)


# ---------------------------------------------------------------- edição e aprovação

def edited_scenes(project: Project) -> list[str]:
    """Ids das cenas cuja narração difere do texto original (alteração feita de propósito pelo usuário na interface)."""
    return [sc.id for e in project.episodes for sc in e.scenes if sc.narration != sc.original_narration]


def apply_edits(project: Project, edits: dict[str, dict], store: ProjectStore, allow_text_edit: bool = True) -> Project:
    """edits = {scene_id: {narration?, image_prompt?, animation_prompt?, camera_move?, sfx?, location?, characters?}}.
    Editar a narração é um ato explícito do usuário (allow_text_edit); sem essa permissão o texto original é mantido.
    Mudar texto invalida áudio e clipe da cena; mudar prompt de imagem invalida imagem, animação e clipe."""
    for sid, patch in edits.items():
        _, sc = project.scene(sid)
        if "narration" in patch and patch["narration"].strip() != sc.narration:
            if not allow_text_edit:
                raise PermissionError(f"A narração da cena {sid} veio alterada, mas a edição de texto não foi autorizada.")
            sc.narration = patch["narration"].strip()
            sc.spoken_text = normalize_pt(sc.narration)
            sc.audio_duration = None
            sc.status["audio"] = "pending"
            sc.status["clip"] = "pending"
        if "image_prompt" in patch and patch["image_prompt"].strip() != sc.image_prompt:
            sc.image_prompt = patch["image_prompt"].strip()
            invalidate_visuals(sc)
        for k in ("animation_prompt", "camera_move", "sfx", "location", "characters"):
            if k in patch and patch[k] != getattr(sc, k):
                setattr(sc, k, (patch[k] or None) if k == "sfx" else patch[k])
                sc.status["clip"] = "pending"
                if k in ("location", "characters"):
                    invalidate_visuals(sc)
    store.save(project)
    return project


def invalidate_visuals(sc: Scene) -> None:
    """A imagem vai mudar: imagem, animação e clipe ficam pendentes e a animação velha deixa de poder ser usada."""
    sc.status.update({"image": "pending", "animation": "pending", "clip": "pending"})
    sc.files.animation = None
    sc.files.animation_source = None


def approve(project: Project, store: ProjectStore, episodes: list[int] | None = None) -> Project:
    for ep in project.episodes:
        if episodes is None or ep.number in episodes:
            ep.approved = True
    store.save(project)
    return project
