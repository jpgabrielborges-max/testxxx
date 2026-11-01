"""Orquestração das etapas. Cada função recebe um JobContext, reporta progresso real e salva o projeto a cada cena."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import render, sfx, subs
from .align import Aligner
from .comfy import ComfyClient, ComfyError, load_workflow, patch_workflow, validate_against_server
from .jobs import Cancelled, JobContext
from .llm import OllamaClient
from .planner import generate_script
from .preflight import VramSampler
from .project import ProjectStore, append_log, dump_json, project_log_path
from .prompts import compose_image_prompt
from .schemas import Episode, Project, Scene
from .tts import make_backend, synth_to_wav


class StageError(RuntimeError):
    pass


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
        out = []
        for e in project.episodes:
            if eps and e.number not in eps:
                continue
            for s in e.scenes:
                if ids is None or s.id in ids:
                    out.append((e, s))
        return out

    def _require_approved(self, project: Project, pairs: list[tuple[Episode, Scene]]) -> None:
        bad = sorted({e.number for e, _ in pairs if not e.approved})
        if bad:
            raise StageError(f"Aprove o roteiro do(s) episódio(s) {bad} antes de continuar (botão 'Editar e aprovar roteiro').")

    def _ep_dir(self, project: Project, ep: Episode) -> Path:
        return self.store.sub(project.id, "episodes", f"ep{ep.number:02d}")

    def _workflow(self, kind: str):
        path = self.cfg.path(f"comfyui.workflows.{kind}")
        return load_workflow(path)

    def _finish_stage(self, project: Project, errors: list[str], what: str) -> None:
        if errors:
            raise StageError(f"{what}: {len(errors)} cena(s) com erro. " + " | ".join(errors[:5]))

    # ---------------------------------------------------------------- 1. roteiro
    def stage_script(self, project: Project, ctx: JobContext) -> Project:
        ctx._sink = ctx._sink or self._sink(project)
        return generate_script(project, self.llm, self.store, self.cfg, ctx)

    # ---------------------------------------------------------------- 2. imagens
    def check_workflows(self, kinds=("image",)) -> list[str]:
        """Compara os workflows com o ComfyUI que está rodando. Lista vazia = tudo certo."""
        info = self.comfy.object_info()
        probs = []
        for k in kinds:
            wf, _ = self._workflow(k)
            probs += [f"[{k}] {p}" for p in validate_against_server(wf, info)]
        return probs

    def stage_images(self, project: Project, ctx: JobContext, scene_ids=None, episodes=None, force: bool = False) -> None:
        ctx._sink = ctx._sink or self._sink(project)
        pairs = [(e, s) for e, s in self._scenes(project, scene_ids, episodes) if force or s.status["image"] != "done"
                 or not (s.files.image and self.store.abs(project.id, s.files.image).exists())]
        self._require_approved(project, pairs)
        if not pairs:
            ctx.log("Todas as imagens já existem")
            return
        ctx.plan(len(pairs))
        ctx.step("Validando o workflow de imagem no ComfyUI")
        wf, mapping = self._workflow("image")
        probs = validate_against_server(wf, self.comfy.object_info())
        if probs:
            raise StageError("O workflow de imagem não é compatível com o ComfyUI instalado:\n- " + "\n- ".join(probs))
        img = self.cfg.section("image")
        timeout = self.cfg.get("comfyui.timeout_image_s", 300)
        errors: list[str] = []
        for ep, sc in pairs:
            ctx.check()
            ctx.step(f"Imagem {sc.id} ({ctx.state.done + 1}/{len(pairs)})")
            idx = ep.scenes.index(sc)
            seed = project.settings.seed + idx * 101 + sc.seed_bump * 100003
            values = {
                "positive_prompt": compose_image_prompt(project, sc), "seed": seed,
                "width": img.get("width", 768), "height": img.get("height", 1344), "steps": img.get("steps", 4),
                "filename_prefix": f"wif/{project.id}/{sc.id}",
            }
            sc.set_status("image", "running")
            self.store.save(project)
            try:
                with VramSampler() as vs:
                    dest = self._ep_dir(project, ep) / "images" / f"{sc.id}"
                    out = self.comfy.run(patch_workflow(wf, mapping, values), dest, timeout,
                                         output_node=None, cancel=ctx.check)
                sc.files.image = self.store.rel(project.id, out)
                sc.metrics.update({f"image_{k}": v for k, v in vs.metrics().items()})
                sc.set_status("image", "done")
                sc.status["clip"] = "pending"
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
        self._finish_stage(project, errors, "Geração de imagens")

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
        ctx._sink = ctx._sink or self._sink(project)
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
        errors: list[str] = []
        speed = base_speed

        def synth_all(spd: float, items) -> None:
            for ep, sc in items:
                ctx.check()
                ctx.step(f"Narração {sc.id}")
                wav = self._ep_dir(project, ep) / "audio" / f"{sc.id}.wav"
                sc.set_status("audio", "running")
                try:
                    dur = synth_to_wav(backend, sc.narration, voice, spd, wav)
                    words = aligner.align(wav, sc.narration, dur)
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

        synth_all(speed, todo)
        # Ajuste automático de velocidade para caber na duração pedida (limitado por tts.max_speed)
        for ep in {e.number: e for e, _ in todo}.values():
            total = self._episode_duration(ep)
            target = project.settings.duration_s
            if total is not None and total > target * 1.05 and speed < float(t.get("max_speed", 1.15)):
                new_speed = min(float(t.get("max_speed", 1.15)), round(speed * total / target, 2))
                ctx.log(f"Episódio {ep.number}: narração com {total:.1f}s para alvo de {target}s. Refazendo a {new_speed}x")
                ctx.plan(ctx.state.total + len(ep.scenes))
                synth_all(new_speed, [(ep, s) for s in ep.scenes])
        ctx.advance()
        self._finish_stage(project, errors, "Narração")

    def _pads(self, ep: Episode) -> list[float]:
        pad, last = self.cfg.get("tts.scene_pad_s", 0.35), self.cfg.get("tts.last_pad_s", 0.9)
        return [pad] * (len(ep.scenes) - 1) + [last]

    def _episode_duration(self, ep: Episode) -> Optional[float]:
        if any(s.audio_duration is None for s in ep.scenes):
            return None
        return sum(max(2.0, (s.audio_duration or 0) + p) for s, p in zip(ep.scenes, self._pads(ep)))

    # ---------------------------------------------------------------- 4. animação (modo completo)
    def stage_animations(self, project: Project, ctx: JobContext, scene_ids=None, episodes=None, force: bool = False) -> None:
        ctx._sink = ctx._sink or self._sink(project)
        pairs = [(e, s) for e, s in self._scenes(project, scene_ids, episodes) if s.animate or scene_ids]
        self._require_approved(project, pairs)
        missing = [s.id for _, s in pairs if not (s.files.image and self.store.abs(project.id, s.files.image).exists())]
        if missing:
            raise StageError(f"Gere as imagens antes de animar. Sem imagem: {missing}")
        pairs = [(e, s) for e, s in pairs if force or s.status["animation"] != "done"]
        if not pairs:
            ctx.log("Nenhuma cena selecionada para animar (marque 'animar' nas cenas) ou tudo já foi animado")
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
                uploaded = self.comfy.upload_image(self.store.abs(project.id, sc.files.image))
                values = {
                    "input_image": uploaded,
                    "prompt": f"{sc.animation_prompt}. {sc.camera_move.replace('_', ' ')} camera motion, smooth, cinematic",
                    "seed": project.settings.seed + sc.seed_bump * 100003 + 17,
                    "width": a.get("width", 704), "height": a.get("height", 1280), "length": a.get("length", 49),
                    "fps": a.get("fps", 24), "steps": a.get("steps", 20), "filename_prefix": f"wif/{project.id}/{sc.id}_anim",
                }
                with VramSampler() as vs:
                    dest = self._ep_dir(project, ep) / "animations" / sc.id
                    out = self.comfy.run(patch_workflow(wf, mapping, values), dest, timeout, cancel=ctx.check)
                sc.files.animation = self.store.rel(project.id, out)
                sc.metrics.update({f"animation_{k}": v for k, v in vs.metrics().items()})
                sc.set_status("animation", "done")
                sc.status["clip"] = "pending"
                ctx.log(f"{sc.id}: animação ok em {vs.seconds:.0f}s" + (f", pico de VRAM {vs.peak} MB" if vs.peak else ""))
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
        self._finish_stage(project, errors, "Animações")

    # ---------------------------------------------------------------- 5. vídeo final
    def stage_render(self, project: Project, ctx: JobContext, episodes=None) -> None:
        ctx._sink = ctx._sink or self._sink(project)
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
        total_units = sum(len(e.scenes) + 3 for e in eps)
        ctx.state.total, ctx.state.done = total_units, 0
        for ep in eps:
            self._render_episode(project, ep, ctx)

    def _render_episode(self, project: Project, ep: Episode, ctx: JobContext) -> None:
        r = self.cfg.section("render")
        root = self.store.dir(project.id)
        ed = self._ep_dir(project, ep)
        work = ed / "work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        pads = self._pads(ep)
        durs = [round(max(2.0, (s.audio_duration or 0) + p), 3) for s, p in zip(ep.scenes, pads)]
        starts = [sum(durs[:i]) for i in range(len(durs))]
        total = sum(durs)
        trans = float(r.get("transition_s", 0.18)) if r.get("transition", "fade") != "none" else 0.0
        ctx.step(f"Episódio {ep.number}: montando {len(ep.scenes)} clipes")
        clips: list[Path] = []
        for i, (sc, d) in enumerate(zip(ep.scenes, durs)):
            ctx.check()
            length = d + (trans if i < len(ep.scenes) - 1 else 0.0)
            use_anim = sc.files.animation and sc.status["animation"] == "done" and self.store.abs(project.id, sc.files.animation).exists()
            src = self.store.abs(project.id, sc.files.animation if use_anim else sc.files.image)
            ctx.step(f"Clipe {sc.id} ({'animação IA' if use_anim else 'imagem com ' + sc.camera_move})")
            clip = render.render_scene_clip(src, work / f"{sc.id}.mp4", length, sc.camera_move, self.cfg,
                                            is_video=bool(use_anim), cwd=root, log=ctx.log)
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
        ctx.step(f"Episódio {ep.number}: mixagem de áudio")
        music = Path(r["music_file"]) if r.get("music_file") else None
        placements = [{"start": st, "duration": d, "narration_wav": str(self.store.abs(project.id, sc.files.audio)), "sfx": sc.sfx}
                      for st, d, sc in zip(starts, durs, ep.scenes)]
        mix = sfx.mix_episode(placements, total, work / "mix.wav", music, r.get("music_volume", 0.10), r.get("sfx_volume", 0.35))
        ctx.advance()
        ctx.step(f"Episódio {ep.number}: exportando MP4 final")
        out = ed / f"{project.id}_ep{ep.number:02d}.mp4"
        render.assemble(clips, durs, mix, ass, out, self.cfg, root, ctx.log)
        report = render.verify_reel(out, project.settings.duration_s)
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
        import json

        p = self.store.abs(project.id, sc.files.audio).with_suffix(".words.json")
        return json.loads(p.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- regenerar uma cena
    def regenerate_scene(self, project: Project, scene_id: str, ctx: JobContext, what: str = "image",
                         new_seed: bool = True) -> None:
        """what: image | animation | audio. Troca a semente (para a imagem/animação mudar de verdade) e refaz só essa cena."""
        ep, sc = project.scene(scene_id)
        if new_seed and what in ("image", "animation"):
            sc.seed_bump += 1
        sc.status[what] = "pending"
        sc.status["clip"] = "pending"
        self.store.save(project)
        fn = {"image": self.stage_images, "animation": self.stage_animations, "audio": self.stage_audio}[what]
        fn(project, ctx, scene_ids=[scene_id], force=True)
