"""Modelos de dados validados com Pydantic. O que o LLM devolve e o que fica salvo em disco."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

CameraMove = Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
SfxName = Literal["whoosh", "impact", "rumble", "riser", "none"]
FactType = Literal["fato", "especulacao"]
Status = Literal["pending", "running", "done", "error", "skipped"]

VISUAL_STYLES = {
    "cinematografico": "cinematográfico realista",
    "animacao_3d": "animação 3D",
    "cartoon": "cartoon",
    "documental": "documental",
}
TONES = {
    "misterioso": "misterioso",
    "cientifico": "científico",
    "epico": "épico",
    "divertido": "divertido",
}

STYLE_SUFFIX = {
    "cinematografico": "cinematic photorealistic film still, 35mm, dramatic lighting, shallow depth of field, ultra detailed, vertical 9:16 composition",
    "animacao_3d": "stylized 3D animation render, pixar-like quality, soft global illumination, rich colors, clean shapes, vertical 9:16 composition",
    "cartoon": "flat 2D cartoon illustration, bold outlines, vibrant colors, simple shading, vertical 9:16 composition",
    "documental": "documentary photography, natural light, handheld realism, muted colors, candid framing, vertical 9:16 composition",
}


def slugify(text: str, max_len: int = 40) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t[:max_len].strip("-")) or "projeto"


class Settings(BaseModel):
    hypothesis: str = Field(min_length=3)
    duration_s: Literal[30, 45, 60] = 30
    episodes: Literal[1, 2, 4] = 1
    visual_style: Literal["cinematografico", "animacao_3d", "cartoon", "documental"] = "cinematografico"
    tone: Literal["misterioso", "cientifico", "epico", "divertido"] = "misterioso"
    voice: str = "pf_dora"
    style_reference: str = ""
    mode: Literal["rapido", "completo"] = "rapido"
    seed: int = 12345


class Character(BaseModel):
    name: str
    appearance: str


class Memory(BaseModel):
    """Bíblia visual e narrativa da série. É reaproveitada nos prompts de cada cena e episódio."""

    universe: str = ""
    characters: list[Character] = Field(default_factory=list)
    palette: str = ""
    locations: list[str] = Field(default_factory=list)
    lighting: str = ""
    camera_style: str = ""
    episode_recaps: list[str] = Field(default_factory=list)  # elementos importantes de episódios anteriores


class SceneFiles(BaseModel):
    image: Optional[str] = None
    animation: Optional[str] = None
    audio: Optional[str] = None
    clip: Optional[str] = None


class Scene(BaseModel):
    id: str
    episode: int
    narration: str
    est_duration: float = 4.0
    image_prompt: str
    animation_prompt: str = ""
    visual_style: str = "cinematografico"
    camera_move: CameraMove = "zoom_in"
    sfx: Optional[SfxName] = None
    fact_type: FactType = "fato"
    characters: list[str] = Field(default_factory=list)
    animate: bool = False  # selecionada para animação por IA no modo completo
    files: SceneFiles = Field(default_factory=SceneFiles)
    status: dict[str, Status] = Field(
        default_factory=lambda: {"image": "pending", "animation": "pending", "audio": "pending", "clip": "pending"}
    )
    seed_bump: int = 0
    audio_duration: Optional[float] = None
    error: Optional[str] = None
    metrics: dict[str, float] = Field(default_factory=dict)  # tempo e VRAM medidos

    def set_status(self, stage: str, value: Status, error: str | None = None) -> None:
        self.status[stage] = value
        self.error = error if value == "error" else (None if value == "done" else self.error)


class Episode(BaseModel):
    number: int
    title: str = ""
    hook: str = ""
    summary: str = ""
    next_hint: str = ""
    scenes: list[Scene] = Field(default_factory=list)
    approved: bool = False
    final_video: Optional[str] = None
    final_srt: Optional[str] = None
    final_report: dict = Field(default_factory=dict)


class Project(BaseModel):
    id: str
    created: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    settings: Settings
    series_title: str = ""
    memory: Memory = Field(default_factory=Memory)
    episode_outline: list[dict] = Field(default_factory=list)
    episodes: list[Episode] = Field(default_factory=list)

    def episode(self, number: int) -> Episode:
        for e in self.episodes:
            if e.number == number:
                return e
        raise KeyError(f"episódio {number} não existe")

    def scene(self, scene_id: str) -> tuple[Episode, Scene]:
        for e in self.episodes:
            for s in e.scenes:
                if s.id == scene_id:
                    return e, s
        raise KeyError(f"cena {scene_id} não existe")


# ---------- Esquemas devolvidos pelo LLM (validados e usados como JSON schema no Ollama) ----------


class LLMCharacter(BaseModel):
    name: str
    appearance: str = Field(description="aparência física detalhada, em inglês, reaproveitável nos prompts de imagem")


class LLMEpisodeOutline(BaseModel):
    number: int
    title: str
    premise: str
    ending_hint: str = Field(description="como o episódio termina e puxa o próximo, sem enganar o espectador")


class SeriesPlan(BaseModel):
    series_title: str
    universe: str = Field(description="descrição do universo visual, em inglês")
    characters: list[LLMCharacter] = Field(default_factory=list)
    palette: str = Field(description="paleta de cores, em inglês")
    locations: list[str] = Field(default_factory=list)
    lighting: str
    camera_style: str
    episodes: list[LLMEpisodeOutline]


class LLMScene(BaseModel):
    narration: str = Field(description="texto narrado em português do Brasil, números por extenso")
    image_prompt: str = Field(description="prompt visual detalhado em inglês, sem texto escrito na imagem")
    animation_prompt: str = Field(description="movimento da cena em inglês, curto")
    camera_move: CameraMove
    sfx: SfxName = "none"
    fact_type: FactType
    characters: list[str] = Field(default_factory=list)

    @field_validator("narration")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if len(v.split()) < 2:
            raise ValueError("narração curta demais")
        return v.strip()


class EpisodeDraft(BaseModel):
    title: str
    hook: str = Field(description="a primeira frase narrada, o gancho")
    summary: str = Field(description="resumo de 2 frases do que acontece neste episódio, para a memória da série")
    closing: str = Field(description="última frase narrada: consequência surpreendente ou pergunta relevante")
    scenes: list[LLMScene]
