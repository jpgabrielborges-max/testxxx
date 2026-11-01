"""Execução em segundo plano com progresso REAL (contagem de unidades concluídas), logs e cancelamento."""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional


class Cancelled(Exception):
    pass


@dataclass
class JobState:
    name: str
    status: str = "running"  # running | done | error | cancelled
    step: str = ""
    total: int = 0
    done: int = 0
    error: Optional[str] = None
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    logs: list[str] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        return 0.0 if self.total <= 0 else min(1.0, self.done / self.total)


class JobContext:
    """Passado para as funções do pipeline. Cada chamada a advance() representa trabalho que de fato terminou."""

    def __init__(self, state: JobState, log_sink: Callable[[str], None] | None = None):
        self.state = state
        self._cancel = threading.Event()
        self._sink = log_sink

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self.state.logs.append(line)
        if self._sink:
            self._sink(msg)

    def plan(self, total: int) -> None:
        self.state.total = total
        self.state.done = 0

    def step(self, label: str) -> None:
        self.state.step = label
        self.log(label)

    def advance(self, n: int = 1) -> None:
        self.state.done += n

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()


class JobRunner:
    """Um job por vez (a GPU é compartilhada, então rodar em paralelo só causaria falta de VRAM)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current: Optional[JobContext] = None
        self.history: list[JobState] = []
        self._thread: Optional[threading.Thread] = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, name: str, fn: Callable[[JobContext], None], log_sink: Callable[[str], None] | None = None) -> JobContext:
        with self._lock:
            if self.busy:
                raise RuntimeError("Já existe uma tarefa em andamento. Aguarde ou cancele.")
            state = JobState(name=name)
            ctx = JobContext(state, log_sink)
            self.current = ctx
            self.history.append(state)
            self.history = self.history[-20:]

            def target():
                try:
                    ctx.log(f"Início: {name}")
                    fn(ctx)
                    state.status = "done"
                    ctx.log(f"Concluído: {name}")
                except Cancelled:
                    state.status = "cancelled"
                    ctx.log("Cancelado pelo usuário")
                except Exception as exc:  # noqa: BLE001 - o erro vai inteiro para o log e para a UI
                    state.status = "error"
                    state.error = f"{type(exc).__name__}: {exc}"
                    ctx.log(f"ERRO: {state.error}")
                    ctx.log(traceback.format_exc())
                finally:
                    state.finished = time.time()

            self._thread = threading.Thread(target=target, name=f"job-{name}", daemon=True)
            self._thread.start()
            return ctx

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)
