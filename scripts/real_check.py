"""Testes REAIS no seu computador: Ollama, ComfyUI (FLUX e Wan 2.2), Kokoro/Whisper, FFmpeg e o botão único de ponta a ponta.
Nada aqui usa servidores falsos. Cada etapa roda isolada e o resultado (PASSOU/FALHOU/PULADO) vai para um relatório que você
pode me enviar.

  .\\.venv\\Scripts\\python scripts\\real_check.py                       # tudo, na ordem de prioridade
  .\\.venv\\Scripts\\python scripts\\real_check.py --steps ambiente,ollama,workflows,imagem
  .\\.venv\\Scripts\\python scripts\\real_check.py --skip wan,tudo_um_botao

Saída: real_test_output/relatorio.md, relatorio.json e os arquivos gerados (imagem, áudio, MP4, animação).
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wifstudio import render  # noqa: E402
from wifstudio.comfy import ComfyClient, load_workflow, patch_workflow, validate_against_server  # noqa: E402
from wifstudio.config import load_config  # noqa: E402
from wifstudio.jobs import JobRunner  # noqa: E402
from wifstudio.llm import OllamaClient  # noqa: E402
from wifstudio.pipeline import Studio  # noqa: E402
from wifstudio.preflight import VramSampler, gpu_info  # noqa: E402
from wifstudio.schemas import SceneVisualBatch, Settings  # noqa: E402
from wifstudio.textnorm import wer  # noqa: E402

STEPS = ["ambiente", "ollama", "workflows", "imagem", "tts_pt", "mp4_30s", "wan", "tudo_um_botao"]
OUT = ROOT / "real_test_output"
cfg = load_config()
state: dict = {}


class Skip(Exception):
    pass


def need(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------- etapas
def step_ambiente(d: list[str]):
    d.append(f"Python {platform.python_version()} em {platform.platform()}")
    for tool in ("ffmpeg", "ffprobe"):
        need(shutil.which(tool) is not None, f"{tool} não está no PATH (winget install -e --id Gyan.FFmpeg)")
    f = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    for flt in ("ass", "xfade", "zoompan"):
        need(f" {flt} " in f, f"seu FFmpeg não tem o filtro '{flt}' (use o build full do Gyan)")
    d.append("FFmpeg com ass, xfade e zoompan")
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    d.append("FFmpeg lista o codificador NVENC (só funciona com a GPU NVIDIA)" if "h264_nvenc" in enc else "FFmpeg sem NVENC: o export usa libx264 na CPU (tudo bem)")
    g = gpu_info()
    need(g is not None, "nvidia-smi não encontrado: instale o driver NVIDIA")
    d.append(f"GPU: {g['name']} | driver {g['driver']} | VRAM {g['vram_total_mb']} MB | compute {g['compute_cap']}")
    st = ComfyClient.from_config(cfg).system_stats()
    dev = (st.get("devices") or [{}])[0]
    d.append(f"ComfyUI {st['system'].get('comfyui_version')} | PyTorch {st['system'].get('pytorch_version')} | dispositivo: {dev.get('name')} | VRAM livre ~{dev.get('vram_free', 0) // 2**20} MB")
    need("cuda" in str(dev.get("type", dev.get("name", ""))).lower(), f"o ComfyUI NÃO está usando a GPU CUDA (dispositivo: {dev}). Rode sem --cpu e confira o PyTorch.")


def step_ollama(d: list[str]):
    llm = OllamaClient.from_config(cfg)
    need(llm.has_model(), f"modelo {llm.model} não instalado: ollama pull {llm.model}")
    d.append(f"Ollama {llm.host} com {llm.model}")
    from wifstudio.jobs import JobContext, JobState
    ctx = JobContext(JobState("real"))
    text = (ROOT / "examples" / "roteiro_crime.txt").read_text(encoding="utf-8")
    store_dir = OUT / "ollama_test"
    shutil.rmtree(store_dir, ignore_errors=True)
    st = Studio(cfg, store=__import__("wifstudio.project", fromlist=["ProjectStore"]).ProjectStore(store_dir))
    p = st.store.create(Settings(source="roteiro", script_text=text, genre="crime", visual_style="cinematografico"))
    t0 = time.time()
    st.stage_plan(p, ctx)
    sec = time.time() - t0
    n = sum(len(e.scenes) for e in p.episodes)
    d += [f"plano em {sec:.0f}s: {n} cenas, {len(p.memory.characters)} personagens, {len(p.memory.locations)} cenários",
          f"gênero deduzido: {p.episode_outline[0].get('genre')} | tom: {p.episode_outline[0].get('tone')}"]
    from wifstudio.script import narrated_words
    got = [w for e in p.episodes for s in e.scenes for w in s.narration.split()]
    need(got == narrated_words(text), "a narração das cenas NÃO é idêntica ao roteiro (isso é um bug grave, envie o relatório)")
    d.append("texto do roteiro preservado palavra por palavra")
    for line in ctx.state.logs:
        if "Tentativa" in line:
            d.append(f"o modelo precisou de nova tentativa: {line[9:160]}")
    d.append("Exemplo de prompt de imagem: " + p.episodes[0].scenes[0].image_prompt[:200])


def step_workflows(d: list[str]):
    c = ComfyClient.from_config(cfg)
    info = c.object_info()
    problems = []
    for kind in ("image", "video"):
        wf, _ = load_workflow(cfg.path(f"comfyui.workflows.{kind}"))
        problems += [f"[{kind}] {p}" for p in validate_against_server(wf, info)]
    if problems:
        d += problems
    need(not problems, "workflow incompatível com o seu ComfyUI. Instale os modelos listados em ComfyUI/models/... ou ajuste os nomes.")
    d.append("os workflows de imagem e vídeo batem com os nós e modelos instalados")


def step_imagem(d: list[str]):
    from PIL import Image, ImageStat
    c = ComfyClient.from_config(cfg)
    wf, mp = load_workflow(cfg.path("comfyui.workflows.image"))
    img = cfg.section("image")
    vals = {"positive_prompt": "cinematic photo of an empty city avenue at dawn, abandoned cars, soft fog, volumetric light, 35mm, vertical",
            "seed": 42, "width": img.get("width", 768), "height": img.get("height", 1344), "steps": img.get("steps", 4),
            "filename_prefix": "wif/real_check"}
    with VramSampler() as vs:
        out = c.run(patch_workflow(wf, mp, vals), OUT / "imagem_teste", cfg.get("comfyui.timeout_image_s", 300))
    im = Image.open(out)
    sd = statistics.mean(ImageStat.Stat(im.convert("RGB")).stddev)
    d.append(f"imagem {im.size[0]}x{im.size[1]} em {vs.seconds:.1f}s" + (f", pico de VRAM {vs.peak} MB (base {vs.baseline} MB)" if vs.peak else ""))
    need(im.size == (vals["width"], vals["height"]), f"tamanho inesperado {im.size}")
    need(sd > 12, f"a imagem parece vazia/chapada (desvio {sd:.1f})")
    d.append(f"conteúdo ok (desvio de cor {sd:.0f}). ABRA {out} e julgue a qualidade você mesmo")
    state["image"] = out


def step_tts_pt(d: list[str]):
    from wifstudio.tts import make_backend, synth_to_wav
    text = "E se a gravidade da Terra sumisse por apenas cinco segundos? Os cientistas acreditam que ninguém sentiria o próprio peso."
    from wifstudio.textnorm import normalize_pt
    be = make_backend(cfg)
    wav = OUT / "narracao_teste.wav"
    t0 = time.time()
    dur = synth_to_wav(be, normalize_pt(text), cfg.get("tts.voice", "pf_dora"), 1.0, wav)
    d.append(f"narração de {dur:.1f}s gerada em {time.time() - t0:.1f}s ({wav})")
    need(3.0 < dur < 20.0, f"duração estranha: {dur}s")
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel(cfg.get("align.whisper_model", "small"), device="cpu", compute_type="int8")
        segs, _ = m.transcribe(str(wav), language="pt", beam_size=5)
        hyp = " ".join(s.text for s in segs)
        w = wer(text, hyp)
        d.append(f"Whisper ouviu: {hyp.strip()!r} | erro de palavras {w:.0%}")
        need(w < 0.25, f"o reconhecimento de fala entendeu mal a narração (erro {w:.0%}): a voz pode não estar em pt-BR")
    except ImportError:
        d.append("faster-whisper não instalado: não deu para conferir por reconhecimento de fala (OUÇA o arquivo)")


def _run_all(name: str, script: Path, mode: str, d: list[str], review="automatico"):
    shutil.rmtree(OUT / name, ignore_errors=True)
    from wifstudio.project import ProjectStore
    st = Studio(cfg, store=ProjectStore(OUT / name / "projects"))
    p = st.store.create(Settings(source="roteiro", script_text=script.read_text(encoding="utf-8"), mode=mode, review_mode=review,
                                 visual_style="cinematografico", genre="auto"))
    runner = JobRunner()
    t0 = time.time()
    ctx = runner.start(name, lambda c: st.run_all(p, c))
    last = ""
    while runner.busy:
        s = ctx.state
        line = f"  [{s.stage_index}/{s.stage_count}] {s.stage}: {s.step} ({s.done}/{s.total})"
        if line != last:
            print(line, flush=True)
            last = line
        time.sleep(2)
    runner.wait()
    (OUT / name / "log.txt").write_text("\n".join(ctx.state.logs), encoding="utf-8")
    need(ctx.state.status == "done", f"{ctx.state.status}: {ctx.state.error or ctx.state.message}. Veja {OUT / name / 'log.txt'}")
    p = st.store.load(p.id)
    ep = p.episodes[0]
    mp4 = st.store.abs(p.id, ep.final_video)
    shutil.copy2(mp4, OUT / f"{name}.mp4")
    target = sum(st.scene_duration(ep, s) for s in ep.scenes)
    rep = render.verify_reel(mp4, target, tol=0.05)
    d.append(f"{len(ep.scenes)} cenas | MP4 de {rep['duration']:.1f}s, {rep['size_mb']} MB, volume médio {rep['mean_volume_db']:.1f} dB | total {time.time() - t0:.0f}s")
    for c in rep["checks"]:
        d.append(f"  {'ok ' if c['ok'] else 'FALHA'} {c['check']}: {c['detail']}")
    d.append(f"ASSISTA {OUT / (name + '.mp4')} e julgue narração, legendas, cortes e qualidade")
    need(rep["ok"], "o MP4 não passou em todas as verificações")
    return p, st


def step_mp4_30s(d: list[str]):
    _run_all("mp4_30s", ROOT / "examples" / "roteiro_e_se_gravidade.txt", "rapido", d)


def step_wan(d: list[str]):
    c = ComfyClient.from_config(cfg)
    img = state.get("image")
    if img is None:
        step_imagem([])
        img = state["image"]
    wf, mp = load_workflow(cfg.path("comfyui.workflows.video"))
    a = cfg.section("animation")
    c.free()
    up = c.upload_image(Path(img))
    vals = {"input_image": up, "prompt": "slow cinematic camera push in, drifting fog, subtle motion", "seed": 7,
            "width": a.get("width", 704), "height": a.get("height", 1280), "length": a.get("length", 49), "fps": a.get("fps", 24),
            "steps": a.get("steps", 20), "filename_prefix": "wif/real_check_wan"}
    with VramSampler() as vs:
        out = c.run(patch_workflow(wf, mp, vals), OUT / "animacao_teste", cfg.get("comfyui.timeout_video_s", 2400))
    d.append(f"{vals['length']} quadros a {vals['width']}x{vals['height']} em {vs.seconds:.0f}s" + (f", pico de VRAM {vs.peak} MB" if vs.peak else ""))
    dur = render.media_duration(out)
    need(dur > 1.0, f"vídeo curto demais ({dur:.2f}s)")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"], capture_output=True)
    need(r.returncode == 0 and not r.stderr.strip(), "o vídeo gerado não decodifica sem erros")
    from PIL import Image
    import numpy as np
    fr = []
    for ss in ("0.1", str(max(dur - 0.2, 0.2))):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", ss, "-i", str(out), "-frames:v", "1", "-vf", "scale=176:312", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True).stdout
        fr.append(np.frombuffer(raw, np.uint8).astype(float))
    motion = float(np.abs(fr[0] - fr[1]).mean()) if len(fr[0]) == len(fr[1]) else 0.0
    d.append(f"duração {dur:.2f}s | movimento entre primeiro e último quadro: {motion:.1f} (0 = estático)")
    need(motion > 2.0, "o vídeo praticamente não se mexe: o Wan pode não ter usado a imagem")
    d.append(f"ASSISTA {out}")
    c.free()


def step_tudo(d: list[str]):
    p, st = _run_all("tudo_um_botao", ROOT / "examples" / "roteiro_crime.txt", "completo", d)
    ep = p.episodes[0]
    d.append("animações: " + ", ".join(f"{s.id}={s.status['animation']}" for s in ep.scenes))
    need(all(s.status["animation"] == "done" for s in ep.scenes), "nem todas as cenas foram animadas")
    for s in ep.scenes:
        d.append(f"  {s.id}: imagem {s.metrics.get('image_seconds', '?')}s, animação {s.metrics.get('animation_seconds', '?')}s, VRAM anim {s.metrics.get('animation_vram_peak_mb', '?')} MB")


FN = {"ambiente": step_ambiente, "ollama": step_ollama, "workflows": step_workflows, "imagem": step_imagem, "tts_pt": step_tts_pt,
      "mp4_30s": step_mp4_30s, "wan": step_wan, "tudo_um_botao": step_tudo}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--skip", default="")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    chosen = [s for s in a.steps.split(",") if s and s not in a.skip.split(",")]
    results = []
    for name in chosen:
        if name not in FN:
            print(f"etapa desconhecida: {name}. Opções: {', '.join(STEPS)}")
            return 2
        print(f"\n=== {name} ===", flush=True)
        d: list[str] = []
        t0 = time.time()
        try:
            FN[name](d)
            status, err = "PASSOU", ""
        except Skip as e:
            status, err = "PULADO", str(e)
        except Exception as e:  # noqa: BLE001
            status, err = "FALHOU", f"{type(e).__name__}: {e}"
            if type(e).__name__ not in ("AssertionError", "LLMError", "ComfyError", "TTSError", "StageError", "WorkflowMappingError"):
                d.append(traceback.format_exc(limit=4))  # erro inesperado: inclui o traceback para eu poder diagnosticar
        results.append({"etapa": name, "status": status, "segundos": round(time.time() - t0, 1), "detalhes": d, "erro": err})
        print(f"{status} ({time.time() - t0:.0f}s) {err}")
        for line in d:
            print("   ", line)
    (OUT / "relatorio.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Relatório de testes reais", "", f"Máquina: {platform.platform()} | Python {platform.python_version()} | GPU: {(gpu_info() or {}).get('name')}", "",
          "| Etapa | Resultado | Tempo |", "|---|---|---|"]
    md += [f"| {r['etapa']} | {r['status']} | {r['segundos']}s |" for r in results]
    for r in results:
        md += ["", f"## {r['etapa']}: {r['status']}", r["erro"]] + [f"- {x}" for x in r["detalhes"]]
    (OUT / "relatorio.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nRelatório: {OUT / 'relatorio.md'}")
    return 0 if all(r["status"] != "FALHOU" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
