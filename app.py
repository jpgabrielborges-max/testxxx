"""What If Local Studio: Content Machine local. Roteiro pronto (ou ideia) -> vídeo final, tudo gratuito e local.
Rode com:  streamlit run app.py   (ou .\\run.ps1)"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from wifstudio.config import load_config  # noqa: E402
from wifstudio.jobs import JobRunner  # noqa: E402
from wifstudio.pipeline import Studio  # noqa: E402
from wifstudio.planner import apply_edits, approve, edited_scenes  # noqa: E402
from wifstudio.preflight import run_checks  # noqa: E402
from wifstudio.project import append_log, project_log_path, tail_log  # noqa: E402
from wifstudio.schemas import GENRES, SFX_NAMES, TONES, VISUAL_STYLES, Settings  # noqa: E402
from wifstudio.script import split_episodes  # noqa: E402
from wifstudio.tts import KOKORO_PT_VOICES  # noqa: E402

st.set_page_config(page_title="What If Local Studio", page_icon="🎬", layout="wide")


@st.cache_resource
def _boot():
    cfg = load_config()
    return cfg, Studio(cfg), JobRunner()


cfg, studio, runner = _boot()
store = studio.store
CAMERAS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
SFX = [""] + SFX_NAMES
ICON = {"pending": "⚪", "running": "🟡", "done": "🟢", "error": "🔴", "skipped": "⚫"}
SRC_LABEL = {"roteiro": "Colar meu roteiro (preservado palavra por palavra)", "ideia": "Criar roteiro a partir de uma ideia"}

st.session_state.setdefault("pid", None)
st.session_state.setdefault("seen_job", None)


def current_project():
    pid = st.session_state.pid
    if not pid:
        return None
    try:
        return store.load(pid)
    except FileNotFoundError:
        st.session_state.pid = None
        return None


def launch(name, fn, pid):
    """Dispara um job em segundo plano. fn(project, ctx). O projeto é recarregado do disco no início do job."""
    if runner.busy:
        st.error("Já existe uma tarefa em andamento. Aguarde ou cancele.")
        return
    proj = store.load(pid)
    ctx = runner.start(name, lambda c: fn(proj, c), log_sink=lambda m: append_log(project_log_path(store, pid), m))
    st.session_state.seen_job = id(ctx.state)
    st.rerun()


# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.title("🎬 What If Local Studio")
    st.caption("Content Machine local. Sem APIs pagas, sem créditos.")
    if st.button("➕ Novo projeto", use_container_width=True, disabled=runner.busy):
        st.session_state.pid = None
        st.rerun()
    projs = store.list()
    if projs:
        labels = {p["id"]: f"{p['title'][:38]} ({p['created'][:10]})" for p in projs}
        ids = list(labels)
        idx = ids.index(st.session_state.pid) if st.session_state.pid in ids else None
        choice = st.selectbox("Projetos salvos", ids, index=idx, format_func=labels.get, placeholder="Abrir projeto...", disabled=runner.busy)
        if choice and choice != st.session_state.pid:
            st.session_state.pid = choice
            st.rerun()
    st.divider()
    with st.expander("Verificação do ambiente", expanded=False):
        if st.button("Verificar agora"):
            st.session_state.checks = run_checks(cfg)
        for c in st.session_state.get("checks", []):
            st.write(f"{'✅' if c['ok'] else '❌'} **{c['name']}**: {c['detail']}")
    if st.session_state.pid and st.button("📂 Abrir pasta do projeto", use_container_width=True):
        try:
            st.toast(f"Aberta: {store.open_folder(st.session_state.pid)}")
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Não consegui abrir o explorador ({exc}). Pasta: {store.dir(st.session_state.pid).resolve()}")

proj = current_project()


# ------------------------------------------------------------------ progresso REAL


def _progress_body():
    ctx = runner.current
    if ctx is None:
        return
    s = ctx.state
    with st.container(border=True):
        if s.status == "running":
            c1, c2 = st.columns([5, 1])
            stage = f"Etapa {s.stage_index}/{s.stage_count} · {s.stage} · " if s.stage_count else ""
            c1.progress(s.fraction, text=f"{stage}{s.step} ({s.done}/{s.total})")
            if c2.button("Cancelar", key="cancel_job"):
                ctx.cancel()
        elif s.status == "done":
            st.success(f"{s.name}: concluído em {(s.finished or time.time()) - s.started:.0f}s")
        elif s.status == "paused":
            st.info(f"⏸️ Pausado para a sua revisão: {s.message}")
        elif s.status == "cancelled":
            st.warning(f"{s.name}: cancelado")
        else:
            st.error(f"{s.name} falhou: {s.error}")
        with st.expander("Logs", expanded=s.status == "error"):
            st.code("\n".join(s.logs[-80:]) or "(vazio)", language=None)
    if s.status != "running" and st.session_state.seen_job == id(s):
        st.session_state.seen_job = None
        st.rerun()


if runner.busy or st.session_state.seen_job:
    st.fragment(run_every=1.0)(_progress_body)()
elif runner.current is not None:
    _progress_body()

busy = runner.busy


def style_inputs(s0: Settings | None):
    """Campos comuns aos dois modos. Devolve um dict com os valores escolhidos."""
    a, b, c = st.columns(3)
    styles = list(VISUAL_STYLES)
    style = a.selectbox("Estilo visual", styles, format_func=VISUAL_STYLES.get, index=styles.index(s0.visual_style) if s0 else 0)
    custom = a.text_input("Descreva o estilo (em inglês funciona melhor)", value=s0.custom_style if s0 else "") if style == "personalizado" else ""
    genres = list(GENRES)
    genre = b.selectbox("Gênero", genres, format_func=lambda g: GENRES[g][0], index=genres.index(s0.genre) if s0 else 0,
                        help="'Automático' deixa o modelo deduzir pelo texto. Serve qualquer gênero.")
    tones = list(TONES)
    tone = c.selectbox("Tom", tones, format_func=TONES.get, index=tones.index(s0.tone) if s0 else 0)
    a, b, c = st.columns(3)
    if cfg.get("tts.backend", "kokoro") == "kokoro":
        vl = list(KOKORO_PT_VOICES)
        voice = a.selectbox("Voz de narração (pt-BR)", vl, format_func=lambda v: f"{v} ({KOKORO_PT_VOICES[v]})",
                            index=vl.index(s0.voice) if s0 and s0.voice in vl else 0)
    else:
        voice = a.text_input("Voz (ignorada pelo backend atual)", value=s0.voice if s0 else "")
    review = b.radio("Modo", ["automatico", "supervisionado"], horizontal=True, index=["automatico", "supervisionado"].index(s0.review_mode) if s0 else 0,
                     format_func=lambda x: "Automático (tudo de uma vez)" if x == "automatico" else "Supervisionado (você aprova cada etapa)")
    mode = c.radio("Animação", ["rapido", "completo"], horizontal=True, index=["rapido", "completo"].index(s0.mode) if s0 else 0,
                   format_func=lambda x: "Rápido (movimento de câmera)" if x == "rapido" else "Completo (Wan 2.2 anima as cenas)")
    scope = "todas"
    if mode == "completo":
        scope = st.radio("Quais cenas animar?", ["todas", "marcadas"], horizontal=True,
                         index=["todas", "marcadas"].index(s0.animate_scope) if s0 else 0,
                         format_func=lambda x: "Todas" if x == "todas" else "Só as marcadas na aba Animações")
    ref = st.text_input("Referência de estilo (opcional)", value=s0.style_reference if s0 else "",
                        help="Descreva ritmo, clima ou estrutura. Não cole conteúdo de terceiros.")
    return dict(visual_style=style, custom_style=custom, genre=genre, tone=tone, voice=voice, review_mode=review, mode=mode,
                animate_scope=scope, style_reference=ref)


# ------------------------------------------------------------------ novo projeto
if proj is None:
    st.subheader("Novo vídeo")
    source = st.radio("Como você quer começar?", list(SRC_LABEL), format_func=SRC_LABEL.get, horizontal=True)
    common_base = {}
    if source == "roteiro":
        script = st.text_area("Cole o seu roteiro", height=280, key="script_in",
                              placeholder="Cole aqui o roteiro pronto (qualquer gênero). O texto é usado exatamente como você escreveu.\n"
                                          "Use --- ou 'Parte 2' para separar episódios e [direções entre colchetes] para dicas visuais.")
        title = st.text_input("Título (opcional)", key="title_in")
        n_words = len(script.split())
        eps = split_episodes(script) if script.strip() else []
        if n_words:
            st.caption(f"{n_words} palavras · {len(eps)} episódio(s) · ~{n_words / cfg.get('narration.words_per_second', 2.5):.0f}s de narração. "
                       "Cada cena recebe imagem, narração e legenda com o SEU texto.")
        common_base = dict(source="roteiro", script_text=script, hypothesis=title)
    else:
        idea = st.text_area("Sua ideia ou hipótese", height=100, key="idea_in", placeholder="E se todos os humanos desaparecessem por 24 horas?")
        a, b, _ = st.columns([1, 1, 3])
        dur = a.selectbox("Duração (s)", [30, 45, 60])
        neps = b.selectbox("Episódios", [1, 2, 4])
        common_base = dict(source="ideia", hypothesis=idea, duration_s=dur, episodes=neps)
    common_base.update(style_inputs(None))

    def create(then):
        try:
            settings = Settings(**common_base)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Não foi possível começar: {exc}")
            return
        p = store.create(settings)
        st.session_state.pid = p.id
        launch("Gerar vídeo completo" if then == "all" else "Plano de cenas",
               (lambda pr, ctx: studio.run_all(pr, ctx)) if then == "all" else (lambda pr, ctx: studio.stage_plan(pr, ctx)), p.id)

    a, b, _ = st.columns([1, 1, 3])
    if a.button("🚀 Gerar vídeo completo", type="primary", disabled=busy):
        create("all")
    if b.button("Só criar o plano de cenas", disabled=busy):
        create("plan")
    st.stop()

# ------------------------------------------------------------------ projeto aberto
pid = proj.id
s0 = proj.settings
st.subheader(f"{proj.series_title or s0.title_hint}")
st.caption(f"{SRC_LABEL[s0.source]} · estilo {VISUAL_STYLES[s0.visual_style]} · modo {s0.review_mode} · animação {s0.mode}")

with st.expander("Opções do projeto (valem a partir da próxima etapa)"):
    c1, c2, c3, c4 = st.columns(4)
    new_review = c1.radio("Revisão", ["automatico", "supervisionado"], index=["automatico", "supervisionado"].index(s0.review_mode), key=f"rv_{pid}")
    new_mode = c2.radio("Animação", ["rapido", "completo"], index=["rapido", "completo"].index(s0.mode), key=f"md_{pid}")
    new_scope = c3.radio("Animar", ["todas", "marcadas"], index=["todas", "marcadas"].index(s0.animate_scope), key=f"sc_{pid}")
    vl = list(KOKORO_PT_VOICES)
    new_voice = c4.selectbox("Voz", vl, index=vl.index(s0.voice) if s0.voice in vl else 0, key=f"vc_{pid}") if cfg.get("tts.backend", "kokoro") == "kokoro" else s0.voice
    if (new_review, new_mode, new_scope, new_voice) != (s0.review_mode, s0.mode, s0.animate_scope, s0.voice):
        voice_changed = new_voice != s0.voice
        s0.review_mode, s0.mode, s0.animate_scope, s0.voice = new_review, new_mode, new_scope, new_voice
        if voice_changed:  # voz nova: a narração antiga não vale mais
            for e in proj.episodes:
                for sc in e.scenes:
                    sc.status["audio"] = sc.status["clip"] = "pending"
        store.save(proj)
        st.toast("Opções salvas")

a, b, _ = st.columns([1.3, 1.3, 3])
label = "🚀 Gerar vídeo completo" if not any(e.final_video for e in proj.episodes) else "🚀 Continuar / refazer o que faltar"
if a.button(label, type="primary", disabled=busy):
    launch("Gerar vídeo completo", lambda p, ctx: studio.run_all(p, ctx), pid)
if not proj.episodes and b.button("✍️ Só criar o plano", disabled=busy):
    launch("Plano de cenas", lambda p, ctx: studio.stage_plan(p, ctx), pid)

if not proj.episodes:
    st.info("O plano de cenas ainda não existe. Clique em **Gerar vídeo completo** (o Ollama precisa estar rodando).")
    st.stop()

tab_plan, tab_img, tab_anim, tab_final = st.tabs(["1. Plano de cenas", "2. Imagens", "3. Animações", "4. Vídeo final"])

# ------------------------------------------------------------------ plano
with tab_plan:
    changed = edited_scenes(proj)
    if s0.source == "roteiro":
        st.success(f"Texto preservado: {sum(len(e.scenes) for e in proj.episodes) - len(changed)} cena(s) idênticas ao seu roteiro"
                   + (f" · ✏️ {len(changed)} editada(s) por você" if changed else "."))
    with st.expander("Bíblia visual (continuidade de personagens, cenários e estilo)"):
        st.json(proj.memory.model_dump(), expanded=False)
    allow_text = st.checkbox("Permitir editar o texto da narração (isso altera o roteiro original)", value=False, key=f"allow_{pid}")
    loc_names = [""] + [l.name for l in proj.memory.locations]
    char_names = [c.name for c in proj.memory.characters]
    for ep in proj.episodes:
        st.markdown(f"#### Episódio {ep.number}: {ep.title} {'✅ aprovado' if ep.approved else '📝 aguardando aprovação'}")
        if ep.summary:
            st.caption(f"Resumo: {ep.summary}" + (f"  |  Ponte para o próximo: {ep.next_hint}" if ep.next_hint else ""))
        for sc in ep.scenes:
            k = f"{pid}_{sc.id}"
            tag = "✏️ " if sc.narration != sc.original_narration else ""
            with st.expander(f"{sc.id} · {tag}~{sc.est_duration:.1f}s · {sc.narration[:70]}"):
                for n in sc.notes:
                    st.caption(f"🎬 direção do roteiro: {n}")
                st.text_area("Narração (texto do roteiro)", sc.narration, key=f"n_{k}", height=80, disabled=not allow_text)
                st.text_area("Prompt da imagem (inglês)", sc.image_prompt, key=f"i_{k}", height=110)
                st.text_input("Prompt da animação", sc.animation_prompt, key=f"a_{k}")
                x, y, z, w = st.columns(4)
                x.selectbox("Câmera", CAMERAS, index=CAMERAS.index(sc.camera_move), key=f"c_{k}")
                y.selectbox("Efeito sonoro", SFX, index=SFX.index(sc.sfx or ""), key=f"s_{k}")
                z.selectbox("Cenário", loc_names, index=loc_names.index(sc.location) if sc.location in loc_names else 0, key=f"l_{k}")
                w.multiselect("Personagens", char_names, default=[c for c in sc.characters if c in char_names], key=f"ch_{k}")
    c1, c2, _ = st.columns([1.2, 1.2, 3])
    save_clicked = c1.button("💾 Salvar edições", disabled=busy)
    approve_clicked = c2.button("✅ Aprovar plano", type="primary", disabled=busy)
    if save_clicked or approve_clicked:
        edits = {}
        for ep in proj.episodes:
            for sc in ep.scenes:
                k = f"{pid}_{sc.id}"
                patch = {"image_prompt": st.session_state[f"i_{k}"], "animation_prompt": st.session_state[f"a_{k}"],
                         "camera_move": st.session_state[f"c_{k}"], "sfx": st.session_state[f"s_{k}"] or None,
                         "location": st.session_state[f"l_{k}"], "characters": st.session_state[f"ch_{k}"]}
                if allow_text:
                    patch["narration"] = st.session_state[f"n_{k}"]
                edits[sc.id] = patch
        apply_edits(proj, edits, store, allow_text_edit=allow_text)
        if approve_clicked:
            approve(proj, store)
        st.success("Plano salvo" + (" e aprovado." if approve_clicked else "."))
        st.rerun()


def scene_grid(kind: str, cols: int = 4):
    for ep in proj.episodes:
        st.markdown(f"**Episódio {ep.number}**")
        for row in range(0, len(ep.scenes), cols):
            for col, sc in zip(st.columns(cols), ep.scenes[row:row + cols]):
                with col.container(border=True):
                    st.caption(f"{ICON[sc.status[kind]]} {sc.id}")
                    f = sc.files.image if kind == "image" else sc.files.animation
                    p = store.abs(pid, f)
                    if p and p.exists():
                        (st.image if kind == "image" else st.video)(str(p))
                    if sc.status[kind] == "error":
                        st.error(sc.error or "erro")
                    if kind == "animation":
                        flag = st.checkbox("animar", value=sc.animate, key=f"anim_{pid}_{sc.id}", disabled=busy)
                        if flag != sc.animate:
                            sc.animate = flag
                            store.save(proj)
                    m = {k: v for k, v in sc.metrics.items() if k.startswith(kind)}
                    if m:
                        st.caption(" · ".join(f"{k.split('_', 1)[1]}: {v:g}" for k, v in m.items()))
                    has_img = bool(sc.files.image and store.abs(pid, sc.files.image).exists())
                    can = proj.plan_approved and not busy and (kind == "image" or has_img)
                    if st.button("🔁 Regenerar", key=f"rg_{kind}_{pid}_{sc.id}", disabled=not can):
                        launch(f"Regenerar {kind} {sc.id}", lambda p, ctx, s=sc.id, k=kind: studio.regenerate_scene(
                            p, s, ctx, k, reanimate=(k == "image" and p.settings.mode == "completo" and bool(p.scene(s)[1].files.animation))), pid)


with tab_img:
    if not proj.plan_approved:
        st.warning("Aprove o plano na aba anterior (ou use 'Gerar vídeo completo' no modo automático).")
    a, b, c, _ = st.columns([1, 1, 1, 2])
    if a.button("🖼️ Gerar imagens", type="primary", disabled=busy or not proj.plan_approved):
        launch("Imagens", lambda p, ctx: studio.stage_images(p, ctx), pid)
    if b.button("Refazer todas", disabled=busy or not proj.plan_approved):
        launch("Imagens (todas)", lambda p, ctx: studio.stage_images(p, ctx, force=True), pid)
    if c.button("✅ Aprovar imagens", disabled=busy):
        proj.images_approved = True
        store.save(proj)
        st.rerun()
    if proj.images_approved:
        st.caption("Imagens aprovadas.")
    st.caption("Regenerar uma imagem troca a semente e **descarta a animação antiga dessa cena**.")
    scene_grid("image")

with tab_anim:
    if proj.settings.mode == "rapido":
        st.info("Modo rápido: esta etapa é opcional. O vídeo usa zoom e pan nas imagens (FFmpeg). Para animar com o Wan 2.2, mude a opção 'Animação' do projeto para *completo*.")
    st.caption("A duração de cada animação acompanha a narração da cena. Tempo e pico de VRAM aparecem nos cartões.")
    a, b, c, _ = st.columns([1, 1, 1, 2])
    if a.button("🎞️ Gerar animações", type="primary", disabled=busy or not proj.plan_approved):
        launch("Animações", lambda p, ctx: studio.stage_animations(p, ctx), pid)
    if b.button("Verificar workflow Wan", disabled=busy):
        try:
            probs = studio.check_workflows(("video",))
            st.success("Workflow compatível com o ComfyUI instalado.") if not probs else st.error("\n\n".join(probs))
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    if c.button("✅ Aprovar animações", disabled=busy):
        proj.animations_approved = True
        store.save(proj)
        st.rerun()
    scene_grid("animation")

with tab_final:
    a, b, _ = st.columns([1, 1, 3])
    if a.button("🎬 Gerar só o vídeo final", type="primary", disabled=busy or not proj.plan_approved):
        launch("Vídeo final", lambda p, ctx: studio.stage_render(p, ctx), pid)
    if b.button("🔊 Gerar só a narração", disabled=busy or not proj.plan_approved):
        launch("Narração", lambda p, ctx: studio.stage_audio(p, ctx), pid)
    for ep in proj.episodes:
        st.markdown(f"#### Episódio {ep.number}")
        with st.expander("Narração por cena"):
            for sc in ep.scenes:
                ap = store.abs(pid, sc.files.audio)
                x, y = st.columns([3, 2])
                x.write(f"{ICON[sc.status['audio']]} **{sc.id}** {sc.narration}")
                if ap and ap.exists():
                    y.audio(str(ap))
                    if y.button("🔁 Refazer voz", key=f"ra_{pid}_{sc.id}", disabled=busy):
                        launch(f"Narração {sc.id}", lambda p, ctx, s=sc.id: studio.regenerate_scene(p, s, ctx, "audio", new_seed=False), pid)
        vp = store.abs(pid, ep.final_video)
        if vp and vp.exists():
            v, r = st.columns([1, 1])
            v.video(str(vp))
            rep = ep.final_report or {}
            r.markdown(f"**{rep.get('duration', 0):.1f}s · {rep.get('size_mb', 0)} MB · volume médio {rep.get('mean_volume_db', 0):.1f} dB**")
            for c in rep.get("checks", []):
                r.write(f"{'✅' if c['ok'] else '❌'} {c['check']}: {c['detail']}")
            r.download_button("⬇️ Baixar MP4", vp.read_bytes(), file_name=vp.name, mime="video/mp4", key=f"dl_{pid}_{ep.number}")
            sp = store.abs(pid, ep.final_srt)
            if sp and sp.exists():
                r.download_button("⬇️ Baixar legendas (SRT)", sp.read_bytes(), file_name=sp.name, key=f"dls_{pid}_{ep.number}")
            r.caption(f"Cópia também em: {cfg.path('paths.outputs_dir') / pid}")
    with st.expander("Log do projeto"):
        st.code("\n".join(tail_log(project_log_path(store, pid), 120)) or "(vazio)", language=None)
