"""What If Local Studio: interface Streamlit. Rode com:  streamlit run app.py   (ou .\\run.ps1)"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from wifstudio.config import load_config  # noqa: E402
from wifstudio.jobs import JobRunner  # noqa: E402
from wifstudio.pipeline import Studio  # noqa: E402
from wifstudio.planner import apply_edits, approve  # noqa: E402
from wifstudio.preflight import run_checks  # noqa: E402
from wifstudio.project import append_log, project_log_path, tail_log  # noqa: E402
from wifstudio.schemas import TONES, VISUAL_STYLES, Settings  # noqa: E402
from wifstudio.tts import KOKORO_PT_VOICES  # noqa: E402

st.set_page_config(page_title="What If Local Studio", page_icon="🎬", layout="wide")


@st.cache_resource
def _boot():
    cfg = load_config()
    return cfg, Studio(cfg), JobRunner()


cfg, studio, runner = _boot()
store = studio.store
CAMERAS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
SFX = ["", "whoosh", "impact", "rumble", "riser"]
ICON = {"pending": "⚪", "running": "🟡", "done": "🟢", "error": "🔴", "skipped": "⚫"}

if "pid" not in st.session_state:
    st.session_state.pid = None
if "seen_job" not in st.session_state:
    st.session_state.seen_job = None


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
    st.caption("Tudo roda no seu computador. Sem APIs pagas.")
    if st.button("➕ Novo projeto", use_container_width=True, disabled=runner.busy):
        st.session_state.pid = None
        st.rerun()
    projs = store.list()
    if projs:
        labels = {p["id"]: f"{p['title'][:38]} ({p['created'][:10]})" for p in projs}
        ids = list(labels)
        idx = ids.index(st.session_state.pid) if st.session_state.pid in ids else None
        choice = st.selectbox("Projetos salvos", ids, index=idx, format_func=labels.get, placeholder="Abrir projeto...",
                              disabled=runner.busy)
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

# ------------------------------------------------------------------ painel de progresso (progresso REAL)


def _progress_body():
    ctx = runner.current
    if ctx is None:
        return
    s = ctx.state
    box = st.container(border=True)
    with box:
        if s.status == "running":
            c1, c2 = st.columns([5, 1])
            c1.progress(s.fraction, text=f"{s.name}: {s.step} ({s.done}/{s.total})")
            if c2.button("Cancelar", key="cancel_job"):
                ctx.cancel()
        elif s.status == "done":
            st.success(f"{s.name}: concluído em {(s.finished or time.time()) - s.started:.0f}s")
        elif s.status == "cancelled":
            st.warning(f"{s.name}: cancelado")
        else:
            st.error(f"{s.name} falhou: {s.error}")
        with st.expander("Logs", expanded=s.status in ("error",)):
            st.code("\n".join(s.logs[-60:]) or "(vazio)", language=None)
    if s.status != "running" and st.session_state.seen_job == id(s):
        st.session_state.seen_job = None  # atualiza o restante da tela uma vez ao terminar
        st.rerun()


if runner.busy or st.session_state.seen_job:
    st.fragment(run_every=1.0)(_progress_body)()
elif runner.current is not None:
    _progress_body()

busy = runner.busy

# ------------------------------------------------------------------ formulário
s0 = proj.settings if proj else None
st.subheader("1. Ideia")
c1, c2 = st.columns([3, 2])
hyp = c1.text_area("Hipótese ou tema", value=s0.hypothesis if s0 else "", height=90, placeholder="E se todos os humanos desaparecessem por 24 horas?")
with c2:
    a, b = st.columns(2)
    dur = a.selectbox("Duração (s)", [30, 45, 60], index=[30, 45, 60].index(s0.duration_s) if s0 else 0)
    eps = b.selectbox("Episódios", [1, 2, 4], index=[1, 2, 4].index(s0.episodes) if s0 else 0)
a, b, c = st.columns(3)
style = a.selectbox("Estilo visual", list(VISUAL_STYLES), format_func=VISUAL_STYLES.get, index=list(VISUAL_STYLES).index(s0.visual_style) if s0 else 0)
tone = b.selectbox("Tom", list(TONES), format_func=TONES.get, index=list(TONES).index(s0.tone) if s0 else 0)
if cfg.get("tts.backend", "kokoro") == "kokoro":
    vl = list(KOKORO_PT_VOICES)
    voice = c.selectbox("Voz de narração", vl, format_func=lambda v: f"{v} ({KOKORO_PT_VOICES[v]})", index=vl.index(s0.voice) if s0 and s0.voice in vl else 0)
else:
    voice = c.text_input("Voz (ignorada pelo backend atual)", value=s0.voice if s0 else "")
a, b = st.columns([3, 1])
ref = a.text_input("Referência de estilo (opcional)", value=s0.style_reference if s0 else "", help="Descreva ritmo, clima ou estrutura. Não cole conteúdo de terceiros.")
mode = b.radio("Modo de geração", ["rapido", "completo"], index=["rapido", "completo"].index(s0.mode) if s0 else 0, horizontal=True,
               help="Rápido: imagens com movimento de câmera (FFmpeg). Completo: anima cenas selecionadas com Wan 2.2.")

has_script = bool(proj and proj.episodes)
overwrite = True
if has_script:
    overwrite = st.checkbox("Sobrescrever o roteiro atual (apaga edições, imagens e áudios das cenas)", value=False)
# Validação no clique (e não com disabled=): o Streamlit só confirma o texto digitado ao sair do campo, e um botão
# desabilitado "engole" o primeiro clique.
if st.button("✍️ Gerar roteiro", type="primary", disabled=busy):
    if not hyp.strip():
        st.error("Digite uma hipótese antes de gerar o roteiro.")
        st.stop()
    if has_script and not overwrite:
        st.warning("Este projeto já tem roteiro. Marque 'Sobrescrever o roteiro atual' ou crie um novo projeto.")
        st.stop()
    try:
        settings = Settings(hypothesis=hyp.strip(), duration_s=dur, episodes=eps, visual_style=style, tone=tone,
                            voice=voice, style_reference=ref, mode=mode)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Configuração inválida: {exc}")
        st.stop()
    if proj and has_script:
        proj.settings = settings
        proj.episodes = []
        store.save(proj)
        pid = proj.id
    else:
        pid = store.create(settings).id
        st.session_state.pid = pid
    launch("Roteiro", lambda p, ctx: studio.stage_script(p, ctx), pid)

if not proj or not proj.episodes:
    st.info("Digite uma hipótese e clique em **Gerar roteiro**. O Ollama precisa estar rodando (veja *Verificação do ambiente*).")
    st.stop()

pid = proj.id
tab_script, tab_img, tab_anim, tab_final = st.tabs(["2. Roteiro", "3. Imagens", "4. Animações", "5. Vídeo final"])

# ------------------------------------------------------------------ roteiro
with tab_script:
    st.caption(f"**{proj.series_title}**. Edite o que quiser e aprove. Cenas marcadas como *especulação* devem soar como hipótese.")
    with st.expander("Memória da série (continuidade visual e narrativa)"):
        st.json(proj.memory.model_dump(), expanded=False)
    for ep in proj.episodes:
        st.markdown(f"#### Episódio {ep.number}: {ep.title} {'✅ aprovado' if ep.approved else '📝 aguardando aprovação'}")
        st.caption(f"Resumo: {ep.summary}  |  Gancho para o próximo: {ep.next_hint}")
        for sc in ep.scenes:
            with st.expander(f"{sc.id} · {'🔬 fato' if sc.fact_type == 'fato' else '💭 especulação'} · ~{sc.est_duration:.1f}s · {sc.narration[:60]}"):
                st.text_area("Narração (pt-BR)", sc.narration, key=f"n_{sc.id}", height=80)
                st.text_area("Prompt da imagem (inglês)", sc.image_prompt, key=f"i_{sc.id}", height=110)
                st.text_input("Prompt da animação", sc.animation_prompt, key=f"a_{sc.id}")
                x, y = st.columns(2)
                x.selectbox("Movimento de câmera", CAMERAS, index=CAMERAS.index(sc.camera_move), key=f"c_{sc.id}")
                y.selectbox("Efeito sonoro", SFX, index=SFX.index(sc.sfx or ""), key=f"s_{sc.id}")
    if st.button("✅ Editar e aprovar roteiro", type="primary", disabled=busy):
        edits = {sc.id: {"narration": st.session_state[f"n_{sc.id}"], "image_prompt": st.session_state[f"i_{sc.id}"],
                         "animation_prompt": st.session_state[f"a_{sc.id}"], "camera_move": st.session_state[f"c_{sc.id}"],
                         "sfx": st.session_state[f"s_{sc.id}"] or None} for ep in proj.episodes for sc in ep.scenes}
        apply_edits(proj, edits, store)
        approve(proj, store)
        st.success("Roteiro salvo e aprovado.")
        st.rerun()

approved = all(e.approved for e in proj.episodes)


def scene_grid(kind: str, cols: int = 4):
    """Cartões de cena com status e botão de regenerar. kind: image | animation"""
    for ep in proj.episodes:
        st.markdown(f"**Episódio {ep.number}**")
        for row in range(0, len(ep.scenes), cols):
            columns = st.columns(cols)
            for col, sc in zip(columns, ep.scenes[row:row + cols]):
                with col.container(border=True):
                    st.caption(f"{ICON[sc.status[kind]]} {sc.id}")
                    f = sc.files.image if kind == "image" else sc.files.animation
                    p = store.abs(pid, f)
                    if p and p.exists():
                        (st.image if kind == "image" else st.video)(str(p))
                    if sc.status[kind] == "error":
                        st.error(sc.error or "erro")
                    if kind == "animation":
                        flag = st.checkbox("animar", value=sc.animate, key=f"anim_{sc.id}", disabled=busy)
                        if flag != sc.animate:
                            sc.animate = flag
                            store.save(proj)
                    m = {k: v for k, v in sc.metrics.items() if k.startswith(kind)}
                    if m:
                        st.caption(" · ".join(f"{k.split('_', 1)[1]}: {v:g}" for k, v in m.items()))
                    can = approved and not busy and (kind == "image" or (sc.files.image and store.abs(pid, sc.files.image).exists()))
                    if st.button("🔁 Regenerar", key=f"rg_{kind}_{sc.id}", disabled=not can):
                        launch(f"Regenerar {sc.id}", lambda p, ctx, s=sc.id, k=kind: studio.regenerate_scene(p, s, ctx, k), pid)


with tab_img:
    if not approved:
        st.warning("Aprove o roteiro na aba anterior para liberar a geração de imagens.")
    a, b, _ = st.columns([1, 1, 3])
    if a.button("🖼️ Gerar imagens", type="primary", disabled=busy or not approved):
        launch("Imagens", lambda p, ctx: studio.stage_images(p, ctx), pid)
    if b.button("Refazer todas", disabled=busy or not approved):
        launch("Imagens (todas)", lambda p, ctx: studio.stage_images(p, ctx, force=True), pid)
    scene_grid("image")

with tab_anim:
    if proj.settings.mode == "rapido":
        st.info("Modo rápido: esta etapa é opcional. O vídeo final usa zoom e pan nas imagens (FFmpeg). Para usar a animação por IA, mude o modo para *completo*.")
    st.caption("Marque as cenas a animar. Cenas sem animação continuam com movimento de câmera. A medição de tempo e pico de VRAM aparece em cada cartão.")
    a, b, _ = st.columns([1, 1, 3])
    if a.button("🎞️ Gerar animações", type="primary", disabled=busy or not approved):
        launch("Animações", lambda p, ctx: studio.stage_animations(p, ctx), pid)
    if b.button("Verificar workflow Wan", disabled=busy):
        try:
            probs = studio.check_workflows(("video",))
            st.success("Workflow compatível com o ComfyUI instalado.") if not probs else st.error("\n\n".join(probs))
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    scene_grid("animation")

with tab_final:
    a, b, _ = st.columns([1, 1, 3])
    if a.button("🎬 Gerar vídeo final", type="primary", disabled=busy or not approved):
        launch("Vídeo final", lambda p, ctx: studio.stage_render(p, ctx), pid)
    if b.button("🔊 Gerar só a narração", disabled=busy or not approved):
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
                    if y.button("🔁 Refazer voz", key=f"ra_{sc.id}", disabled=busy):
                        launch(f"Narração {sc.id}", lambda p, ctx, s=sc.id: studio.regenerate_scene(p, s, ctx, "audio", new_seed=False), pid)
        vp = store.abs(pid, ep.final_video)
        if vp and vp.exists():
            v, r = st.columns([1, 1])
            v.video(str(vp))
            rep = ep.final_report or {}
            r.markdown(f"**{rep.get('duration', 0):.1f}s · {rep.get('size_mb', 0)} MB · volume médio {rep.get('mean_volume_db', 0):.1f} dB**")
            for c in rep.get("checks", []):
                r.write(f"{'✅' if c['ok'] else '❌'} {c['check']}: {c['detail']}")
            r.download_button("⬇️ Baixar MP4", vp.read_bytes(), file_name=vp.name, mime="video/mp4", key=f"dl_{ep.number}")
            sp = store.abs(pid, ep.final_srt)
            if sp and sp.exists():
                r.download_button("⬇️ Baixar legendas (SRT)", sp.read_bytes(), file_name=sp.name, key=f"dls_{ep.number}")
            r.caption(f"Cópia também em: {cfg.path('paths.outputs_dir') / pid}")
    with st.expander("Log do projeto"):
        st.code("\n".join(tail_log(project_log_path(store, pid), 120)) or "(vazio)", language=None)
