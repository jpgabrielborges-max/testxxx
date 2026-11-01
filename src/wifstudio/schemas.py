"""Modelos de dados validados com Pydantic. O que o LLM devolve e o que fica salvo em disco."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

CameraMove = Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
SFX_NAMES = ["whoosh", "impact", "rumble", "riser", "heartbeat", "wind", "boom", "drone"]
SfxName = Literal["whoosh", "impact", "rumble", "riser", "heartbeat", "wind", "boom", "drone", "none"]
FactType = Literal["fato", "especulacao", "ficcao"]
Status = Literal["pending", "running", "done", "error", "skipped"]

# Estilos visuais: chave -> (rótulo para a interface, sufixo em inglês enviado ao modelo de imagem)
STYLE_PRESETS: dict[str, tuple[str, str]] = {
    "cinematografico": ("Cinematográfico realista", "cinematic photorealistic film still, 35mm, dramatic lighting, shallow depth of field, ultra detailed"),
    "animacao_3d": ("Animação 3D", "stylized 3D animation render, soft global illumination, rich colors, clean shapes"),
    "cartoon": ("Cartoon", "flat 2D cartoon illustration, bold outlines, vibrant colors, simple shading"),
    "documental": ("Documental", "documentary photography, natural light, handheld realism, muted colors, candid framing"),
    "anime": ("Anime", "anime key visual, detailed cel shading, expressive characters, painterly backgrounds, dynamic composition"),
    "terror_sombrio": ("Terror sombrio", "dark horror atmosphere, low-key lighting, deep shadows, desaturated cold palette, film grain, unsettling mood"),
    "pov": ("POV primeira pessoa", "first-person point of view shot, the camera is the narrator's eyes, hands visible in the foreground, immersive wide lens, cinematic realism"),
    "ilustracao": ("Ilustração de livro infantil / fantasia", "hand-painted storybook illustration, soft watercolor textures, warm colors, whimsical details"),
    "personalizado": ("Personalizado (você descreve)", ""),
}
VISUAL_STYLES = {k: v[0] for k, v in STYLE_PRESETS.items()}
STYLE_SUFFIX = {k: v[1] for k, v in STYLE_PRESETS.items()}

TONES = {
    "auto": "automático (deduzido do roteiro)", "misterioso": "misterioso", "cientifico": "científico", "epico": "épico",
    "divertido": "divertido", "sombrio": "sombrio e tenso", "emocionante": "emocionante",
}

# Dicas de gênero para o LLM. NÃO são roteiros nem limitam o gênero: "auto" deixa o modelo deduzir.
GENRES: dict[str, tuple[str, str]] = {
    "auto": ("Automático (deduzir do roteiro)", ""),
    "what_if": ("E se...?", "speculative science what-if scenario: show cause and consequence clearly, separate fact from speculation"),
    "apocalipse_zumbi": ("Apocalipse zumbi / sobrevivência", "post-apocalyptic survival: ruined streets, abandoned vehicles, scarce light, tension, desaturated palette, no graphic gore"),
    "pov": ("POV / primeira pessoa", "first-person narration: the viewer is the protagonist, hands and surroundings visible, the narrator's face never shown"),
    "animais": ("História de animais", "animal protagonists with expressive faces and clear anatomy, natural or domestic settings, warm emotional tone"),
    "crime": ("Crime / investigação", "noir investigation: dim practical lighting, evidence details, suspenseful framing, no graphic violence"),
    "documentario": ("Documentário", "documentary realism: real-looking locations, informative framing, archival and natural light feeling"),
    "ficcao": ("Ficção / aventura", "narrative fiction: consistent characters and settings, cinematic storytelling"),
    "terror": ("Terror", "horror suspense: implied threat, shadows, atmosphere over gore"),
    "comedia": ("Comédia", "light comedic tone: expressive exaggerated situations, bright readable composition"),
    "historia": ("História / biografia", "historical reconstruction: period-accurate clothing, architecture and props, respectful depiction"),
}


def slugify(text: str, max_len: int = 40) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t[:max_len].strip("-")) or "projeto"


class Settings(BaseModel):
    source: Literal["roteiro", "ideia"] = "ideia"          # roteiro = texto do usuário, preservado; ideia = o LLM escreve
    script_text: str = ""                                  # roteiro do usuário (verbatim)
    hypothesis: str = ""                                   # ideia/tema (modo ideia) ou título opcional (modo roteiro)
    duration_s: Literal[30, 45, 60] = 30                   # só no modo ideia; no modo roteiro a duração vem do texto
    episodes: Literal[1, 2, 4] = 1                         # só no modo ideia; no modo roteiro vem dos marcadores
    visual_style: str = "cinematografico"
    custom_style: str = ""
    genre: str = "auto"
    tone: str = "auto"
    voice: str = "pf_dora"
    style_reference: str = ""
    mode: Literal["rapido", "completo"] = "rapido"
    animate_scope: Literal["todas", "marcadas"] = "todas"  # no modo completo
    review_mode: Literal["automatico", "supervisionado"] = "automatico"
    seed: int = 12345

    @field_validator("visual_style")
    @classmethod
    def _style_ok(cls, v: str) -> str:
        if v not in STYLE_PRESETS:
            raise ValueError(f"estilo desconhecido: {v}. Opções: {', '.join(STYLE_PRESETS)}")
        return v

    @field_validator("genre")
    @classmethod
    def _genre_ok(cls, v: str) -> str:
        if v not in GENRES:
            raise ValueError(f"gênero desconhecido: {v}. Opções: {', '.join(GENRES)}")
        return v

    @field_validator("tone")
    @classmethod
    def _tone_ok(cls, v: str) -> str:
        if v not in TONES:
            raise ValueError(f"tom desconhecido: {v}. Opções: {', '.join(TONES)}")
        return v

    @model_validator(mode="after")
    def _source_ok(self):
        if self.source == "ideia" and len(self.hypothesis.strip()) < 3:
            raise ValueError("Digite uma ideia/hipótese para o modo 'criar roteiro a partir de uma ideia'.")
        if self.source == "roteiro" and len(self.script_text.split()) < 3:
            raise ValueError("Cole o seu roteiro (ao menos algumas palavras) no modo 'roteiro pronto'.")
        if self.visual_style == "personalizado" and not self.custom_style.strip():
            raise ValueError("Descreva o estilo personalizado (em inglês funciona melhor).")
        return self

    @property
    def title_hint(self) -> str:
        return self.hypothesis.strip() or " ".join(self.script_text.split()[:8])


class Character(BaseModel):
    name: str
    appearance: str


class Location(BaseModel):
    name: str
    description: str


class Memory(BaseModel):
    """Bíblia visual e narrativa. Reaproveitada nos prompts de cada cena e episódio (continuidade)."""

    universe: str = ""
    characters: list[Character] = Field(default_factory=list)
    palette: str = ""
    locations: list[Location] = Field(default_factory=list)
    lighting: str = ""
    camera_style: str = ""
    style_notes: str = ""
    episode_recaps: list[str] = Field(default_factory=list)

    @field_validator("locations", mode="before")
    @classmethod
    def _old_locations(cls, v):  # projetos salvos antes guardavam strings
        return [{"name": x, "description": x} if isinstance(x, str) else x for x in (v or [])]


class SceneFiles(BaseModel):
    image: Optional[str] = None
    image_hash: Optional[str] = None          # sha1 da imagem atual
    animation: Optional[str] = None
    animation_source: Optional[str] = None    # sha1 da imagem que originou a animação (se diferente da atual, a animação está velha)
    audio: Optional[str] = None
    clip: Optional[str] = None


class Scene(BaseModel):
    id: str
    episode: int
    narration: str                       # texto EXATO do roteiro do usuário (ou do LLM, no modo ideia). Legendas usam este texto.
    original_narration: str = ""         # texto como foi criado; se narration != isto, o usuário editou de propósito
    spoken_text: str = ""                # versão para a voz (números por extenso etc.)
    est_duration: float = 4.0
    image_prompt: str
    animation_prompt: str = ""
    visual_style: str = "cinematografico"
    camera_move: CameraMove = "zoom_in"
    sfx: Optional[SfxName] = None
    fact_type: FactType = "fato"
    characters: list[str] = Field(default_factory=list)
    location: str = ""
    notes: list[str] = Field(default_factory=list)   # direções [entre colchetes] do roteiro do usuário
    animate: bool = False
    files: SceneFiles = Field(default_factory=SceneFiles)
    status: dict[str, Status] = Field(
        default_factory=lambda: {"image": "pending", "animation": "pending", "audio": "pending", "clip": "pending"}
    )
    seed_bump: int = 0
    audio_duration: Optional[float] = None
    error: Optional[str] = None
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _spoken(self):
        if not self.original_narration:
            self.original_narration = self.narration
        if not self.spoken_text:
            from .textnorm import normalize_pt
            self.spoken_text = normalize_pt(self.narration)
        return self

    def set_status(self, stage: str, value: Status, error: str | None = None) -> None:
        self.status[stage] = value
        self.error = error if value == "error" else (None if value == "done" else self.error)


class Episode(BaseModel):
    number: int
    title: str = ""
    hook: str = ""
    summary: str = ""
    next_hint: str = ""
    source_text: str = ""        # trecho do roteiro do usuário que originou este episódio (verbatim)
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
    images_approved: bool = False
    animations_approved: bool = False

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

    @property
    def plan_approved(self) -> bool:
        return bool(self.episodes) and all(e.approved for e in self.episodes)


# ---------- Esquemas devolvidos pelo LLM (validados e usados como JSON schema no Ollama) ----------


class LLMCharacter(BaseModel):
    name: str
    appearance: str = Field(description="aparência física detalhada, em inglês, reaproveitável nos prompts de imagem")


class LLMLocation(BaseModel):
    name: str
    description: str = Field(description="descrição visual do cenário, em inglês")


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
    locations: list[LLMLocation] = Field(default_factory=list)
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
    location: str = ""

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


class EpisodeSummary(BaseModel):
    number: int
    title: str
    summary: str = Field(description="resumo curto, em português, do que acontece (memória de continuidade)")
    ending_hint: str = Field(default="", description="como termina e o que o próximo episódio retoma, em português")


class ScriptAnalysis(BaseModel):
    """Interpretação do roteiro do usuário: só descreve, nunca reescreve."""

    series_title: str
    genre: str = Field(description="gênero deduzido, em português, livre")
    tone: str = Field(description="tom deduzido, em português")
    summary: str
    universe: str = Field(description="universo visual, em inglês")
    characters: list[LLMCharacter] = Field(default_factory=list)
    locations: list[LLMLocation] = Field(default_factory=list)
    palette: str = Field(description="paleta de cores, em inglês")
    lighting: str
    camera_style: str
    style_notes: str = Field(default="", description="observações de estilo em inglês, úteis a todos os prompts")
    episodes: list[EpisodeSummary]


class SceneVisual(BaseModel):
    index: int = Field(description="o mesmo número do trecho recebido")
    image_prompt: str = Field(description="prompt visual detalhado em inglês, sem texto escrito na imagem")
    animation_prompt: str = Field(description="movimento da cena em inglês, curto")
    camera_move: CameraMove
    sfx: SfxName = "none"
    fact_type: FactType = "ficcao"
    characters: list[str] = Field(default_factory=list, description="nomes EXATOS da lista de personagens")
    location: str = Field(default="", description="nome EXATO de um cenário da lista, ou vazio")


class SceneVisualBatch(BaseModel):
    scenes: list[SceneVisual]
