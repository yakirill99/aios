"""FastAPI-сервер дашборда AIOS: API прогона + раздача интерфейса.

Один процесс отдаёт и JSON, и статику — отдельный веб-сервер и сборка
фронта не нужны.

    uvicorn aios.dashboard.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aios.dashboard import runner, state

logging.basicConfig(
    level=os.getenv("AIOS_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

WEB_DIR = Path(os.getenv("AIOS_WEB_DIR", "ui/web")).expanduser()

app = FastAPI(
    title="AIOS — дашборд",
    version="0.1.0",
    description="Мультиагентная оптимизация управления фондом скважин",
)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/health", tags=["служебное"])
async def health() -> dict[str, object]:
    """Проверка живости — используется в healthcheck контейнера."""
    return {"status": "ok", "live_run": state.has_live_run(), **state.describe()}


@app.get("/api/state", tags=["прогон"])
async def get_state() -> JSONResponse:
    """Состояние оптимизации для сводного дашборда.

    Форма ответа совпадает с объектом `state` во фронте: npv, iteration,
    wells[], agents{}, logs[] и так далее.
    """
    try:
        data, source = state.load_state()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(data, headers={"X-Data-Source": source})


@app.get("/api/journal", tags=["прогон"])
async def get_journal() -> JSONResponse:
    """Журнал прогона: config, schedule, journal, wells, agents, matrix.

    То, что оставляет после себя цикл скользящего горизонта. Экран
    «Журнал прогона» читает только отсюда.
    """
    try:
        data, source = state.load_journal()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(data, headers={"X-Data-Source": source})


@app.post("/api/run/start", tags=["прогон"])
async def start_run() -> dict[str, object]:
    """Запускает цикл оптимизации в фоне.

    Возвращает управление сразу: расчёт идёт отдельным процессом и
    пишет результаты в каталог прогонов, откуда их забирает /api/state.
    """
    started, message = runner.start()
    return {"started": started, "message": message}


@app.get("/api/run/status", tags=["прогон"])
async def run_status() -> dict[str, object]:
    """Что сейчас с фоновым прогоном."""
    return runner.status()


@app.get("/api/artifacts/{filename}", tags=["прогон"])
async def get_artifact(filename: str) -> FileResponse:
    """Отдаёт артефакт прогона, например wells_schedule.inc."""
    path = state.artifact_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Файл '{filename}' не найден")
    return FileResponse(path, filename=path.name)


# ---------------------------------------------------------------------------
# Статика
# ---------------------------------------------------------------------------
# Монтируется ПОСЛЕ всех /api-маршрутов — иначе перехватит их на себя.

if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    log.warning("Каталог %s не найден — интерфейс отдаваться не будет", WEB_DIR)
