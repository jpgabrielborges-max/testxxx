"""Cliente do Ollama com saída estruturada (JSON schema) validada por Pydantic."""
from __future__ import annotations

import json
from typing import Callable, Optional, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str, model: str, temperature: float = 0.8, num_ctx: int = 8192,
                 timeout: int = 900, think: Optional[bool] = None, max_retries: int = 2):
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.think = think
        self.max_retries = max_retries

    @classmethod
    def from_config(cls, cfg) -> "OllamaClient":
        o = cfg.section("ollama")
        return cls(o.get("host", "http://127.0.0.1:11434"), o.get("model", "qwen2.5:14b"),
                   o.get("temperature", 0.8), o.get("num_ctx", 8192), o.get("timeout_s", 900),
                   o.get("think"), o.get("max_retries", 2))

    # ---- status ----
    def models(self) -> list[str]:
        r = requests.get(f"{self.host}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def has_model(self) -> bool:
        names = self.models()
        return self.model in names or f"{self.model}:latest" in names or any(n.split(":")[0] == self.model for n in names if ":" not in self.model)

    def unload(self) -> None:
        """Descarrega o modelo da VRAM (keep_alive=0) para liberar espaço ao ComfyUI."""
        try:
            requests.post(f"{self.host}/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=30)
        except requests.RequestException:
            pass

    # ---- chamada estruturada ----
    def chat_structured(self, system: str, user: str, schema: Type[T],
                        check: Callable[[T], list[str]] | None = None,
                        log: Callable[[str], None] = lambda m: None) -> T:
        """Pede JSON no formato do schema. Se o JSON for inválido ou `check` apontar problemas, pede de novo
        com o motivo (até max_retries vezes). Nunca devolve dado que não passou na validação."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_problem = ""
        for attempt in range(self.max_retries + 1):
            body = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": schema.model_json_schema(),
                "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
            }
            if self.think is not None:
                body["think"] = self.think
            try:
                r = requests.post(f"{self.host}/api/chat", json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                raise LLMError(f"Ollama inacessível em {self.host}: {exc}") from exc
            if r.status_code != 200:
                raise LLMError(f"Ollama respondeu {r.status_code}: {r.text[:300]}")
            content = r.json().get("message", {}).get("content", "")
            try:
                obj = schema.model_validate_json(content)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_problem = f"JSON fora do schema: {str(exc)[:400]}"
                log(f"Tentativa {attempt + 1}: {last_problem}")
            else:
                problems = check(obj) if check else []
                if not problems:
                    return obj
                last_problem = "; ".join(problems)
                log(f"Tentativa {attempt + 1}: {last_problem}")
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"Corrija e devolva o JSON completo de novo. Problemas: {last_problem}"})
        raise LLMError(f"O modelo não produziu uma saída válida após {self.max_retries + 1} tentativas. Último problema: {last_problem}")
