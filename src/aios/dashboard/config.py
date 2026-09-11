"""Реестры деки и пресетов запуска + проверка параметров.

Схема параметров лежит в configs/run_presets.yaml и работает сразу в
двух ролях: по ней фронт рисует форму, по ней же бэкенд проверяет
пришедшее. За счёт этого форма и валидация не могут разъехаться.

Всё, чего нет в схеме, отвергается. Это граница доверия: дальше по
цепочке значения попадают в конфиг прогона и в командную строку.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("AIOS_CONFIG_DIR", "configs")).expanduser()
DECKS_FILE = CONFIG_DIR / "decks.yaml"
PRESETS_FILE = CONFIG_DIR / "run_presets.yaml"


class ValidationError(ValueError):
    """Параметры прогона не прошли проверку."""


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        log.warning("Не найден %s — соответствующий раздел будет пустым", path)
        return {}
    except (yaml.YAMLError, OSError) as exc:
        log.error("Не удалось разобрать %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Деки
# ---------------------------------------------------------------------------

def decks() -> list[dict[str, Any]]:
    """Список месторождений из реестра."""
    return _load_yaml(DECKS_FILE).get("decks") or []


def find_deck(deck_id: str) -> dict[str, Any] | None:
    return next((d for d in decks() if d.get("id") == deck_id), None)


def check_deck(deck_id: str) -> dict[str, Any]:
    """Проверяет, что дека есть в реестре и готова к прогону."""
    deck = find_deck(deck_id)
    if deck is None:
        known = ", ".join(d.get("id", "?") for d in decks()) or "реестр пуст"
        raise ValidationError(f"Дека '{deck_id}' не найдена. Доступны: {known}")
    if not deck.get("ready", False):
        raise ValidationError(
            f"Дека '{deck_id}' помечена как неготовая — сначала посчитайте планку"
        )
    return deck


# ---------------------------------------------------------------------------
# Пресеты и схема параметров
# ---------------------------------------------------------------------------

def presets() -> list[dict[str, Any]]:
    return _load_yaml(PRESETS_FILE).get("presets") or []


def param_schema() -> list[dict[str, Any]]:
    return _load_yaml(PRESETS_FILE).get("params") or []


def llm_profiles() -> dict[str, Any]:
    return _load_yaml(PRESETS_FILE).get("llm_profiles") or {}


def find_preset(preset_id: str) -> dict[str, Any] | None:
    return next((p for p in presets() if p.get("id") == preset_id), None)


def _check_one(spec: dict[str, Any], value: Any) -> Any:
    """Проверяет одно значение по его описанию в схеме."""
    name = spec.get("label", spec["id"])
    kind = spec.get("type", "str")

    if kind == "int":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{name}: ожидалось целое число, получено {value!r}")
        low, high = spec.get("min"), spec.get("max")
        if low is not None and number < low:
            raise ValidationError(f"{name}: не меньше {low}, получено {number}")
        if high is not None and number > high:
            raise ValidationError(f"{name}: не больше {high}, получено {number}")
        return number

    if kind == "date":
        text = str(value)
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValidationError(f"{name}: ожидалась дата ГГГГ-ММ-ДД, получено {value!r}")
        return text

    if kind == "enum":
        allowed = [str(o["value"]) for o in spec.get("options", [])]
        if str(value) not in allowed:
            raise ValidationError(
                f"{name}: допустимо {', '.join(allowed)}; получено {value!r}"
            )
        return str(value)

    raise ValidationError(f"{name}: неподдерживаемый тип параметра '{kind}'")


def validate_params(raw: dict[str, Any], preset_id: str | None = None) -> dict[str, Any]:
    """Сводит пресет и пользовательский ввод, проверяя всё по схеме.

    Порядок: значения пресета, поверх — то, что прислал пользователь.
    Ключи вне схемы отвергаются, а не игнорируются молча: молчаливое
    выбрасывание прячет опечатки в имени параметра.
    """
    schema = param_schema()
    by_id = {s["id"]: s for s in schema}

    merged: dict[str, Any] = {}
    if preset_id is not None:
        preset = find_preset(preset_id)
        if preset is None:
            known = ", ".join(p.get("id", "?") for p in presets()) or "список пуст"
            raise ValidationError(f"Пресет '{preset_id}' не найден. Доступны: {known}")
        merged.update(preset.get("params") or {})

    unknown = set(raw) - set(by_id)
    if unknown:
        raise ValidationError(f"Неизвестные параметры: {', '.join(sorted(unknown))}")
    merged.update(raw)

    checked = {key: _check_one(by_id[key], value)
               for key, value in merged.items() if key in by_id}

    # Профиль модели нужен только при живом вызове
    if checked.get("llm_mode") == "live":
        profile = checked.get("llm_profile")
        if not profile:
            raise ValidationError("Для режима 'live' нужно выбрать профиль модели")
        if profile not in llm_profiles():
            raise ValidationError(f"Профиль модели '{profile}' не описан в конфигурации")
    else:
        checked.pop("llm_profile", None)

    return checked
