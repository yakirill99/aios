"""Переходник между расчётом и дашбордом.

ЭТО ЕДИНСТВЕННЫЙ ФАЙЛ, КОТОРЫЙ ПРИДЁТСЯ ПРАВИТЬ при подключении новых
данных. Скрипты расчёта продолжают писать то, что писали; здесь их
вывод читается и раскладывается в форму, которую ждёт интерфейс.

    python -m aios.dashboard.collect

Читает:
    ~/aios-data/rolling/journal.json      цикл скользящего горизонта
    ~/aios-data/probes/response_matrix.csv  матрица влияния (если есть)

Пишет:
    ~/aios-data/rolling/state.json        сводка для главного экрана
    ~/aios-data/rolling/journal.json      обогащённый журнал (wells/agents/matrix)

Запись атомарная — через временный файл и переименование. Без этого
дашборд может прочитать JSON на середине записи.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

LIVE_DIR = Path(os.getenv("AIOS_LIVE_DIR", "~/aios-data/rolling")).expanduser()
PROBES_DIR = Path(os.getenv("AIOS_PROBES_DIR", "~/aios-data/probes")).expanduser()


# ---------------------------------------------------------------------------
# Чтение исходников
# ---------------------------------------------------------------------------

def read_journal() -> dict[str, Any]:
    """Журнал цикла скользящего горизонта как есть."""
    path = LIVE_DIR / "journal.json"
    if not path.is_file():
        raise FileNotFoundError(f"Не найден {path} — сначала прогоните rolling_horizon.py")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_matrix() -> list[dict[str, Any]]:
    """Матрица влияния из response_matrix.csv.

    Ожидаются колонки source, observed, metric, peak_abs, lag_month.
    Если файла нет — возвращается пустой список, дашборд это переживёт.
    """
    path = PROBES_DIR / "response_matrix.csv"
    if not path.is_file():
        print(f"матрица влияния пропущена: нет {path}")
        return []

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                rows.append({
                    "source": str(row["source"]),
                    "observed": str(row["observed"]),
                    "metric": row.get("metric", "oil"),
                    "peak_abs": float(row["peak_abs"]),
                    "lag_month": int(float(row["lag_month"])),
                })
            except (KeyError, ValueError) as exc:
                print(f"строка матрицы пропущена ({exc}): {row}")
    return rows


def build_wells(journal: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Состояние фонда скважин.

    TODO: подставить реальные дебиты из summary_to_csv.py. Пока фонд
    восстанавливается по расписанию — какие скважины упоминались
    в управляющих воздействиях и что с ними делали последним шагом.
    Дашборд это показывает корректно, но дебиты будут нулевые.
    """
    wells: dict[str, dict[str, Any]] = {}
    for action in journal.get("schedule", []):
        well_id = str(action.get("well"))
        control = action.get("control", "")
        is_injector = control.upper().startswith(("W", "GRAT", "WRAT")) and "INJ" in control.upper()
        wells.setdefault(well_id, {
            "type": "inj" if is_injector else "prod",
            "status": "active",
            "oilRate": 0,
            "liquidRate": 0,
            "watercut": 0,
        })
        wells[well_id]["lastAction"] = f"{control} {action.get('value')} ({action.get('date')})"
    return wells


def read_result() -> dict[str, Any]:
    """result.json цикла: там лежит правда про агентный слой.

    Ключи llm_mode / llm_mode_effective / llm_steps / rule_steps пишет
    сам rolling_horizon.py. Именно по ним видно, работали агенты или
    цикл весь прогон шёл на эвристиках.
    """
    path = LIVE_DIR / "result.json"
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}


def build_agents(journal: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Карточки агентов по фактическим данным прогона.

    Раньше здесь стояли выдуманные статусы: карточки «работали» всегда,
    даже когда цикл шёл с --llm off и агентного слоя не существовало.
    На защите это прямой повод для неудобного вопроса.
    """
    steps = journal.get("journal", [])
    last = steps[-1] if steps else {}

    requested = result.get("llm_mode") or journal.get("config", {}).get("llm_mode", "off")
    effective = result.get("llm_mode_effective", "none")
    llm_steps = int(result.get("llm_steps", 0) or 0)
    rule_steps = int(result.get("rule_steps", 0) or 0)

    if requested == "off":
        status, note = "idle", "Не использовался: прогон шёл на правилах (--llm off)"
    elif llm_steps == 0:
        status, note = "idle", f"Запрошен режим {requested}, но ни один шаг его не задействовал"
    else:
        status = "active"
        note = f"Шагов с агентом: {llm_steps}, на правилах: {rule_steps}"
        if effective and effective != requested:
            note += f". ВНИМАНИЕ: запрошен {requested}, фактически {effective}"

    def card(name: str, description: str, action: str) -> dict[str, Any]:
        return {"name": name, "status": status, "description": description,
                "lastAction": action if status == "active" else note}

    return [
        card("Аналитик", "Анализирует состояние фонда",
             last.get("analyst_summary") or "—"),
        card("Стратег", "Отбирает оптимальные решения",
             last.get("chosen_tag") or "—"),
        card("Критик", "Объясняет принятые решения",
             last.get("explanation") or last.get("risk") or "—"),
        {"name": "Оркестратор", "status": "active" if steps else "idle",
         "description": "Управляет циклом оптимизации",
         "lastAction": f"Шагов выполнено: {len(steps)}" if steps else "Прогонов не было"},
    ]


# ---------------------------------------------------------------------------
# Сводка для главного экрана
# ---------------------------------------------------------------------------

def build_state(journal: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Сводит журнал в короткое состояние.

    Ничего не пересчитывает — только агрегирует то, что уже посчитал
    расчёт, чтобы цифры на двух экранах не разъехались.
    """
    result = result or {}
    steps = journal.get("journal", [])
    last = steps[-1] if steps else {}
    wells = journal.get("wells", {})
    config = journal.get("config", {})

    # Статусы агентов берём из тех же карточек, что видит журнал —
    # два экрана не должны расходиться.
    cards = {c["name"]: c for c in journal.get("agents", [])}

    def agent_state(name: str) -> str:
        card = cards.get(name)
        if card is None:
            return "—"
        return "Активен" if card.get("status") == "active" else "Не задействован"

    rows = []
    for well_id, w in wells.items():
        is_prod = w.get("type") == "prod"
        rows.append({
            "id": ("P" if is_prod else "I") + str(well_id),
            "type": "producer" if is_prod else "injector",
            "status": "Активна" if w.get("status") == "active" else "Закрыта",
            "rate": w.get("oilRate" if is_prod else "liquidRate", 0),
            "wc": w.get("watercut") if is_prod else None,
        })

    npv = last.get("npv_window", 0)
    noop = last.get("npv_noop", 0)

    logs = [
        {
            "time": s.get("date", ""),
            "msg": f"Шаг {s.get('step')}: {s.get('analyst_summary') or s.get('chosen_tag', '')}",
            "type": "success" if s.get("gain", 0) > 0 else "info",
        }
        for s in steps
    ]

    return {
        "npv": npv,
        "baselineNpv": noop,
        "improvement": last.get("gain", npv - noop),
        "iteration": last.get("step", 0),
        "wellsActive": sum(1 for r in rows if r["status"] == "Активна"),
        "wellsTotal": len(rows),
        "progress": _progress(config, steps),
        "candidates": last.get("n_candidates", config.get("max_candidates", 0)),
        "llmMode": result.get("llm_mode_effective") or config.get("llm_mode", "off"),
        "autoUpdate": True,
        "logAuto": True,
        "logCount": len(logs),
        "agents": {
            "analyst": agent_state("Аналитик"),
            "strategist": agent_state("Стратег"),
            "critic": agent_state("Критик"),
        },
        "wells": rows,
        "logs": logs,
    }


def _progress(config: dict[str, Any], steps: list[dict[str, Any]]) -> int:
    """Доля пройденного горизонта в процентах.

    Считается по числу шагов от cut до end с шагом step_months.
    Если дат в конфиге нет — возвращает 0, а не выдумывает число.
    """
    try:
        start_year, start_month = (int(x) for x in config["cut"].split("-")[:2])
        end_year, end_month = (int(x) for x in config["end"].split("-")[:2])
        step = int(config.get("step_months", 3)) or 3
    except (KeyError, ValueError, AttributeError):
        return 0

    months = (end_year - start_year) * 12 + (end_month - start_month)
    total = max(months // step, 1)
    return min(round(len(steps) / total * 100), 100)


# ---------------------------------------------------------------------------

def write_atomic(path: Path, data: Any) -> None:
    """Пишет JSON через временный файл — читатель не увидит половину."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    print(f"записано: {path}")


def main() -> int:
    try:
        journal = read_journal()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ошибка: {exc}")
        return 1

    result = read_result()
    journal.setdefault("wells", build_wells(journal))
    journal["agents"] = build_agents(journal, result)
    journal.setdefault("matrix", read_matrix())

    write_atomic(LIVE_DIR / "journal.json", journal)
    write_atomic(LIVE_DIR / "state.json", build_state(journal, result))

    llm = result.get("llm_mode_effective", "нет result.json")
    print(f"агентный слой: {llm} | шагов с агентом: {result.get('llm_steps', '?')}")

    steps = len(journal.get("journal", []))
    print(f"шагов: {steps} | скважин: {len(journal['wells'])} | связей: {len(journal['matrix'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
