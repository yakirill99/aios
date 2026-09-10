"""Чтение состояния прогона с диска.

Здесь нет ни математики, ни обращений к OPM Flow, ни к агентам.
Модуль только читает файлы, которые оставляет после себя цикл
скользящего горизонта, и отдаёт их наверх. Связь строго
односторонняя: дашборд знает про расчёт, расчёт про дашборд не знает.

Два источника:

    ~/aios-data/rolling/    живой прогон (вне репозитория)
        journal.json        пишет rolling_horizon.py
        state.json          пишет aios.dashboard.collect

    ui/demo/                эталонный набор (лежит в git)
        journal.json
        state.json

Живое перекрывает демонстрационное. Журнал сшивается по верхним
ключам: если в живом journal.json есть config/schedule/journal, но нет
wells/agents/matrix, недостающее берётся из демо, а в ответе
проставляется, что именно откуда взято. Благодаря этому дашборд
открывается осмысленным на чистой машине и не ломается на половине
готовности расчёта.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _dir_from_env(var: str, default: str) -> Path:
    return Path(os.getenv(var, default)).expanduser()


LIVE_DIR = _dir_from_env("AIOS_LIVE_DIR", "~/aios-data/rolling")
DEMO_DIR = _dir_from_env("AIOS_DEMO_DIR", "ui/demo")
SUBMISSION_DIR = _dir_from_env("AIOS_SUBMISSION_DIR", "submission")

# Верхние ключи journal.json, которые дашборд умеет показывать
JOURNAL_KEYS = ("config", "schedule", "journal", "wells", "agents", "matrix")


def _read_json(path: Path) -> dict[str, Any] | None:
    """Читает JSON, возвращает None если файла нет или он повреждён."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        # Частая ситуация: цикл пишет файл прямо сейчас и мы поймали его
        # на середине. Не считаем это ошибкой, просто откатимся на демо.
        log.warning("Не удалось прочитать %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        log.warning("%s: ожидался объект, получен %s", path, type(data).__name__)
        return None
    return data


def load_state() -> tuple[dict[str, Any], str]:
    """Состояние для сводного дашборда. Возвращает (данные, источник)."""
    data = _read_json(LIVE_DIR / "state.json")
    if data is not None:
        return data, "live"

    data = _read_json(DEMO_DIR / "state.json")
    if data is not None:
        return data, "demo"

    raise FileNotFoundError(
        f"Нет ни {LIVE_DIR / 'state.json'}, ни {DEMO_DIR / 'state.json'}"
    )


def load_journal() -> tuple[dict[str, Any], str]:
    """Журнал прогона, сшитый из живого и демонстрационного.

    Возвращает (данные, источник), где источник — 'live', 'demo' или
    'mixed'. В данные добавляется служебный ключ `_source` с картой
    «ключ -> откуда взят», чтобы на защите было видно, что реальное.
    """
    live = _read_json(LIVE_DIR / "journal.json") or {}
    demo = _read_json(DEMO_DIR / "journal.json")

    if demo is None and not live:
        raise FileNotFoundError(
            f"Нет ни {LIVE_DIR / 'journal.json'}, ни {DEMO_DIR / 'journal.json'}"
        )
    demo = demo or {}

    merged: dict[str, Any] = {}
    origin: dict[str, str] = {}
    for key in JOURNAL_KEYS:
        if live.get(key):
            merged[key], origin[key] = live[key], "live"
        elif key in demo:
            merged[key], origin[key] = demo[key], "demo"

    # Ключи, которых нет в нашем списке, но которые появились в живом
    # журнале — пробрасываем как есть, вдруг пригодятся.
    for key, value in live.items():
        if key not in merged and not key.startswith("_"):
            merged[key], origin[key] = value, "live"

    kinds = set(origin.values())
    source = kinds.pop() if len(kinds) == 1 else "mixed"

    merged["_source"] = origin
    return merged, source


def artifact_path(filename: str) -> Path | None:
    """Путь к артефакту прогона: wells_schedule.inc, npv_claimed.txt и т.п.

    Имя чистится от разделителей пути, чтобы запрос вида
    `../../etc/passwd` не выходил за пределы разрешённых каталогов.
    """
    safe = os.path.basename(filename)
    if not safe or safe.startswith("."):
        return None

    for directory in (LIVE_DIR, SUBMISSION_DIR, DEMO_DIR):
        candidate = directory / safe
        if candidate.is_file():
            return candidate
    return None


def has_live_run() -> bool:
    """Есть ли на диске результаты живого прогона."""
    return (LIVE_DIR / "journal.json").is_file() or (LIVE_DIR / "state.json").is_file()


def describe() -> dict[str, str]:
    """Куда именно смотрит сервер — удобно для отладки на чужой машине."""
    return {
        "live_dir": str(LIVE_DIR),
        "demo_dir": str(DEMO_DIR),
        "submission_dir": str(SUBMISSION_DIR),
    }
