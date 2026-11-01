"""Persistência dos projetos em disco (JSON atômico) e organização das pastas."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from .schemas import Project, Settings, slugify

_LOCK = threading.RLock()


class ProjectStore:
    def __init__(self, projects_dir: Path):
        self.root = Path(projects_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    # ----- caminhos -----
    def dir(self, project_id: str) -> Path:
        return self.root / project_id

    def sub(self, project_id: str, *parts: str) -> Path:
        p = self.dir(project_id).joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def rel(self, project_id: str, path: Path) -> str:
        return Path(path).resolve().relative_to(self.dir(project_id).resolve()).as_posix()

    def abs(self, project_id: str, rel: str | None) -> Path | None:
        return (self.dir(project_id) / rel) if rel else None

    # ----- CRUD -----
    def create(self, settings: Settings) -> Project:
        pid = f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(settings.hypothesis, 32)}"
        proj = Project(id=pid, settings=settings)
        self.dir(pid).mkdir(parents=True, exist_ok=True)
        self.save(proj)
        return proj

    def save(self, proj: Project) -> None:
        with _LOCK:
            d = self.dir(proj.id)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "project.json.tmp"
            tmp.write_text(proj.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, d / "project.json")

    def load(self, project_id: str) -> Project:
        with _LOCK:
            return Project.model_validate_json((self.dir(project_id) / "project.json").read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*/project.json"), reverse=True):
            try:
                proj = Project.model_validate_json(p.read_text(encoding="utf-8"))
                out.append({"id": proj.id, "title": proj.series_title or proj.settings.hypothesis,
                            "created": proj.created, "episodes": len(proj.episodes)})
            except Exception:  # noqa: BLE001 - projeto corrompido nao pode derrubar a lista
                continue
        return out

    def open_folder(self, project_id: str) -> str:
        """Abre a pasta no explorador do sistema. Devolve o caminho."""
        path = self.dir(project_id).resolve()
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return str(path)


def project_log_path(store: ProjectStore, project_id: str) -> Path:
    return store.sub(project_id, "logs") / "studio.log"


_TS = re.compile(r"\s+")


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%H:%M:%S} {_TS.sub(' ', line).strip()}\n")


def tail_log(path: Path, n: int = 200) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


def dump_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
