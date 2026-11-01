"""Prompts do roteirista. Inspirados na estrutura da skill de referência (uma frase = um corte, gancho no início,
ritmo de vídeo curto) mas escritos para pt-BR e para saída JSON validada."""
from __future__ import annotations

import json

from .schemas import GENRES, SFX_NAMES, STYLE_SUFFIX, TONES, VISUAL_STYLES, Memory, Project, Scene, Settings

SYSTEM_COMMON = """Você é roteirista de vídeos verticais curtos do formato "E se...?" para Instagram Reels.
Escreva SEMPRE a narração em português do Brasil, em linguagem falada e natural, frases curtas, uma ideia por frase.
Regras de roteiro:
- A primeira frase é um gancho forte e HONESTO: uma hipótese intrigante em até 12 palavras. Nada de promessas que o vídeo não cumpre.
- Apresente as consequências aos poucos, da mais intuitiva para a mais surpreendente. Sem explicações longas ou repetidas.
- Distinga fato de especulação: marque cada cena como "fato" (ciência estabelecida) ou "especulacao". Em cenas de especulação use palavras como "provavelmente", "em teoria", "talvez", "os cientistas acreditam".
- Não invente números. Se citar um número, use apenas valores conhecidos e arredondados, e escreva-os POR EXTENSO na narração ("nove vírgula oito metros por segundo ao quadrado").
- O final traz uma consequência surpreendente ou uma pergunta relevante. Sem clickbait enganoso.
- Cada cena tem uma narração curta (uma ou duas frases) que cabe em 3 a 6 segundos.
Regras dos prompts de imagem (SEMPRE em inglês, para o modelo de imagem):
- Descreva sujeito, cenário, ação, iluminação, lente e clima, em uma frase rica de 30 a 60 palavras.
- Nunca peça texto escrito, letras, legendas, logotipos, marcas, pessoas reais famosas ou personagens protegidos.
- Reaproveite o universo visual, a paleta e as aparências dos personagens fornecidos para manter consistência.
- Composição vertical 9:16, sujeito bem enquadrado, espaço livre no terço inferior para legendas.
Responda apenas com JSON válido no schema pedido."""


def style_name(s: Settings) -> str:
    return s.custom_style.strip() if s.visual_style == "personalizado" else VISUAL_STYLES[s.visual_style]


def genre_line(s: Settings) -> str:
    label, hint = GENRES[s.genre]
    return f"Gênero: {label}." + (f" Orientação visual: {hint}." if hint else " Deduza o gênero do próprio texto.")


def style_line(s: Settings) -> str:
    ref = ""
    if s.style_reference.strip():
        ref = (f"\nReferência de estilo do usuário (analise apenas estrutura, ritmo e clima; NÃO copie conteúdo, "
               f"falas ou identidade visual de terceiros): {s.style_reference.strip()}")
    return f"Estilo visual: {style_name(s)}. Tom: {TONES[s.tone]}. {genre_line(s)}{ref}"


def scene_budget(duration_s: int, wps: float, pad: float, last_pad: float) -> tuple[int, int]:
    """Quantidade de cenas e de palavras narradas que cabem na duração pedida."""
    n_scenes = max(5, round(duration_s / 4.3))
    speech_s = duration_s - n_scenes * pad - (last_pad - pad)
    return n_scenes, max(20, round(speech_s * wps))


def plan_prompt(s: Settings) -> tuple[str, str]:
    user = f"""Hipótese: "{s.hypothesis}"
{style_line(s)}
Número de episódios: {s.episodes}.

Crie a bíblia da série e o plano de episódios.
- series_title: título curto e chamativo.
- universe, palette, lighting, camera_style, characters e locations (cada cenário com name e description): em inglês, para guiar um modelo de imagem. Se a hipótese não precisa de personagens, devolva lista vazia ou poucos personagens genéricos.
- episodes: exatamente {s.episodes} item(ns), numerados a partir de 1. Cada um com premissa e como termina. Todo episódio, exceto o último, termina com um gancho que o episódio seguinte realmente cumpre."""
    return SYSTEM_COMMON, user


def episode_prompt(project: Project, outline: dict, n_scenes: int, words: int, tolerance: float) -> tuple[str, str]:
    s = project.settings
    mem = project.memory
    lo, hi = round(words * (1 - tolerance)), round(words * (1 + tolerance))
    is_last = outline["number"] == s.episodes
    series_note = ""
    if s.episodes > 1:
        series_note = (f"\nPlano de todos os episódios: {json.dumps(project.episode_outline, ensure_ascii=False)}"
                       f"\nO que já aconteceu nos episódios anteriores: {json.dumps(mem.episode_recaps, ensure_ascii=False)}"
                       f"\nEste é o episódio {outline['number']} de {s.episodes}. "
                       + ("Feche a história com uma conclusão satisfatória." if is_last
                          else "Termine com um encerramento que desperte vontade de ver o próximo, sem enganar."))
    user = f"""Hipótese da série: "{s.hypothesis}"
{style_line(s)}
Bíblia visual (reaproveite nos prompts de imagem): {mem.model_dump_json()}{series_note}

Escreva o episódio {outline['number']}: "{outline['title']}". Premissa: {outline['premise']}. Finalização desejada: {outline['ending_hint']}.
Duração alvo do vídeo: {s.duration_s} segundos.
- Exatamente {n_scenes} cenas.
- Entre {lo} e {hi} palavras narradas no TOTAL (somando todas as cenas).
- A narração da primeira cena é o gancho. A da última cena é o fechamento.
- Varie camera_move entre as cenas. Use sfx com parcimônia (no máximo em metade das cenas), apenas quando houver ação clara.
- Em cada cena informe 'location' (nome EXATO de um cenário da bíblia) e 'characters' (nomes EXATOS da bíblia)."""
    return SYSTEM_COMMON, user


def compose_image_prompt(project: Project, scene: Scene) -> str:
    """Prompt final enviado ao ComfyUI: cena + personagens presentes + cenário + bíblia visual + estilo. A repetição das
    mesmas descrições em todas as cenas é o que sustenta a consistência (não há garantia de perfeição)."""
    mem: Memory = project.memory
    st = project.settings
    parts = [scene.image_prompt.strip().rstrip(".")]
    for c in mem.characters:
        if c.name in scene.characters:
            parts.append(f"{c.name}: {c.appearance}")
    for loc in mem.locations:
        if loc.name == scene.location:
            parts.append(f"setting: {loc.description}")
    if mem.universe:
        parts.append(mem.universe)
    if mem.palette:
        parts.append(f"color palette: {mem.palette}")
    if mem.lighting:
        parts.append(f"lighting: {mem.lighting}")
    if mem.camera_style:
        parts.append(f"camera: {mem.camera_style}")
    if mem.style_notes:
        parts.append(mem.style_notes)
    suffix = st.custom_style.strip() if st.visual_style == "personalizado" else STYLE_SUFFIX[st.visual_style]
    parts.append(suffix)
    parts.append("vertical 9:16 composition, no text, no letters, no watermark")
    return ", ".join(p for p in parts if p)


# ---------------------------------------------------------------- roteiro fornecido pelo usuário

SYSTEM_ANALYZE = """Você é diretor de arte e roteirista de vídeos verticais curtos. Você recebe um roteiro JÁ ESCRITO pelo usuário.
REGRA ABSOLUTA: você NÃO escreve, NÃO corrige e NÃO altera a narração. Seu trabalho é só interpretar o roteiro e descrever o visual.
Qualquer gênero é válido (terror, documentário, ficção, animais, crime, POV, apocalipse zumbi, e se...?, comédia, etc.).
Regras dos prompts visuais (SEMPRE em inglês, para o modelo de imagem):
- Descreva sujeito, cenário, ação, iluminação, lente e clima, em uma frase rica de 30 a 60 palavras.
- Nunca peça texto escrito, letras, legendas, logotipos, marcas, pessoas reais famosas ou personagens protegidos de terceiros.
- Sem gore explícito: sugira a ameaça pelo ambiente, sombra e reação, nunca por ferimentos detalhados.
- Reaproveite SEMPRE as descrições exatas de personagens e cenários da bíblia visual para manter consistência.
- Composição vertical 9:16, sujeito bem enquadrado, espaço livre no terço inferior para legendas.
- No estilo POV, o narrador nunca aparece de frente: mostre mãos, objetos e o que ele vê.
Responda apenas com JSON válido no schema pedido."""


def analysis_prompt(project: Project, episodes: list) -> tuple[str, str]:
    s = project.settings
    eps_txt = "\n\n".join(f"=== EPISÓDIO {e.number}{(': ' + e.title) if e.title else ''} ===\n{e.body}" for e in episodes)
    user = f"""{style_line(s)}
Título/ideia do usuário (opcional): {s.hypothesis or '(nenhum)'}

ROTEIRO DO USUÁRIO (não altere nada dele):
{eps_txt}

Interprete o roteiro e devolva a bíblia da série:
- series_title, genre, tone e summary em português. Se o usuário não pediu tom/gênero, deduza.
- universe, palette, lighting, camera_style, style_notes, characters (name + appearance) e locations (name + description): em inglês.
  Crie personagens e cenários SOMENTE para o que o roteiro realmente menciona ou exige. Dê a cada personagem uma aparência concreta e fixa
  (idade, cabelo, roupa, traço marcante). Animais e objetos recorrentes também são personagens.
- episodes: exatamente {len(episodes)} item(ns), com number, title (curto), summary e ending_hint em português."""
    return SYSTEM_ANALYZE, user


def visuals_prompt(project: Project, batch: list[dict], prev: dict | None) -> tuple[str, str]:
    """batch = [{index, text, notes}] — textos EXATOS do usuário. O LLM devolve só o visual de cada índice."""
    s, mem = project.settings, project.memory
    names = [c.name for c in mem.characters]
    locs = [l.name for l in mem.locations]
    prev_txt = (f"\nCena anterior: cenário={prev.get('location')!r}, personagens={prev.get('characters')}, visual={prev.get('image_prompt', '')[:200]!r}"
                if prev else "")
    user = f"""{style_line(s)}
Bíblia visual: {mem.model_dump_json()}
Personagens permitidos (use os nomes EXATOS, ou lista vazia): {names}
Cenários permitidos (use o nome EXATO, ou vazio): {locs}
Efeitos sonoros permitidos: {SFX_NAMES + ['none']}. Use com parcimônia, só quando a narração indicar uma ação sonora clara (porta, vento, explosão, coração acelerado, passos).{prev_txt}

Trechos do roteiro (NÃO reescreva). Para CADA índice devolva image_prompt, animation_prompt, camera_move, sfx, fact_type, characters e location.
fact_type: "fato" (informação real), "especulacao" (hipótese) ou "ficcao" (história).
Varie camera_move. Mantenha continuidade com a cena anterior quando o cenário for o mesmo.
{json.dumps(batch, ensure_ascii=False)}"""
    return SYSTEM_ANALYZE, user
