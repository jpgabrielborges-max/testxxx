"""Modo experimental isolado: abre como uma página adicional no Streamlit."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wifstudio.cinematic25d import prepare_render, render_prepared  # noqa: E402
from wifstudio.config import load_config  # noqa: E402
from wifstudio.project import ProjectStore  # noqa: E402

st.set_page_config(page_title="Cinematic 2.5D | Content Machine", page_icon="🎞️", layout="wide")
st.title("🎞️ Animação cinematográfica 2.5D")
st.caption("Protótipo experimental: personagens articulados por código + cenário 3D + câmera contínua; sem Wan e sem API paga.")
st.warning("O rig demonstrativo é simples; ainda não é um gerador automático de personagens profissionais. Seus projetos originais não serão alterados.")

cfg = load_config(ROOT)
store = ProjectStore(cfg.path("paths.projects_dir"))
projects = store.list()
if not projects:
    st.info("Crie um projeto e gere um plano de cenas na interface principal antes de usar este módulo.")
    st.stop()

labels = {p["id"]: (p["title"] + " — " + p["id"]) for p in projects}
project_id = st.selectbox("Projeto existente", list(labels), format_func=labels.get)
project = store.load(project_id)
if not project.episodes:
    st.info("Este projeto ainda não tem um plano de cenas.")
    st.stop()

episode_num = st.selectbox("Episódio", [e.number for e in project.episodes])
episode = project.episode(episode_num)
st.write(f"**Cenas:** {len(episode.scenes)} · **Personagens:** {len(project.memory.characters)}")
st.caption("Use o botão para exportar a atuação. A narração existente será reaproveitada quando disponível.")

if st.button("Preparar timeline e mix de áudio"):
    try:
        props_path, out_path = prepare_render(project, episode, store, cfg, ROOT)
        st.success(f"Timeline criada: {props_path}")
        st.code(str(out_path), language=None)
        st.json(__import__("json").loads(props_path.read_text(encoding="utf-8"))["story"], expanded=False)
    except Exception as exc:
        st.error(f"Falhou ao preparar o projeto: {exc}")

p = store.dir(project_id) / "cinematic25d" / "props.json"
o = store.dir(project_id) / "cinematic25d" / f"{project_id}_ep{episode_num:02d}.mp4"
if p.exists() and st.button("Renderizar animação 2.5D (Remotion)"):
    try:
        with st.spinner("Renderizando com Node + Remotion; mantenha esta aba aberta."):
            output = render_prepared(p, o, ROOT)
        st.success(f"Vídeo renderizado: {output}")
        st.video(str(output))
    except Exception as exc:
        st.error(str(exc))
if o.exists():
    st.video(str(o))

st.info("Primeiro uso: abra PowerShell em cinematic25d e execute npm install. Para pré-visualizar o exemplo: npm run studio.")
