"""Servidores FALSOS de Ollama e ComfyUI, só para testar a orquestração sem GPU.

IMPORTANTE: o conteúdo devolvido aqui (roteiro e imagens) é fixture de teste, NÃO é saída de um LLM nem do FLUX.
O que os testes comprovam é o código de integração (protocolo HTTP, mapeamento de nós, FFmpeg, TTS, legendas).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

GRAVITY_SCENES = [
    ("E se a gravidade da Terra sumisse por apenas cinco segundos?",
     "Wide shot of a quiet city street at dawn, a lone car and a pedestrian, calm before the event, soft golden light, 35mm", "zoom_in", "none", "fato"),
    ("No primeiro instante, você não sentiria o próprio peso.",
     "Close-up of a person's sneakers lifting slightly off the pavement, dust hovering, dramatic rim light", "pan_up", "whoosh", "fato"),
    ("Tudo que não estivesse preso começaria a flutuar.",
     "A kitchen with cups, spoons and water droplets floating in mid air, sunlight through a window", "zoom_out", "none", "fato"),
    ("Como a Terra gira, tudo continuaria em movimento em linha reta.",
     "View of planet Earth from low orbit with a faint motion arrow along the equator, deep blue ocean, thin atmosphere", "pan_right", "riser", "especulacao"),
    ("Em cinco segundos, isso daria menos de meio metro de distância.",
     "A tiny gap between a car tire and the asphalt road, shallow depth of field, tense mood", "zoom_in", "none", "fato"),
    ("Quando a gravidade voltasse, tudo cairia de uma vez, e seria bem desagradável.",
     "Objects and furniture falling back onto the floor in a living room, motion blur, dust cloud, dramatic", "pan_down", "impact", "especulacao"),
    ("E se ela sumisse por um minuto inteiro? A história seria outra.",
     "A lone astronaut-like silhouette floating above a city skyline at night, eerie blue light, wide angle", "zoom_out", "rumble", "especulacao"),
]


def gravity_episode(overlong: bool = False) -> dict:
    scenes = []
    for narr, img, cam, sfx, ft in GRAVITY_SCENES:
        if overlong:
            narr = narr + " " + "E isso mudaria absolutamente tudo o que conhecemos sobre a nossa rotina diária no planeta."
        scenes.append({"narration": narr, "image_prompt": img, "animation_prompt": "slow subtle motion, floating particles",
                       "camera_move": cam, "sfx": sfx, "fact_type": ft, "characters": []})
    return {"title": "Cinco segundos sem peso", "hook": GRAVITY_SCENES[0][0],
            "summary": "A gravidade some por cinco segundos; objetos soltos flutuam e depois caem.",
            "closing": GRAVITY_SCENES[-1][0], "scenes": scenes}


def series_plan(n_episodes: int) -> dict:
    return {
        "series_title": "Sem Gravidade", "universe": "realistic modern Earth, everyday locations",
        "characters": [], "palette": "teal and warm amber", "locations": [{"name": "city street", "description": "quiet city street at dawn"}, {"name": "kitchen", "description": "sunlit home kitchen"},
                                                                    {"name": "low orbit", "description": "view of Earth from low orbit"}],
        "lighting": "soft golden hour with strong rim light", "camera_style": "35mm cinematic, shallow depth of field",
        "episodes": [{"number": i, "title": f"Episódio {i}", "premise": "A gravidade desaparece e as consequências crescem.",
                      "ending_hint": "termina com uma pergunta que o próximo episódio responde"} for i in range(1, n_episodes + 1)],
    }


def script_analysis(n_eps: int) -> dict:
    return {
        "series_title": "Última Rua", "genre": "apocalipse zumbi", "tone": "sombrio e tenso",
        "summary": "Um sobrevivente tenta atravessar a cidade tomada por infectados.",
        "universe": "abandoned coastal city after an outbreak, overcast mornings", "palette": "desaturated teal, rust and ash grey",
        "characters": [{"name": "Rafa", "appearance": "30-year-old man, short black hair, stubble, worn green jacket, backpack"},
                       {"name": "Bidu", "appearance": "small brown mixed-breed dog with white chest and a red collar"}],
        "locations": [{"name": "Avenida deserta", "description": "wide empty avenue with abandoned cars and broken traffic lights"},
                      {"name": "Mercado", "description": "dim supermarket with fallen shelves and flickering lights"}],
        "lighting": "low overcast light with rim highlights", "camera_style": "handheld 35mm, shallow depth of field",
        "style_notes": "grain, muted colors",
        "episodes": [{"number": i, "title": f"Parte {i}", "summary": f"Resumo do episódio {i}.", "ending_hint": "retoma na próxima parte"}
                     for i in range(1, n_eps + 1)],
    }


def scene_visuals(payload: list[dict], characters: list[str], locations: list[str], wrong_indices: bool = False) -> dict:
    cams = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "pan_down"]
    sfxs = ["none", "whoosh", "none", "heartbeat", "none", "wind"]
    out = []
    for k, p in enumerate(payload):
        out.append({"index": p["index"] + (100 if wrong_indices else 0),
                    "image_prompt": f"Cinematic wide shot illustrating: {p['text'][:70]}. Moody light, detailed environment, strong depth",
                    "animation_prompt": "slow subtle motion, drifting dust", "camera_move": cams[k % 6], "sfx": sfxs[k % 6],
                    "fact_type": "ficcao", "characters": characters[:1] if k % 2 == 0 else characters[:2],
                    "location": locations[0] if locations and k < 3 else (locations[-1] if locations else "")})
    return {"scenes": out}


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencia o stderr
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))


class FakeOllama:
    def __init__(self, first_episode_overlong: bool = False, model: str = "qwen2.5:14b"):
        self.first_overlong = first_episode_overlong
        self.model = model
        self.wrong_indices_first = False
        self.visual_calls = 0
        self.analysis_calls = 0
        self.episode_calls = 0
        self.requests: list[dict] = []
        self.unloaded = False
        outer = self

        class H(_Quiet):
            def do_GET(self):
                if self.path == "/api/tags":
                    return self._json({"models": [{"name": outer.model}]})
                self._json({}, 404)

            def do_POST(self):
                body = json.loads(self._body() or b"{}")
                if self.path == "/api/generate":
                    outer.unloaded = body.get("keep_alive") == 0
                    return self._json({"done": True})
                if self.path == "/api/chat":
                    outer.requests.append(body)
                    title = (body.get("format") or {}).get("title")
                    if title == "SeriesPlan":
                        n = int(re.search(r"Número de episódios: (\d+)", body["messages"][1]["content"]).group(1))
                        return self._json({"message": {"content": json.dumps(series_plan(n))}})
                    if title == "ScriptAnalysis":
                        outer.analysis_calls += 1
                        n = int(re.search(r"exatamente (\d+) item", body["messages"][1]["content"]).group(1))
                        return self._json({"message": {"content": json.dumps(script_analysis(n))}})
                    if title == "SceneVisualBatch":
                        import ast
                        outer.visual_calls += 1
                        msg = body["messages"][1]["content"]
                        payload = json.loads(msg.strip().splitlines()[-1])
                        chars = ast.literal_eval(re.search(r"nomes EXATOS, ou lista vazia\): (\[.*?\])", msg).group(1))
                        locs = ast.literal_eval(re.search(r"use o nome EXATO, ou vazio\): (\[.*?\])", msg).group(1))
                        wrong = outer.wrong_indices_first and outer.visual_calls == 1
                        return self._json({"message": {"content": json.dumps(scene_visuals(payload, chars, locs, wrong))}})
                    if title == "EpisodeDraft":
                        outer.episode_calls += 1
                        over = outer.first_overlong and outer.episode_calls == 1
                        return self._json({"message": {"content": json.dumps(gravity_episode(overlong=over))}})
                    return self._json({"error": "schema desconhecido"}, 400)
                self._json({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


_FIX = Path(__file__).parent / "fixtures" / "object_info_comfyui_0.43.0.json"
OBJECT_INFO = {k: v for k, v in json.loads(_FIX.read_text(encoding="utf-8")).items() if not k.startswith("_")}  # nós REAIS do ComfyUI 0.43.0


class FakeComfy:
    """Implementa o subconjunto do protocolo do ComfyUI que o aplicativo usa. Aceita o workflow só se os nós mapeados
    existirem de fato; gera uma imagem (ou um mp4 curto) distinta por prompt."""

    def __init__(self, delay: float = 0.4, fail_prompt_containing: str | None = None, models: list[str] | None = None):
        self.delay = delay
        self.fail_on = fail_prompt_containing
        self.tmp = Path(tempfile.mkdtemp(prefix="fakecomfy_"))
        self.jobs: dict[str, dict] = {}
        self.workflows: list[dict] = []
        self.uploads: list[str] = []
        self.freed = 0
        self.interrupted = 0
        self.object_info = json.loads(json.dumps(OBJECT_INFO))
        if models is not None:
            self.object_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0] = models
        outer = self

        class H(_Quiet):
            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/system_stats":
                    return self._json({"system": {"pytorch_version": "fake"}, "devices": [{"name": "FAKE GPU"}]})
                if u.path == "/object_info":
                    return self._json(outer.object_info)
                if u.path.startswith("/history/"):
                    pid = u.path.rsplit("/", 1)[1]
                    return self._json(outer._history(pid))
                if u.path == "/view":
                    q = parse_qs(u.query)
                    f = outer.tmp / q["filename"][0]
                    if not f.exists():
                        return self._json({}, 404)
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._json({}, 404)

            def do_POST(self):
                body = self._body()
                if self.path == "/prompt":
                    wf = json.loads(body)["prompt"]
                    return outer._submit(self, wf)
                if self.path == "/upload/image":
                    m = re.search(rb'filename="([^"]+)"', body)
                    name = m.group(1).decode() if m else "up.png"
                    outer.uploads.append(name)
                    return self._json({"name": name, "subfolder": "", "type": "input"})
                if self.path == "/free":
                    outer.freed += 1
                    return self._json({})
                if self.path == "/interrupt":
                    outer.interrupted += 1
                    return self._json({})
                self._json({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    # ------------------------------------------------------------------
    def _submit(self, h, wf: dict):
        # valida como o ComfyUI real: toda classe precisa existir e toda ligação apontar para um nó existente
        node_errors = {}
        for nid, node in wf.items():
            if node["class_type"] not in self.object_info:
                node_errors[nid] = f"classe desconhecida {node['class_type']}"
            for v in node["inputs"].values():
                if isinstance(v, list) and str(v[0]) not in wf:
                    node_errors[nid] = f"ligação quebrada para {v[0]}"
        if node_errors:
            return h._json({"error": {"type": "prompt_outputs_failed_validation"}, "node_errors": node_errors}, 400)
        self.workflows.append(wf)
        pid = hashlib.md5(json.dumps(wf, sort_keys=True).encode() + str(time.time()).encode()).hexdigest()[:12]
        kind = "video" if any(n["class_type"] == "SaveVideo" for n in wf.values()) else "image"
        prompt_text = next((n["inputs"]["text"] for n in wf.values() if n["class_type"] == "CLIPTextEncode" and n["inputs"].get("text")), "")
        self.jobs[pid] = {"t0": time.time(), "kind": kind, "prompt": prompt_text, "wf": wf}
        h._json({"prompt_id": pid, "number": 1, "node_errors": {}})

    def _history(self, pid: str) -> dict:
        job = self.jobs.get(pid)
        if not job or time.time() - job["t0"] < self.delay:
            return {}
        if "result" not in job:
            if self.fail_on and self.fail_on in job["prompt"]:
                job["result"] = {"status": {"status_str": "error", "completed": False, "messages": [
                    ["execution_error", {"node_id": "5", "node_type": "KSampler", "exception_message": "CUDA out of memory (simulado)"}]]},
                    "outputs": {}}
            else:
                name = f"{pid}.{'mp4' if job['kind'] == 'video' else 'png'}"
                (self.make_video if job["kind"] == "video" else self.make_image)(self.tmp / name, job["prompt"])
                node = next(i for i, n in job["wf"].items() if n["class_type"] in ("SaveImage", "SaveVideo"))
                job["result"] = {"status": {"status_str": "success", "completed": True, "messages": []},
                                 "outputs": {node: {"images": [{"filename": name, "subfolder": "", "type": "output"}]}}}
        return {pid: job["result"]}

    @staticmethod
    def make_image(path: Path, prompt: str, size=(768, 1344)):
        h = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
        c1 = ((h >> 0) & 0xFF, (h >> 8) & 0xFF // 1, (h >> 16) & 0xFF)
        c2 = (255 - c1[1], c1[2], 255 - c1[0])
        img = Image.new("RGB", size)
        d = ImageDraw.Draw(img)
        for y in range(size[1]):
            t = y / size[1]
            d.line([(0, y), (size[0], y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
        for k in range(8):  # formas para o movimento de câmera ficar visível
            x, y, r = (h >> (k * 5)) % size[0], (h >> (k * 3 + 7)) % size[1], 60 + (h >> (k + 2)) % 140
            d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255), width=6)
        img.save(path)

    @staticmethod
    def make_video(path: Path, prompt: str):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=704x1280:rate=24:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
