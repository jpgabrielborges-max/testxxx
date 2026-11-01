"""Cliente da API do ComfyUI + edição de workflows API JSON por TÍTULO de nó (nunca por ID fixo)."""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import requests


class ComfyError(RuntimeError):
    pass


class WorkflowMappingError(ComfyError):
    pass


# ---------------------------------------------------------------- workflows


def load_workflow(path: Path) -> tuple[dict, dict]:
    """Devolve (workflow_api_json, mapping). O mapeamento fica em <nome>.mapping.json ao lado do workflow."""
    path = Path(path)
    wf = json.loads(path.read_text(encoding="utf-8"))
    mp = path.with_name(path.stem + ".mapping.json")
    if not mp.exists():
        raise WorkflowMappingError(f"Falta o arquivo de mapeamento {mp.name} ao lado do workflow (veja workflows/README.md).")
    return wf, json.loads(mp.read_text(encoding="utf-8"))


def _title(node: dict) -> str:
    return (node.get("_meta") or {}).get("title", "")


def resolve_node(workflow: dict, spec: dict) -> str:
    """Localiza o nó descrito por spec: {node_id} | {title} | {class_type}. Exige correspondência única."""
    if "node_id" in spec:
        nid = str(spec["node_id"])
        if nid not in workflow:
            raise WorkflowMappingError(f"node_id {nid} não existe no workflow.")
        return nid
    matches = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or "class_type" not in node:
            continue
        if "title" in spec and _title(node).strip().lower() != str(spec["title"]).strip().lower():
            continue
        if "class_type" in spec and node["class_type"] != spec["class_type"]:
            continue
        matches.append(nid)
    if len(matches) != 1:
        found = [f"{nid}:{n.get('class_type')}:'{_title(n)}'" for nid, n in workflow.items() if isinstance(n, dict)]
        raise WorkflowMappingError(
            f"O mapeamento {spec} correspondeu a {len(matches)} nós (esperado 1). Nós do workflow: {found}")
    return matches[0]


def patch_workflow(workflow: dict, mapping: dict, values: dict[str, Any]) -> dict:
    """Copia o workflow e grava `values` (por papel) nas entradas mapeadas. Papéis obrigatórios ausentes dão erro."""
    wf = copy.deepcopy(workflow)
    roles: dict = mapping.get("roles", {})
    for req in mapping.get("required", []):
        if req not in values:
            raise WorkflowMappingError(f"Valor obrigatório '{req}' não foi fornecido.")
    for role, value in values.items():
        spec = roles.get(role)
        if spec is None:
            if role in mapping.get("required", []):
                raise WorkflowMappingError(f"O mapeamento não define o papel '{role}'.")
            continue
        nid = resolve_node(wf, spec)
        inp = spec["input"]
        if inp not in wf[nid].get("inputs", {}):
            raise WorkflowMappingError(f"O nó {nid} ({wf[nid]['class_type']}) não tem a entrada '{inp}'.")
        wf[nid]["inputs"][inp] = value
    return wf


def validate_against_server(workflow: dict, object_info: dict) -> list[str]:
    """Compara o workflow com o ComfyUI instalado: classes de nó existentes e arquivos de modelo disponíveis."""
    problems = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or "class_type" not in node:
            continue
        ct = node["class_type"]
        info = object_info.get(ct)
        if info is None:
            problems.append(f"nó {nid}: a classe '{ct}' não existe neste ComfyUI (atualize o ComfyUI ou ajuste o workflow)")
            continue
        specs = {**info.get("input", {}).get("required", {}), **info.get("input", {}).get("optional", {})}
        for name, val in node.get("inputs", {}).items():
            if isinstance(val, list):  # ligação para outro nó
                continue
            spec = specs.get(name)
            if spec and isinstance(spec[0], list) and val not in spec[0]:
                problems.append(f"nó {nid} ({ct}): '{name}' = {val!r} não está entre as opções instaladas "
                                f"{spec[0][:6]}{'...' if len(spec[0]) > 6 else ''}")
    return problems


# ---------------------------------------------------------------- cliente


class ComfyClient:
    def __init__(self, host: str = "http://127.0.0.1:8188", poll_interval: float = 1.0):
        self.host = host.rstrip("/")
        self.poll = poll_interval
        self.client_id = uuid.uuid4().hex

    @classmethod
    def from_config(cls, cfg) -> "ComfyClient":
        c = cfg.section("comfyui")
        return cls(c.get("host", "http://127.0.0.1:8188"), c.get("poll_interval_s", 1.0))

    def _get(self, path: str, **kw):
        try:
            r = requests.get(f"{self.host}{path}", timeout=kw.pop("timeout", 30), **kw)
        except requests.RequestException as exc:
            raise ComfyError(f"ComfyUI inacessível em {self.host}: {exc}") from exc
        return r

    def system_stats(self) -> dict:
        r = self._get("/system_stats", timeout=5)
        r.raise_for_status()
        return r.json()

    def object_info(self) -> dict:
        r = self._get("/object_info", timeout=60)
        r.raise_for_status()
        return r.json()

    def free(self, unload_models: bool = True) -> None:
        try:
            requests.post(f"{self.host}/free", json={"unload_models": unload_models, "free_memory": True}, timeout=30)
        except requests.RequestException:
            pass

    def interrupt(self) -> None:
        try:
            requests.post(f"{self.host}/interrupt", timeout=10)
        except requests.RequestException:
            pass

    def upload_image(self, path: Path) -> str:
        with open(path, "rb") as f:
            r = requests.post(f"{self.host}/upload/image", files={"image": (Path(path).name, f, "image/png")},
                              data={"overwrite": "true"}, timeout=60)
        if r.status_code != 200:
            raise ComfyError(f"Falha no upload da imagem: {r.status_code} {r.text[:200]}")
        j = r.json()
        return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    def submit(self, workflow: dict) -> str:
        try:
            r = requests.post(f"{self.host}/prompt", json={"prompt": workflow, "client_id": self.client_id}, timeout=60)
        except requests.RequestException as exc:
            raise ComfyError(f"ComfyUI inacessível em {self.host}: {exc}") from exc
        if r.status_code != 200:
            detail = r.text[:1500]
            raise ComfyError(f"ComfyUI recusou o workflow ({r.status_code}): {detail}")
        return r.json()["prompt_id"]

    def wait(self, prompt_id: str, timeout: float, cancel: Callable[[], None] = lambda: None,
             on_tick: Callable[[float], None] = lambda s: None) -> dict:
        start = time.time()
        while True:
            try:
                cancel()
            except BaseException:
                self.interrupt()
                raise
            r = self._get(f"/history/{prompt_id}", timeout=15)
            if r.status_code == 200:
                data = r.json()
                entry = data.get(prompt_id)
                if entry:
                    status = (entry.get("status") or {})
                    if status.get("status_str") == "error":
                        raise ComfyError("Erro na execução do ComfyUI: " + self._error_text(status))
                    if entry.get("outputs") or status.get("completed"):
                        return entry
            elapsed = time.time() - start
            if elapsed > timeout:
                self.interrupt()
                raise ComfyError(f"Timeout de {timeout:.0f}s esperando o ComfyUI (prompt {prompt_id}).")
            on_tick(elapsed)
            time.sleep(self.poll)

    @staticmethod
    def _error_text(status: dict) -> str:
        for ev in status.get("messages", []):
            if ev and ev[0] == "execution_error":
                d = ev[1]
                return f"{d.get('node_type')} (nó {d.get('node_id')}): {d.get('exception_message', '').strip()}"
        return json.dumps(status)[:500]

    @staticmethod
    def output_files(entry: dict, node_id: Optional[str] = None) -> list[dict]:
        refs = []
        for nid, out in (entry.get("outputs") or {}).items():
            if node_id and nid != node_id:
                continue
            for key in ("images", "gifs", "videos", "audio"):
                for item in out.get(key, []) or []:
                    if item.get("type", "output") == "output" and item.get("filename"):
                        refs.append(item)
        return refs

    def download(self, ref: dict, dest: Path) -> Path:
        r = self._get("/view", params={"filename": ref["filename"], "subfolder": ref.get("subfolder", ""),
                                       "type": ref.get("type", "output")}, timeout=300)
        if r.status_code != 200:
            raise ComfyError(f"Falha ao baixar {ref['filename']}: {r.status_code}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    def run(self, workflow: dict, dest: Path, timeout: float, output_node: Optional[str] = None,
            cancel: Callable[[], None] = lambda: None, on_tick: Callable[[float], None] = lambda s: None) -> Path:
        """Envia, acompanha, baixa o primeiro arquivo de saída e devolve o caminho local (extensão do original)."""
        pid = self.submit(workflow)
        entry = self.wait(pid, timeout, cancel, on_tick)
        files = self.output_files(entry, output_node) or self.output_files(entry)
        if not files:
            raise ComfyError(f"O ComfyUI terminou mas não devolveu arquivo de saída (prompt {pid}).")
        ref = files[0]
        ext = Path(ref["filename"]).suffix or ".png"
        return self.download(ref, Path(dest).with_suffix(ext))
