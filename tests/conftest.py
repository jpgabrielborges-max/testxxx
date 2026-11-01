import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fake_servers import FakeComfy, FakeOllama  # noqa: E402
from wifstudio.config import load_config  # noqa: E402

KOKORO_DIR = Path(os.environ.get("WIF_TEST_KOKORO_DIR", "/home/claude/models_cache"))
HAS_KOKORO = (KOKORO_DIR / "kokoro-v1.0.onnx").exists() and (KOKORO_DIR / "voices-v1.0.bin").exists()
needs_kokoro = pytest.mark.skipif(not HAS_KOKORO, reason="modelos do Kokoro ausentes (defina WIF_TEST_KOKORO_DIR)")


@pytest.fixture()
def fake_ollama():
    s = FakeOllama()
    yield s
    s.close()


@pytest.fixture()
def fake_comfy():
    s = FakeComfy()
    yield s
    s.close()


@pytest.fixture()
def cfg(tmp_path, fake_ollama, fake_comfy):
    """Config apontando para servidores falsos e pastas temporárias. Whisper desligado (modelo não baixável aqui)."""
    for d in ("workflows",):
        shutil.copytree(ROOT / d, tmp_path / d)
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path, use_env=False, overrides={
        "ollama": {"host": fake_ollama.url},
        "comfyui": {"host": fake_comfy.url, "poll_interval_s": 0.1},
        "align": {"backend": "proportional"},
        "tts": {"kokoro": {"model": str(KOKORO_DIR / "kokoro-v1.0.onnx"), "voices": str(KOKORO_DIR / "voices-v1.0.bin")}},
        "paths": {"projects_dir": "projects", "outputs_dir": "outputs"},
        "subtitles": {"font": "DejaVu Sans"},
    })
