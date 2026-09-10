"""Запуск цикла оптимизации в фоне.

Сервер НЕ импортирует расчётный код и ничего о нём не знает. Он
запускает команду из AIOS_RUN_CMD как отдельный процесс. Поэтому
scripts/rolling_horizon.py и всё, что за ним стоит, править не нужно.

    AIOS_RUN_CMD=python scripts/rolling_horizon.py --config configs/run.yaml

Если переменная не задана, ручка /api/run/start честно отвечает, что
запускать нечего, и дашборд продолжает работать на демо-данных.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

RUN_CMD = os.getenv("AIOS_RUN_CMD", "").strip()
LIVE_DIR = Path(os.getenv("AIOS_LIVE_DIR", "~/aios-data/rolling")).expanduser()

_process: subprocess.Popen | None = None
_started_at: str | None = None


def start() -> tuple[bool, str]:
    """Запускает прогон. Возвращает (успех, сообщение для интерфейса)."""
    global _process, _started_at

    if not RUN_CMD:
        return False, "Команда прогона не задана (AIOS_RUN_CMD) — работаем на демо-данных"

    if _process is not None and _process.poll() is None:
        return False, "Прогон уже идёт"

    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        # Вывод уходит в файл, а не в пайп: иначе процесс встанет,
        # когда буфер трубы заполнится, а читать её здесь некому.
        logfile = (LIVE_DIR / "dashboard_run.log").open("a", encoding="utf-8")
        _process = subprocess.Popen(
            shlex.split(RUN_CMD),
            stdout=logfile,
            stderr=subprocess.STDOUT,
            cwd=os.getcwd(),
        )
    except (OSError, ValueError) as exc:
        log.exception("Не удалось запустить прогон")
        return False, f"Не удалось запустить прогон: {exc}"

    _started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("Прогон запущен: %s (pid %s)", RUN_CMD, _process.pid)
    return True, "Прогон запущен"


def status() -> dict[str, object]:
    """Состояние фонового процесса."""
    if not RUN_CMD:
        return {"configured": False, "running": False, "detail": "AIOS_RUN_CMD не задан"}

    if _process is None:
        return {"configured": True, "running": False, "detail": "Прогон ещё не запускался"}

    code = _process.poll()
    if code is None:
        return {
            "configured": True,
            "running": True,
            "pid": _process.pid,
            "started_at": _started_at,
        }

    return {
        "configured": True,
        "running": False,
        "exit_code": code,
        "started_at": _started_at,
        "detail": "Прогон завершён успешно" if code == 0 else f"Прогон упал, код {code}",
    }
