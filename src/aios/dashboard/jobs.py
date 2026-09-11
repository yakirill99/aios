"""Очередь заданий на прогон.

Дашборд и симулятор живут в разных контейнерах: в образе дашборда нет
OPM Flow, и это правильно — иначе каждая правка вёрстки пересобирала бы
симулятор. Связываются они через каталог с заданиями.

    дашборд ──> queue/<run_id>.json ──> воркер (с OPM) ──> rolling/<run_id>/

Ни docker-сокета внутрь контейнера, ни вложенного докера. Дашборд умеет
только положить файл; всё, что запускает расчёт, живёт в воркере.

Состояния задания: queued -> running -> done | failed. Отменённое
руками задание получает cancelled.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

QUEUE_DIR = Path(os.getenv("AIOS_QUEUE_DIR", "~/aios-data/queue")).expanduser()

ACTIVE = ("queued", "running")


class QueueUnavailable(RuntimeError):
    """В каталог очереди нельзя писать.

    Самая частая причина: каталог ~/aios-data/queue не существовал на
    хосте, Docker создал его от root, а контейнер работает под appuser.
    """


def writable() -> tuple[bool, str]:
    """Проверяет, что в очередь можно писать. Возвращает (можно, пояснение)."""
    try:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        probe = QUEUE_DIR / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, "доступна на запись"
    except OSError as exc:
        return False, f"{QUEUE_DIR}: {exc.strerror or exc}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(run_id: str) -> Path:
    # run_id генерируем сами, но на вход он приходит из URL — чистим
    safe = os.path.basename(run_id)
    return QUEUE_DIR / f"{safe}.json"


def _write(job: dict[str, Any]) -> None:
    """Атомарная запись: воркер может читать каталог в этот же момент."""
    try:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        target = _path(job["run_id"])
        tmp = target.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        tmp.replace(target)
    except OSError as exc:
        raise QueueUnavailable(
            f"Не удалось записать задание в {QUEUE_DIR}: {exc.strerror or exc}. "
            f"Проверьте права на каталог очереди."
        ) from exc


def read(run_id: str) -> dict[str, Any] | None:
    try:
        with _path(run_id).open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Задание %s не читается: %s", run_id, exc)
        return None


def listing(limit: int = 20) -> list[dict[str, Any]]:
    """Задания, новые сверху."""
    if not QUEUE_DIR.is_dir():
        return []
    jobs = []
    for path in QUEUE_DIR.glob("*.json"):
        try:
            with path.open(encoding="utf-8") as f:
                jobs.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs[:limit]


def active() -> dict[str, Any] | None:
    """Задание, которое сейчас в очереди или считается."""
    return next((j for j in listing(50) if j.get("status") in ACTIVE), None)


def submit(deck: dict[str, Any], preset: dict[str, Any] | None,
           params: dict[str, Any]) -> dict[str, Any]:
    """Кладёт задание в очередь. Возвращает его целиком."""
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job = {
        "run_id": run_id,
        "status": "queued",
        "created_at": now(),
        "deck": {"id": deck["id"], "name": deck.get("name"), "path": deck.get("path")},
        "preset": {"id": preset["id"], "name": preset.get("name")} if preset else None,
        "instant": bool(preset and preset.get("instant")),
        "params": params,
        "out_dir": f"rolling/{run_id}",
        "log": [],
    }
    _write(job)
    log.info("Задание поставлено в очередь: %s (дека %s)", run_id, deck["id"])
    return job


def update(run_id: str, **fields: Any) -> dict[str, Any] | None:
    """Меняет поля задания. Используется воркером."""
    job = read(run_id)
    if job is None:
        return None
    job.update(fields)
    job["updated_at"] = now()
    _write(job)
    return job


def append_log(run_id: str, message: str) -> None:
    job = read(run_id)
    if job is None:
        return
    job.setdefault("log", []).append({"time": now(), "message": message})
    job["log"] = job["log"][-50:]
    _write(job)


def cancel(run_id: str) -> dict[str, Any] | None:
    """Помечает задание отменённым.

    Запущенный процесс этим не убивается — воркер увидит отметку и не
    станет начинать следующий этап. Мгновенной остановки симулятора тут
    нет и не предполагается.
    """
    job = read(run_id)
    if job is None or job.get("status") not in ACTIVE:
        return None
    return update(run_id, status="cancelled", detail="Отменено из интерфейса")


def next_queued() -> dict[str, Any] | None:
    """Самое старое задание в очереди. Точка входа воркера."""
    queued = [j for j in listing(50) if j.get("status") == "queued"]
    return queued[-1] if queued else None
