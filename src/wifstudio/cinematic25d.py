"""Exporta projetos existentes para uma timeline de atuação e câmera contínuas.

Módulo opcional: não modifica arquivos do pipeline de imagem/vídeo existente.
É um diretor por regras, não gera rigs automaticamente nem promete atuação sem assets.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ACTIONS = (
    (r"lanterna|flashlight|ilumin|apont", "lantern"),
    (r"assust|medo|p[aâ]nic|susto|temor|horror", "fear"),
    (r"olh|virou|virar|viu|perceb|observ|procur", "look"),
    (r"corr|fug|dispar|escap", "run"),
    (r"and|caminh|passo|march", "walk"),
    (r"peg|segur|levant|ergu", "reach"),
    (r"fal|diz|grit|pergunt|convers|responde", "talk"),
)
PALETTES = [
    {"skin": "#e9b188", "hair": "#623b2b", "clothes": "#d95752"},
    {"skin": "#d99b73", "hair": "#302b30", "clothes": "#69a7bd"},
    {"skin": "#b97f58", "hair": "#241d26", "clothes": "#e9bd52"},
    {"skin": "#f3c9a0", "hair": "#c09145", "clothes": "#7d85ca"},
]


def _slug(value: str) -> str:
    return (re.sub(r"[^a-zA-Z0-9_-]", "_", value)[:64] or "project")


def _choose_action(text: str) -> str:
    for pattern, action in ACTIONS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return action
    return "look"


def compile_story(scenes: list[dict], character_names: list[str] | None = None,
                  environment: str = "train_night", word_subtitles: list[dict] | None = None) -> dict:
    """Compila uma timeline determinística. Cada cena tem text, duration e characters opcionais.

    Os movimentos são heurísticos e editáveis no JSON; não há chamada externa ou GPU.
    """
    if not scenes:
        raise ValueError("Não há cenas para animar")
    if environment not in {"train_night", "forest_night", "town_day"}:
        raise ValueError("Cenário não suportado neste protótipo")
    names = list(dict.fromkeys(str(x).strip() for x in (character_names or []) if str(x).strip()))
    if not names:
        names = ["Personagem A", "Personagem B"]
    names = names[:4]
    actors = [{"id": f"actor_{i}", "name": name, "palette": PALETTES[i % len(PALETTES)]}
              for i, name in enumerate(names)]
    total, beats, subtitles, events = 0.0, [], [], []
    for index, scene in enumerate(scenes):
        text = str(scene.get("text", "")).strip()
        if not text:
            raise ValueError(f"Cena {index + 1} sem narração")
        duration = float(scene.get("duration", 4.0))
        if not (0.5 <= duration <= 120.0):
            raise ValueError(f"Duração inválida na cena {index + 1}")
        start = round(total, 3)
        target = scene.get("characters") or names
        active = [a for a in actors if a["name"] in target]
        if not active:
            active = actors[:1]
        choice = _choose_action(text + " " + str(scene.get("action_hint", "")))
        action_at = round(start + min(0.65, duration * .22), 3)
        event_duration = round(min(2.0, max(.5, duration * .7)), 3)
        events.append({"actor": active[0]["id"], "action": choice, "at": action_at, "duration": event_duration})
        if len(active) > 1:
            events.append({"actor": active[1]["id"], "action": "look", "at": round(action_at + .22, 3),
                           "duration": event_duration})
        beats.append({"start": start, "end": round(start + duration, 3), "text": text,
                      "action": choice, "actors": [a["id"] for a in active]})
        subtitles.append({"start": start, "end": round(start + duration, 3), "text": text})
        total += duration
    duration = round(total, 3)
    if word_subtitles is not None:
        subtitles = [{"start": float(w["start"]), "end": float(w["end"]), "text": str(w["text"])}
                     for w in word_subtitles if 0 <= float(w["start"]) < float(w["end"]) <= duration + .05]
    # Câmera fixa no mesmo mundo 3D, com dolly/pan suave e sem cortes entre as cenas.
    camera = [
        {"at": 0.0, "position": [0, 1.05, 16.3], "lookAt": [0, .4, 0], "fov": 37},
        {"at": round(duration * .38, 3), "position": [-1.2, .95, 12.7], "lookAt": [-.45, .4, 0], "fov": 36},
        {"at": round(duration * .76, 3), "position": [1.1, 1.05, 11.4], "lookAt": [.4, .47, 0], "fov": 35},
        {"at": duration, "position": [.45, 1.0, 13.2], "lookAt": [0, .48, 0], "fov": 36},
    ]
    return {"version": 1, "fps": 30, "duration": duration, "environment": environment,
            "actors": actors, "beats": beats, "events": events, "camera": camera, "subtitles": subtitles}


def from_project(project, episode, store, cfg) -> dict:
    scenes, names, all_words = [], [a.name for a in project.memory.characters], []
    if not names:
        names = list(dict.fromkeys(n for sc in episode.scenes for n in sc.characters))
    offset = 0.0
    pad = float(cfg.get("tts.scene_pad_s", .35))
    last = float(cfg.get("tts.last_pad_s", .9))
    for i, sc in enumerate(episode.scenes):
        duration = max(2.0, float(sc.audio_duration or sc.est_duration) + (last if i == len(episode.scenes)-1 else pad))
        scenes.append({"text": sc.narration, "duration": duration, "characters": sc.characters,
                       "action_hint": sc.animation_prompt})
        if sc.files.audio:
            a = store.abs(project.id, sc.files.audio)
            if a and a.with_suffix(".words.json").exists():
                for w in json.loads(a.with_suffix(".words.json").read_text(encoding="utf-8")):
                    all_words.append({"start": offset + float(w["s"]), "end": offset + float(w["e"]), "text": w["w"]})
        offset += duration
    universe = (project.memory.universe + " " + project.settings.script_text).lower()
    environment = "train_night" if "trem" in universe or "train" in universe else ("forest_night" if "floresta" in universe else "town_day")
    # Legendas de palavra se todos os áudios tiverem alinhamento; senão, usa a frase inteira por beat.
    subtitles = None
    if all_words and len(all_words) >= sum(len(s["text"].split()) for s in scenes) * .7:
        from .subs import build_chunks
        chunks = build_chunks([{"s": w["start"], "e": w["end"], "w": w["text"]} for w in all_words], 4, 23, False, offset)
        subtitles = [{"start": c["start"], "end": c["end"], "text": c["text"].replace(r"\N", "\n")} for c in chunks]
    return compile_story(scenes, names, environment, subtitles)


def prepare_render(project, episode, store, cfg, root: Path) -> tuple[Path, Path]:
    """Gera JSON de props e mix de áudio, sem alterar o projeto de vídeo original."""
    story = from_project(project, episode, store, cfg)
    stem = f"{_slug(project.id)}_ep{episode.number:02d}"
    output = store.sub(project.id, "cinematic25d")
    public = root / "cinematic25d" / "public" / "generated" / stem
    public.mkdir(parents=True, exist_ok=True)
    sound = None
    if all(sc.files.audio and store.abs(project.id, sc.files.audio).exists() for sc in episode.scenes):
        from .sfx import mix_episode
        starts = [b["start"] for b in story["beats"]]
        placements = [{"start": st, "duration": (b["end"]-b["start"]),
                       "narration_wav": str(store.abs(project.id, sc.files.audio)), "sfx": sc.sfx}
                      for st, b, sc in zip(starts, story["beats"], episode.scenes)]
        mix = mix_episode(placements, story["duration"], output / "mix.wav", None, .10, .35,
                          sfx_dir=cfg.root / "assets" / "sfx", bed_tone=None)
        shutil.copy2(mix, public / "mix.wav")
        sound = f"generated/{stem}/mix.wav"
    props = {"story": story, "audio": sound}
    path = output / "props.json"
    path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, output / f"{stem}.mp4"


def render_prepared(props: Path, out: Path, root: Path) -> Path:
    module = root / "cinematic25d"
    if not (module / "node_modules" / "@remotion" / "cli").exists():
        raise RuntimeError("Instale o motor uma vez: cd cinematic25d; npm install")
    import os
    command = "npx.cmd" if os.name == "nt" else "npx"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([command, "remotion", "render", "src/index.ts", "Cinematic25D", str(out),
                    "--props", str(props.resolve()), "--codec", "h264", "--crf", "18"], cwd=module, check=True)
    if not out.is_file():
        raise RuntimeError("Remotion não produziu o MP4 esperado")
    return out
