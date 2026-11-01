"""Orquestração das etapas. Cada função recebe um JobContext, reporta progresso real e salva o projeto a cada cena.
`run_all` encadeia tudo (botão "Gerar vídeo completo"), com portões de revisão no modo supervisionado."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import render, sfx, subs
from .align import Aligner
from .comfy import ComfyClient, load_workflow, patch_workflow, validate_against_server
from .jobs import Cancelled, JobContext
from .llm import OllamaClient
from .planner import approve, build_plan, invalidate_visuals
from .preflight import VramSampler
from .project import ProjectStore, append_log, dump_json, project_log_path
from .prompts import compose_image_prompt
from .schemas import Episode, Project, Scene
from .textnorm import display_units, normalize_pt
from .tts import make_backend, synth_to_wav


class StageError(RuntimeError):
    pass


def file_hash(path: Path | None) -> Optional[str]:
    if not path or not Path(path).exists():
        return None
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Studio:
    def __init__(self, cfg, store: ProjectStore | None = None, llm: OllamaClient | None = None,
                 comfy: ComfyClient | None = None, tts_backend=None, aligner: Aligner | None = None):
        self.cfg = cfg
        self.store = store or ProjectStore(cfg.path("paths.projects_dir"))
        self.llm = llm or OllamaClient.from_config(cfg)
        self.comfy = comfy or ComfyClient.from_config(cfg)
        self._tts = tts_backend
        self._aligner = aligner

    # ---------------------------------------------------------------- utilidades
    def _sink(self, project: Project) -> Callable[[str], None]:
        path = project_log_path(self.store, project.id)
        return lambda m: append_log(path, m)

    def _scenes(self, project: Project, scene_ids: Optional[Iterable[str]], episodes: Optional[Iterable[int]]) -> list[tuple[Episode, Scene]]:
        ids = set(scene_ids) if scene_ids else None
        eps = set(episodes) if episodes else None
        return [(e, s) for e in project.episodes if not eps or e.number in eps for s in e.scenes if ids is None or s.id in ids]

    def _require_approved(self, project: Project, pairs: list[tuple[Episode, Scene]]) -> None:
        bad = sorted({e.number for e, _ in pairs if not e.approved})
        if bad:
            raise StageError(f"Aprove o plano do(s) episódio(s) {bad} antes de continuar (botão 'Aprovar plano').")

    def _ep_dir(self, project: Project, ep: Episode) -> Path:
        return self.store.sub(project.id, "episodes", f"ep{ep.number:02d}")

    def _workflow(self, kind: str):
        return load_workflow(self.cfg.path(f"comfyui.workflows.{kind}"))

    def _finish_stage(self, errors: list[str], what: str) -> None:
        if errors:
            raise StageError(f"{what}: {len(errors)} cena(s) com erro. " + " | ".join(errors[:5]))

    def _log_sink(self, project: Project, ctx: JobContext) -> None:
        ctx._sink = ctx._sink or self._sink(project)

    # ---------------------------------------------------------------- 1. plano (roteiro do usuário OU ideia)
    def stage_plan(self, project: Project, ctx: JobContext) -> Project:
        self._log_sink(project, ctx)
        project.images_approved = project.animations_approved = False
        return build_plan(project, self.llm, self.store, self.cfg, ctx)

    stage_script = stage_plan  # nome antigo

    # ---------------------------------------------------------------- 2. imagens
    def check_workflows(self, kinds=("image",)) -> list[str]:
        """Compara os workflows com o ComfyUI que está rodando. Lista vazia = tudo certo."""
        info = self.comfy.object_info()
        probs = []
        for k in kinds:
            wf, _ = self._workflow(k)
            probs += [f"[{k}] {p}" for p in validate_against_server(wf, info)]
        return probs

    def _drop_animation(self, project: Project, sc: Scene) -> None:
        """A imagem mudou: a animação feita a partir da imagem antiga NÃO pode mais ser usada."""
        old = self.store.abs(project.id, sc.files.animation)
        if old and old.exists():
            try:
                old.unlink()
            except OSError:
                pass
        invalidate_visuals(sc)  # (quem chama define o status da imagem logo depois)

    def _seed(self, project: Project, ep: Episode, sc: Scene) -> int:
        base = project.settings.seed + sc.seed_bump * 100003
        if self.cfg.get("consistency.share_seed_by_location", False) and sc.location:
            names = [l.name for l in project.memory.locations]
            if sc.location in names:
                return base + (names.index(sc.location) + 1) * 7919
        return base + ep.scenes.index(sc) * 101

    def _chain_reference(self, project: Project, ep: Episode, sc: Scene) -> Optional[Path]:
        """Imagem da cena anterior, se for a mesma situação (cenário e personagens), para continuidade via img2img."""
        if not self.cfg.get("consistency.chain_img2img", False):
            return None
        idx = ep.scenes.index(sc)
        if idx == 0:
            return None
        prev = ep.scenes[idx - 1]
        same_place = bool(sc.location) and prev.location == sc.location
        shared = set(prev.characters) & set(sc.characters)
        p = self.store.abs(project.id, prev.files.image)
        if same_place and (shared or (not sc.characters and not prev.characters)) and p and p.exists() and prev.status["image"] == "done":
            return p
        return None

    def stage_images(self, project: Project, ctx: JobContext, scene_ids=None, episodes=None, force: bool = False) -> None:
        self._log_sink(project, ctx)
        pairs = [(e, s) for e, s in self._scenes(project, scene_ids, episodes) if force or s.status["image"] != "done"
                 or not (s.files.image and self.store.abs(project.id, s.files.image).exists())]
        self._require_approved(project, pairs)
        if not pairs:
            ctx.log("Todas as imagens já existem")
            return
        ctx.plan(len(pairs))
        ctx.step("Validando o workflow de imagem no ComfyUI")
        info = self.comfy.object_info()
        wf, mapping = self._workflow("image")
        probs = validate_against_server(wf, info)
        wf_chain = map_chain = None
        if self.cfg.get("consistency.chain_img2img", False):
            wf_chain, map_chain = self._workflow("image_chain")
            probs += validate_against_server(wf_chain, info)
        if probs:
            raise StageError("O workflow de imagem não é compatível com o ComfyUI instalado:\n- " + "\n- ".join(probs))
        img = self.cfg.section("image")
        timeout = self.cfg.get("comfyui.timeout_image_s", 300)
        errors: list[str] = []
        for ep, sc in pairs:
            ctx.check()
            ctx.step(f"Imagem {sc.id} ({ctx.state.done + 1}/{len(pairs)})")
            values = {
                "positive_prompt": compose_image_prompt(project, sc), "seed": self._seed(project, ep, sc),
                "width": img.get("width", 768), "height": img.get("height", 1344), "steps": img.get("steps", 4),
                "filename_prefix": f"wif/{project.id}/{sc.id}",
            }
            sc.set_status("image", "running")
            self.store.save(project)
            try:
                ref = self._chain_reference(project, ep, sc)
                with VramSampler() as vs:
                    dest = self._ep_dir(project, ep) / "images" / f"{sc.id}"
                    if ref is not None:
                        values["reference_image"] = self.comfy.upload_image(ref)
                        values["denoise"] = float(self.cfg.get("consistency.chain_denoise", 0.72))
                        values.pop("width"); values.pop("height")  # o tamanho vem da imagem de referência
                        out = self.comfy.run(patch_workflow(wf_chain, map_chain, values), dest, timeout, cancel=ctx.check)
                        ctx.log(f"{sc.id}: continuidade a partir da imagem da cena anterior")
                    else:
                        out = self.comfy.run(patch_workflow(wf, mapping, values), dest, timeout, cancel=ctx.check)
                sc.files.image = self.store.rel(project.id, out)
                sc.files.image_hash = file_hash(out)
                self._drop_animation(project, sc)  # <- correção: nada de animação velha depois de uma imagem nova
                sc.metrics.update({f"image_{k}": v for k, v in vs.metrics().items()})
                sc.set_status("image", "done")
                ctx.log(f"{sc.id}: imagem ok em {vs.seconds:.1f}s" + (f", pico de VRAM {vs.peak} MB" if vs.peak else ""))
            except Cancelled:
                sc.set_status("image", "pending")
                self.store.save(project)
                raise
            except Exception as exc:  # noqa: BLE001 - erro por cena, registrado, as demais continuam
                sc.set_status("image", "error", f"{type(exc).__name__}: {exc}")
                errors.append(f"{sc.id}: {exc}")
                ctx.log(f"{sc.id}: ERRO {exc}")
            self.store.save(project)
            ctx.advance()
        if self.cfg.get("comfyui.free_memory_between_stages", True):
            self.comfy.free()
        self._finish_stage(errors, "Geração de imagens")

    # ---------------------------------------------------------------- 3. narração + alinhamento
    def _tts_backend(self):
        if self._tts is None:
            self._tts = make_backend(self.cfg)
        return self._tts

    def _get_aligner(self, ctx: JobContext) -> Aligner:
        if self._aligner is None:
            self._aligner = Aligner.from_config(self.cfg)
            ctx.log(f"Alinhamento de palavras: {self._aligner.active}" + (f" ({self._aligner.note})" if self._aligner.note else ""))
        return self._aligner

    def stage_audio(self, project: Project, ctx: JobContext, scene_ids=None, episodes=None, force: bool = False) -> None:
        self._log_sink(project, ctx)
        pairs = self._scenes(project, scene_ids, episodes)
        self._require_approved(project, pairs)
        todo = [(e, s) for e, s in pairs if force or s.status["audio"] != "done"
                or not (s.files.audio and self.store.abs(project.id, s.files.audio).exists())]
        if not todo:
            ctx.log("Toda a narração já existe")
            return
        ctx.plan(len(todo) + 1)
        backend = self._tts_backend()
        aligner = self._get_aligner(ctx)
        t = self.cfg.section("tts")
        voice, base_speed = project.settings.voice or t.get("voice", "pf_dora"), float(t.get("speed", 1.0))
        if hasattr(backend, "check_voice"):
            backend.check_voice(voice)  # falha cedo se a voz não for pt-BR
        errors: list[str] = []

        def synth_all(spd: float, items) -> None:
            for ep, sc in items:
                ctx.check()
                ctx.step(f"Narração {sc.id}")
                wav = self._ep_dir(project, ep) / "audio" / f"{sc.id}.wav"
                sc.set_status("audio", "running")
                try:
                    sc.spoken_text = normalize_pt(sc.narration)
                    dur = synth_to_wav(backend, sc.spoken_text, voice, spd, wav)
                    words = aligner.align_units(wav, display_units(sc.narration), dur)
                    dump_json(wav.with_suffix(".words.json"), words)
                    sc.files.audio = self.store.rel(project.id, wav)
                    sc.audio_duration = round(dur, 3)
                    sc.set_status("audio", "done")
                    sc.status["clip"] = "pending"
                except Cancelled:
                    sc.set_status("audio", "pending")
                    raise
                except Exception as exc:  # noqa: BLE001
                    sc.set_status("audio", "error", f"{type(exc).__name__}: {exc}")
                    errors.append(f"{sc.id}: {exc}")
                    ctx.log(f"{sc.id}: ERRO {exc}")
                self.store.save(project)
                ctx.advance()

        synth_all(base_speed, todo)
        # Modo ideia: a duração pedida é um alvo, então a voz acelera até tts.max_speed para caber.
        # Modo roteiro: o texto do usuário manda; a duração é a que ele escreveu (só avisamos se passar de 90 s).
        for ep in {e.number: e for e, _ in todo}.values():
            total = self._episode_duration(ep)
            if total is None:
                continue
            if project.settings.source == "ideia":
                target = project.settings.duration_s
                max_speed = float(t.get("max_speed", 1.15))
                if total > target * 1.05 and base_speed < max_speed:
                    new_speed = min(max_speed, round(base_speed * total / target, 2))
                    ctx.log(f"Episódio {ep.number}: narração com {total:.1f}s para alvo de {target}s. Refazendo a {new_speed}x")
                    ctx.plan(ctx.state.total + len(ep.scenes))
                    synth_all(new_speed, [(ep, s) for s in ep.scenes])
            elif total > 90:
                ctx.log(f"AVISO: o episódio {ep.number} tem {total:.0f}s de narração (acima de 90 s).")
        ctx.advance()
        self._finish_stage(errors, "Narração")

    def _pads(self, ep: Episode) -> list[float]:
        pad, last = self.cfg.get("tts.scene_pad_s", 0.35), self.cfg.get("tts.last_pad_s", 0.9)
        return [pad] * (len(ep.scenes) - 1) + [last]

    def scene_duration(self, ep: Episode, sc: Scene) -> Optional[float]:
        if sc.audio_duration is None:
            return None
        return max(2.0, sc.audio_duration + self._pads(ep)[ep.scenes.index(sc)])

    def _episode_duration(self, ep: Episode) -> Optional[float]:
        durs = [self.scene_duration(ep, s) for s in ep.scenes]
        return None if any(d is None for d in durs) else sum(durs)  # type: ignore[arg-type]

    # ---------------------------------------------------------------- 4. animação (modo completo)
    def _anim_length(self, ep: Episode, sc: Scene) -> int:
        """Quadros do Wan para esta cena: acompanha a duração da narração (4n+1), limitado por min/max."""
        a = self.cfg.section("animation")
        fps = a.get("fps", 24)
        d = self.scene_duration(ep, sc)
        if not a.get("fit_to_scene", True) or d is None:
            return int(a.get("length", 49))
        n = 4 * math.ceil((math.ceil(d * fps) - 1) / 4) + 1
        return int(min(max(n, a.get("min_length", 25)), a.get("max_length", 73)))

    def stage_animations(self, project: Project, ctx: JobContext, scene_ids=None, episodes=None, force: bool = False) -> None:
        self._log_sink(project, ctx)
        s = project.settings
        if scene_ids:
            pairs = self._scenes(project, scene_ids, episodes)
        elif s.animate_scope == "todas":
            pairs = self._scenes(project, None, episodes)
        else:
            pairs = [(e, sc) for e, sc in self._scenes(project, None, episodes) if sc.animate]
        self._require_approved(project, pairs)
        missing = [sc.id for _, sc in pairs if not (sc.files.image and self.store.abs(project.id, sc.files.image).exists())]
        if missing:
            raise StageError(f"Gere as imagens antes de animar. Sem imagem: {missing}")

        def fresh(sc: Scene) -> bool:  # animação existe E foi feita a partir da imagem ATUAL
            p = self.store.abs(project.id, sc.files.animation)
            return bool(p and p.exists() and sc.status["animation"] == "done"
                        and sc.files.animation_source == file_hash(self.store.abs(project.id, sc.files.image)))

        pairs = [(e, sc) for e, sc in pairs if force or not fresh(sc)]
        if not pairs:
            ctx.log("Nenhuma cena para animar (marque cenas no modo 'marcadas') ou todas já estão animadas com a imagem atual")
            return
        ctx.plan(len(pairs))
        ctx.step("Validando o workflow de animação no ComfyUI")
        wf, mapping = self._workflow("video")
        probs = validate_against_server(wf, self.comfy.object_info())
        if probs:
            raise StageError("O workflow de animação não é compatível com o ComfyUI instalado (o modo rápido continua "
                             "funcionando sem ele):\n- " + "\n- ".join(probs))
        if self.cfg.get("comfyui.free_memory_between_stages", True):
            self.comfy.free()  # tira o FLUX da VRAM antes de carregar o Wan
        a = self.cfg.section("animation")
        timeout = self.cfg.get("comfyui.timeout_video_s", 2400)
        errors: list[str] = []
        for ep, sc in pairs:
            ctx.check()
            ctx.step(f"Animando {sc.id} ({ctx.state.done + 1}/{len(pairs)})")
            sc.set_status("animation", "running")
            self.store.save(project)
            try:
                img_path = self.store.abs(project.id, sc.files.image)
                src_hash = file_hash(img_path)
                uploaded = self.comfy.upload_image(img_path)
                length = self._anim_length(ep, sc)
                values = {
                    "input_image": uploaded,
                    "prompt": f"{sc.animation_prompt}. {sc.camera_move.replace('_', ' ')} camera motion, smooth, cinematic",
                    "seed": s.seed + sc.seed_bump * 100003 + 17,
                    "width": a.get("width", 704), "height": a.get("height", 1280), "length": length,
                    "fps": a.get("fps", 24), "steps": a.get("steps", 20), "filename_prefix": f"wif/{project.id}/{sc.id}_anim",
                }
                with VramSampler() as vs:
                    dest = self._ep_dir(project, ep) / "animations" / sc.id
                    out = self.comfy.run(patch_workflow(wf, mapping, values), dest, timeout, cancel=ctx.check)
                sc.files.animation = self.store.rel(project.id, out)
                sc.files.animation_source = src_hash
                sc.metrics.update({f"animation_{k}": v for k, v in vs.metrics().items()})
                sc.metrics["animation_frames"] = float(length)
                sc.set_status("animation", "done")
                sc.status["clip"] = "pending"
                ctx.log(f"{sc.id}: animação ({length} quadros) ok em {vs.seconds:.0f}s" + (f", pico de VRAM {vs.peak} MB" if vs.peak else ""))
            except Cancelled:
                sc.set_status("animation", "pending")
                self.store.save(project)
                raise
            except Exception as exc:  # noqa: BLE001
                sc.set_status("animation", "error", f"{type(exc).__name__}: {exc}")
                errors.append(f"{sc.id}: {exc}")
                ctx.log(f"{sc.id}: ERRO {exc}")
            self.store.save(project)
            ctx.advance()
        if self.cfg.get("comfyui.free_memory_between_stages", True):
            self.comfy.free()
        self._finish_stage(errors, "Animações")

    # ---------------------------------------------------------------- 5. vídeo final
    def stage_render(self, project: Project, ctx: JobContext, episodes=None) -> None:
        self._log_sink(project, ctx)
        eps = [e for e in project.episodes if not episodes or e.number in set(episodes)]
        self._require_approved(project, [(e, s) for e in eps for s in e.scenes])
        for e in eps:  # narração faltante é gerada automaticamente
            if any(s.status["audio"] != "done" for s in e.scenes):
                ctx.log(f"Episódio {e.number}: gerando a narração que faltava")
                sub = JobContext(ctx.state, ctx._sink)
                sub._cancel = ctx._cancel
                self.stage_audio(project, sub, episodes=[e.number])
        for e in eps:
            miss = [s.id for s in e.scenes if not (s.files.image and self.store.abs(project.id, s.files.image).exists())]
            if miss:
                raise StageError(f"Episódio {e.number}: faltam imagens nas cenas {miss}. Gere as imagens antes do vídeo final.")
        ctx.state.total, ctx.state.done = sum(len(e.scenes) + 3 for e in eps), 0
        for ep in eps:
            self._render_episode(project, ep, ctx)

    def _render_episode(self, project: Project, ep: Episode, ctx: JobContext) -> None:
        r = self.cfg.section("render")
        root = self.store.dir(project.id)
        ed = self._ep_dir(project, ep)
        work = ed / "work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        durs = [round(self.scene_duration(ep, s) or 2.0, 3) for s in ep.scenes]
        starts = [sum(durs[:i]) for i in range(len(durs))]
        total = sum(durs)
        trans = float(r.get("transition_s", 0.18)) if r.get("transition", "fade") != "none" else 0.0
        ctx.step(f"Episódio {ep.number}: montando {len(ep.scenes)} clipes")
        clips: list[Path] = []
        for i, (sc, d) in enumerate(zip(ep.scenes, durs)):
            ctx.check()
            length = d + (trans if i < len(ep.scenes) - 1 else 0.0)
            img = self.store.abs(project.id, sc.files.image)
            anim = self.store.abs(project.id, sc.files.animation)
            use_anim = bool(anim and anim.exists() and sc.status["animation"] == "done"
                            and sc.files.animation_source == file_hash(img))  # só usa animação da imagem ATUAL
            if anim and anim.exists() and sc.status["animation"] == "done" and not use_anim:
                ctx.log(f"{sc.id}: a animação existente é de uma imagem antiga e foi IGNORADA (regenere a animação)")
            ctx.step(f"Clipe {sc.id} ({'animação IA' if use_anim else 'imagem com ' + sc.camera_move})")
            clip = render.render_scene_clip(anim if use_anim else img, work / f"{sc.id}.mp4", length, sc.camera_move, self.cfg,
                                            is_video=use_anim, cwd=root, log=ctx.log)
            sc.files.clip = self.store.rel(project.id, clip)
            sc.set_status("clip", "done")
            clips.append(clip)
            ctx.advance()
        ctx.step(f"Episódio {ep.number}: legendas")
        words = subs.timeline_words([(st, self._load_words(project, sc)) for st, sc in zip(starts, ep.scenes)])
        s = self.cfg.section("subtitles")
        events = subs.build_chunks(words, s.get("max_words_per_chunk", 4), s.get("max_chars_per_line", 18),
                                   s.get("uppercase", False), total)
        ass = work / "legendas.ass"
        subs.write_ass(events, ass, s, r.get("width", 1080), r.get("height", 1920), s.get("uppercase", False))
        srt = ed / f"ep{ep.number:02d}.srt"
        subs.write_srt(events, srt)
        ctx.advance()
        ctx.step(f"Episódio {ep.number}: efeitos sonoros e mixagem")
        music = Path(r["music_file"]) if r.get("music_file") else None
        placements = [{"start": st, "duration": d, "narration_wav": str(self.store.abs(project.id, sc.files.audio)), "sfx": sc.sfx}
                      for st, d, sc in zip(starts, durs, ep.scenes)]
        tone = (project.episode_outline[0].get("tone", "") if project.episode_outline else "") or project.settings.tone
        mix = sfx.mix_episode(placements, total, work / "mix.wav", music, r.get("music_volume", 0.10), r.get("sfx_volume", 0.35),
                              sfx_dir=self.cfg.root / "assets" / "sfx", bed_tone=tone if r.get("auto_bed", True) else None,
                              bed_volume=r.get("bed_volume", 0.07))
        ctx.advance()
        ctx.step(f"Episódio {ep.number}: exportando MP4 final")
        out = ed / f"{project.id}_ep{ep.number:02d}.mp4"
        render.assemble(clips, durs, mix, ass, out, self.cfg, root, ctx.log)
        target = project.settings.duration_s if project.settings.source == "ideia" else total
        report = render.verify_reel(out, target, tol=0.15 if project.settings.source == "ideia" else 0.05)
        ep.final_video = self.store.rel(project.id, out)
        ep.final_srt = self.store.rel(project.id, srt)
        ep.final_report = report
        dump_json(ed / "relatorio_verificacao.json", report)
        dest = self.cfg.path("paths.outputs_dir") / project.id
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dest / out.name)
        shutil.copy2(srt, dest / srt.name)
        self.store.save(project)
        ctx.advance()
        bad = [c for c in report["checks"] if not c["ok"]]
        ctx.log(f"Episódio {ep.number}: {report['duration']:.1f}s, {report['size_mb']} MB, "
                + ("todas as verificações passaram" if not bad else "FALHAS: " + "; ".join(f"{c['check']} ({c['detail']})" for c in bad)))
        if bad:
            raise StageError("O MP4 foi gerado mas falhou em: " + "; ".join(c["check"] for c in bad))

    def _load_words(self, project: Project, sc: Scene) -> list[dict]:
        p = self.store.abs(project.id, sc.files.audio).with_suffix(".words.json")
        return json.loads(p.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- tudo em um botão
    def run_all(self, project: Project, ctx: JobContext) -> None:
        """Plano -> imagens -> narração -> animações (modo completo) -> vídeo final. Idempotente: pula o que já está pronto.
        Modo supervisionado: para (pausa) depois de gerar o plano, as imagens e as animações, até o usuário aprovar."""
        self._log_sink(project, ctx)
        s = project.settings
        sup = s.review_mode == "supervisionado"
        animate = s.mode == "completo"
        total = 5 if animate else 4
        n = 0

        n += 1
        ctx.begin_stage(n, total, "Plano de cenas")
        if not project.episodes:
            self.stage_plan(project, ctx)
            project = self.store.load(project.id)
        else:
            ctx.log("Plano existente mantido")
        if not project.plan_approved:
            if sup:
                ctx.pause("Revise as cenas na aba Plano e clique em 'Aprovar plano'. Depois rode 'Gerar vídeo completo' de novo.")
            approve(project, self.store)
            ctx.log("Modo automático: plano aprovado automaticamente")

        n += 1
        ctx.begin_stage(n, total, "Imagens")
        self.stage_images(project, ctx)
        project = self.store.load(project.id)
        if sup and not project.images_approved:
            ctx.pause("Revise as imagens (regenere as que não gostar) e clique em 'Aprovar imagens'. Depois rode 'Gerar vídeo completo' de novo.")

        n += 1
        ctx.begin_stage(n, total, "Narração em português")
        self.stage_audio(project, ctx)
        project = self.store.load(project.id)

        if animate:
            n += 1
            ctx.begin_stage(n, total, "Animação das cenas (Wan 2.2)")
            self.stage_animations(project, ctx)
            project = self.store.load(project.id)
            if sup and not project.animations_approved:
                ctx.pause("Revise as animações e clique em 'Aprovar animações'. Depois rode 'Gerar vídeo completo' de novo.")

        n += 1
        ctx.begin_stage(n, total, "Edição e vídeo final")
        self.stage_render(project, ctx)

    # ---------------------------------------------------------------- regenerar uma cena
    def regenerate_scene(self, project: Project, scene_id: str, ctx: JobContext, what: str = "image",
                         new_seed: bool = True, reanimate: bool = False) -> None:
        """what: image | animation | audio. Troca a semente (para a imagem/animação mudar de verdade) e refaz só essa cena.
        Regenerar a imagem apaga a animação antiga dela; reanimate=True já cria a nova (modo completo)."""
        ep, sc = project.scene(scene_id)
        if new_seed and what in ("image", "animation"):
            sc.seed_bump += 1
        sc.status[what] = "pending"
        sc.status["clip"] = "pending"
        self.store.save(project)
        fn = {"image": self.stage_images, "animation": self.stage_animations, "audio": self.stage_audio}[what]
        fn(project, ctx, scene_ids=[scene_id], force=True)
        if what == "image" and reanimate:
            self.stage_animations(project, ctx, scene_ids=[scene_id], force=True)
