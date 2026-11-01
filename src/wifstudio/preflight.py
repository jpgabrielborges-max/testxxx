"""Diagnóstico do ambiente (Ollama, ComfyUI, FFmpeg, GPU) e amostragem real de VRAM."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Optional


def gpu_info() -> Optional[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,driver_version,memory.total,memory.used,compute_cap",
                              "--format=csv,noheader,nounits"], capture_output=True, timeout=10).stdout.decode().strip()
        name, drv, total, used, cap = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"name": name, "driver": drv, "vram_total_mb": int(total), "vram_used_mb": int(used), "compute_cap": cap}
    except Exception:  # noqa: BLE001
        return None


def vram_used_mb() -> Optional[int]:
    g = gpu_info()
    return g["vram_used_mb"] if g else None


class VramSampler:
    """Mede o pico real de VRAM e o tempo enquanto um bloco roda. Sem GPU NVIDIA, peak fica None."""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.peak: Optional[int] = None
        self.baseline: Optional[int] = None
        self.seconds = 0.0
        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None

    def __enter__(self):
        self.baseline = vram_used_mb()
        self._t0 = time.time()
        if self.baseline is not None:
            self.peak = self.baseline
            self._t = threading.Thread(target=self._loop, daemon=True)
            self._t.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            v = vram_used_mb()
            if v is not None and (self.peak is None or v > self.peak):
                self.peak = v

    def __exit__(self, *a):
        self._stop.set()
        if self._t:
            self._t.join(2)
        self.seconds = time.time() - self._t0

    def metrics(self) -> dict:
        m = {"seconds": round(self.seconds, 1)}
        if self.peak is not None:
            m["vram_peak_mb"] = float(self.peak)
        return m


def run_checks(cfg) -> list[dict]:
    """Lista de {name, ok, detail}. Nunca levanta exceção: o objetivo é mostrar o estado real na tela."""
    from .comfy import ComfyClient
    from .llm import OllamaClient

    out = []

    def add(name, ok, detail):
        out.append({"name": name, "ok": ok, "detail": detail})

    for tool in ("ffmpeg", "ffprobe"):
        add(tool, shutil.which(tool) is not None, shutil.which(tool) or "não encontrado no PATH")
    g = gpu_info()
    if g:
        try:
            drv_ok = float(g["driver"].split(".")[0]) >= 570
        except ValueError:
            drv_ok = True
        add("GPU NVIDIA", True, f"{g['name']} | driver {g['driver']} | {g['vram_total_mb']} MB | compute {g['compute_cap']}"
            + ("" if drv_ok else " | ATENÇÃO: GPUs RTX 50 pedem driver 570 ou mais novo"))
    else:
        add("GPU NVIDIA", False, "nvidia-smi não encontrado (a geração de imagens exige GPU)")
    llm = OllamaClient.from_config(cfg)
    try:
        has = llm.has_model()
        add("Ollama", True, f"{llm.host}")
        add(f"Modelo {llm.model}", has, "instalado" if has else f"rode: ollama pull {llm.model}")
    except Exception as exc:  # noqa: BLE001
        add("Ollama", False, f"{llm.host} inacessível ({type(exc).__name__})")
    cc = ComfyClient.from_config(cfg)
    try:
        st = cc.system_stats()
        dev = (st.get("devices") or [{}])[0]
        add("ComfyUI", True, f"{cc.host} | {dev.get('name', '?')} | PyTorch {st.get('system', {}).get('pytorch_version', '?')}")
    except Exception as exc:  # noqa: BLE001
        add("ComfyUI", False, f"{cc.host} inacessível ({type(exc).__name__})")
    kok = cfg.path("tts.kokoro.model") if cfg.get("tts.kokoro.model") else None
    if cfg.get("tts.backend", "kokoro") == "kokoro" and kok is not None:
        add("Modelo Kokoro (TTS)", kok.exists(), str(kok) if kok.exists() else "rode o setup.ps1")
    return out
