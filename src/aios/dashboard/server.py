"""FastAPI-сервер дашборда AIOS: API прогона + раздача интерфейса.

Один процесс отдаёт и JSON, и статику — отдельный веб-сервер и сборка
фронта не нужны.

    uvicorn aios.dashboard.server:app --host 0.0.0.0 --port 8000

Сервер ничего не считает и не запускает симулятор. Задание на прогон он
кладёт в очередь, а забирает его воркер — см. aios.dashboard.worker.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aios.dashboard import config, jobs, state

logging.basicConfig(
    level=os.getenv("AIOS_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

WEB_DIR = Path(os.getenv("AIOS_WEB_DIR", "ui/web")).expanduser()

# Сборка для организаторов ставит AIOS_READONLY=1: меню запуска
# пропадает, ручки запуска отвечают отказом. Жюри не сможет случайно
# запустить получасовой расчёт посреди защиты.
READONLY = os.getenv("AIOS_READONLY", "").strip().lower() in ("1", "true", "yes")

app = FastAPI(
    title="AIOS — дашборд",
    version="0.2.0",
    description="Мультиагентная оптимизация управления фондом скважин",
)


def _deny_if_readonly() -> None:
    if READONLY:
        raise HTTPException(
            status_code=403,
            detail="Интерфейс собран в режиме только для просмотра",
        )


# ---------------------------------------------------------------------------
# Служебное
# ---------------------------------------------------------------------------

@app.get("/health", tags=["служебное"])
async def health() -> dict[str, object]:
    """Живость — используется в healthcheck контейнера."""
    queue_ok, queue_note = jobs.writable()
    return {
        "status": "ok",
        "live_run": state.has_live_run(),
        "queue_writable": queue_ok,
        "queue_note": queue_note,
        **state.describe(),
    }


@app.get("/api/meta", tags=["служебное"])
async def meta() -> dict[str, object]:
    """Что фронт должен знать о сборке: режим, очередь, источники данных."""
    queue_ok, queue_note = jobs.writable()
    return {
        "readonly": READONLY,
        "queue_dir": str(jobs.QUEUE_DIR),
        "queue_writable": queue_ok,
        "queue_note": queue_note,
        "decks_configured": len(config.decks()),
        "presets_configured": len(config.presets()),
        **state.describe(),
    }


# ---------------------------------------------------------------------------
# Данные прогона
# ---------------------------------------------------------------------------

@app.get("/api/state", tags=["прогон"])
async def get_state() -> JSONResponse:
    """Сводка для главного экрана."""
    try:
        data, source = state.load_state()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(data, headers={"X-Data-Source": source})


@app.get("/api/journal", tags=["прогон"])
async def get_journal() -> JSONResponse:
    """Журнал прогона: config, schedule, journal, wells, agents, matrix."""
    try:
        data, source = state.load_journal()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(data, headers={"X-Data-Source": source})


@app.get("/api/artifacts/{filename}", tags=["прогон"])
async def get_artifact(filename: str) -> FileResponse:
    """Артефакт прогона: wells_schedule.inc, npv_claimed.txt."""
    path = state.artifact_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Файл '{filename}' не найден")
    return FileResponse(path, filename=path.name)


# ---------------------------------------------------------------------------
# Реестры: дека и пресеты
# ---------------------------------------------------------------------------

@app.get("/api/decks", tags=["запуск"])
async def get_decks() -> dict[str, object]:
    """Месторождения из реестра configs/decks.yaml."""
    return {"decks": config.decks()}


@app.get("/api/presets", tags=["запуск"])
async def get_presets() -> dict[str, object]:
    """Пресеты и схема параметров — по ней фронт рисует форму."""
    return {
        "presets": config.presets(),
        "params": config.param_schema(),
        "llm_profiles": sorted(config.llm_profiles()),
    }


# ---------------------------------------------------------------------------
# Очередь прогонов
# ---------------------------------------------------------------------------

@app.post("/api/run/start", tags=["запуск"])
async def start_run(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, object]:
    """Ставит прогон в очередь.

    Тело: {"deck": "model_y", "preset": "demo", "params": {...}}.
    Всё проверяется по схеме из configs/run_presets.yaml; параметр вне
    схемы или число вне диапазона приводят к отказу.
    """
    _deny_if_readonly()

    running = jobs.active()
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Уже идёт прогон {running['run_id']} — дождитесь завершения",
        )

    deck_id = body.get("deck")
    if not deck_id:
        raise HTTPException(status_code=400, detail="Не выбрана дека")

    preset_id = body.get("preset")
    raw_params = body.get("params") or {}
    if not isinstance(raw_params, dict):
        raise HTTPException(status_code=400, detail="params должен быть объектом")

    try:
        deck = config.check_deck(str(deck_id))
        params = config.validate_params(raw_params, preset_id)
    except config.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    preset = config.find_preset(preset_id) if preset_id else None
    try:
        job = jobs.submit(deck, preset, params)
    except jobs.QueueUnavailable as exc:
        # Не 500: это не сбой сервера, а окружение, и починить его может
        # только человек — поэтому текст должен доехать до интерфейса.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"started": True, "message": "Прогон поставлен в очередь", "job": job}


@app.get("/api/run/status", tags=["запуск"])
async def run_status() -> dict[str, object]:
    """Текущее задание и несколько последних."""
    return {
        "readonly": READONLY,
        "active": jobs.active(),
        "recent": jobs.listing(10),
    }


@app.get("/api/run/{run_id}", tags=["запуск"])
async def run_detail(run_id: str) -> dict[str, Any]:
    job = jobs.read(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Задание '{run_id}' не найдено")
    return job


@app.post("/api/run/{run_id}/cancel", tags=["запуск"])
async def run_cancel(run_id: str) -> dict[str, object]:
    """Помечает задание отменённым.

    Идущий симулятор мгновенно не останавливается: воркер увидит
    отметку и не начнёт следующий шаг горизонта.
    """
    _deny_if_readonly()
    job = jobs.cancel(run_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Задание '{run_id}' не найдено или уже завершено",
        )
    return {"cancelled": True, "job": job}


# ---------------------------------------------------------------------------
# Статика
# ---------------------------------------------------------------------------
# Монтируется ПОСЛЕ всех /api-маршрутов — иначе перехватит их на себя.

if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    log.warning("Каталог %s не найден — интерфейс отдаваться не будет", WEB_DIR)
