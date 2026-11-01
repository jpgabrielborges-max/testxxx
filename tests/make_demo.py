"""Gera um MP4 de demonstração com Ollama e ComfyUI FALSOS (imagens são gradientes de teste, NÃO FLUX).
Serve para ouvir a voz pt-BR, ver legendas/transições e conferir a montagem sem GPU.
  python tests/make_demo.py <pasta_saida> [pasta_modelos_kokoro]"""
import shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
from fake_servers import FakeComfy, FakeOllama
from wifstudio.config import load_config
from wifstudio.jobs import JobRunner
from wifstudio.pipeline import Studio
from wifstudio.planner import approve
from wifstudio.render import verify_reel
from wifstudio.schemas import Settings

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
kdir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "models" / "kokoro"
tmp = Path(tempfile.mkdtemp()); shutil.copytree(ROOT / "workflows", tmp / "workflows"); shutil.copy(ROOT / "config.yaml", tmp / "config.yaml")
ol, cf = FakeOllama(), FakeComfy(delay=0.2)
cfg = load_config(tmp, use_env=False, overrides={"ollama": {"host": ol.url}, "comfyui": {"host": cf.url, "poll_interval_s": 0.1},
    "align": {"backend": "proportional"}, "subtitles": {"font": "DejaVu Sans"},
    "tts": {"kokoro": {"model": str(kdir / "kokoro-v1.0.onnx"), "voices": str(kdir / "voices-v1.0.bin")}}})
st = Studio(cfg)
p = st.store.create(Settings(hypothesis="E se a gravidade da Terra desaparecesse por 5 segundos?", duration_s=30, episodes=1))
def job(name, fn):
    r = JobRunner(); c = r.start(name, fn); r.wait(900)
    assert c.state.status == "done", (name, c.state.error, c.state.logs[-8:])
job("roteiro", lambda c: st.stage_script(p, c)); approve(p, st.store)
job("imagens", lambda c: st.stage_images(p, c)); job("video", lambda c: st.stage_render(p, c))
mp4 = st.store.abs(p.id, p.episodes[0].final_video)
shutil.copy(mp4, out / "demo_gravidade_30s_IMAGENS_DE_TESTE.mp4"); shutil.copy(st.store.abs(p.id, p.episodes[0].final_srt), out / "demo.srt")
rep = verify_reel(mp4, 30)
for c in rep["checks"]: print("OK " if c["ok"] else "FALHA", c["check"], "|", c["detail"])
print("duracao", rep["duration"], "tamanho_mb", rep["size_mb"], "geral_ok", rep["ok"])
ol.close(); cf.close()
