"""Carrega config.yaml + .env e entrega um objeto de consulta por caminho pontuado."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict, root: Path):
        self.data = data
        self.root = Path(root)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def section(self, name: str) -> dict:
        return self.data.get(name, {}) or {}

    def path(self, dotted: str) -> Path:
        """Resolve um caminho do config relativo a raiz do projeto."""
        raw = self.get(dotted)
        if raw is None:
            raise KeyError(f"config sem '{dotted}'")
        p = Path(str(raw))
        return p if p.is_absolute() else (self.root / p)

    def set(self, dotted: str, value: Any) -> None:
        cur = self.data
        parts = dotted.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value


def load_config(root: Path | None = None, overrides: dict | None = None, use_env: bool = True) -> Config:
    root = Path(root) if root else ROOT
    cfg_file = root / "config.yaml"
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    data = data or {}
    if use_env:
        try:
            from dotenv import load_dotenv

            load_dotenv(root / ".env", override=False)
        except ImportError:  # python-dotenv e opcional em testes
            pass
        env_map = {
            "OLLAMA_HOST": "ollama.host",
            "OLLAMA_MODEL": "ollama.model",
            "COMFYUI_HOST": "comfyui.host",
        }
        env_over: dict = {}
        for env_key, dotted in env_map.items():
            if os.environ.get(env_key):
                cur = env_over
                parts = dotted.split(".")
                for part in parts[:-1]:
                    cur = cur.setdefault(part, {})
                cur[parts[-1]] = os.environ[env_key]
        data = _deep_merge(data, env_over)
    if overrides:
        data = _deep_merge(data, overrides)
    return Config(data, root)
