#!/usr/bin/env python3
"""
Проверяет сданное расписание против ограничений валидационного кейса.

    python3 check_case_compliance.py

Ничего не меняет — только отвечает на три вопроса:
  1. Укладываемся ли в лимиты 4000 м3/сут по жидкости и закачке;
  2. Есть ли в расписании воздействия в периоды ремонтов;
  3. Какие ремонты попадают в расчётное окно и требуют остановки.

Третий пункт — главный: остановки обязательные, и если скважина в
период ремонта продолжает работать в модели, расписание ограничениям
не соответствует, даже когда агент её не трогал.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

AIOS = Path(os.environ.get("AIOS_ROOT", Path.home() / "aios"))
DATA = Path(os.environ.get("DATA_ROOT", Path.home() / "aios-data"))

CUT = date(2014, 1, 1)
END = date(2015, 12, 1)
CAP_LIQ = 4000.0
CAP_INJ = 4000.0

REPAIRS = [
    ("40", date(2013, 4, 1), date(2013, 8, 1)),
    ("26", date(2014, 10, 1), date(2015, 2, 1)),
    ("30", date(2015, 5, 1), date(2015, 9, 1)),
    ("37", date(2015, 12, 1), date(2016, 3, 1)),
    ("25", date(2016, 6, 1), date(2016, 10, 1)),
    ("24", date(2017, 2, 1), date(2017, 5, 1)),
    ("45", date(2017, 9, 1), date(2018, 1, 1)),
    ("42", date(2018, 4, 1), date(2018, 9, 1)),
]


def head(t: str) -> None:
    print(f"\n\033[1m{t}\033[0m")


def main() -> int:
    head("1. Лимиты по фонду: 4000 м3/сут жидкость, 4000 закачка")
    vpath = DATA / "runs" / "sched" / "validation.json"
    if vpath.exists():
        rep = json.loads(vpath.read_text(encoding="utf-8"))
        rep = rep if isinstance(rep, list) else [rep]
        ok = True
        for r in rep:
            liq = float(r.get("peak_liquid_rate", 0))
            inj = float(r.get("peak_injection_rate", 0))
            mark_l = "ок" if liq <= CAP_LIQ else "ПРЕВЫШЕНИЕ"
            mark_i = "ок" if inj <= CAP_INJ else "ПРЕВЫШЕНИЕ"
            print(f"  {r.get('scope', '?')}: жидкость {liq:,.0f} — {mark_l}; "
                  f"закачка {inj:,.0f} — {mark_i}")
            ok = ok and liq <= CAP_LIQ and inj <= CAP_INJ
        print("  вывод: в лимиты укладываемся" if ok
              else "  вывод: ЛИМИТЫ НАРУШЕНЫ, расписание надо пересчитывать")
    else:
        print(f"  нет {vpath} — сначала ./run_schedule.sh и ./validate.sh")

    head("2. Воздействия в периоды ремонтов")
    rj = DATA / "full34" / "result.json"
    if not rj.exists():
        print(f"  нет {rj}")
        return 1
    sched = json.loads(rj.read_text(encoding="utf-8")).get("schedule", [])
    bad = []
    for a in sched:
        d = date.fromisoformat(a["date"])
        for w, s, e in REPAIRS:
            if str(a["well"]) == w and s <= d < e:
                bad.append((a["date"], w, a.get("control"), a.get("value")))
    if bad:
        for d, w, c, v in bad:
            print(f"  {d}  скв. {w}  {c} {v} — в период ремонта")
        print("  вывод: ВОЗДЕЙСТВИЯ НЕДОПУСТИМЫ, убрать из расписания")
    else:
        print(f"  проверено воздействий: {len(sched)} — ни одного в ремонт")

    head("3. Ремонты внутри расчётного окна")
    inside = [(w, s, e) for w, s, e in REPAIRS if s < END and e > CUT]
    if not inside:
        print("  ни один ремонт не попадает в окно")
    for w, s, e in inside:
        print(f"  скв. {w}: {s} .. {e}")
    print("\n  Остановки обязательные. Если в деке эти скважины в указанные")
    print("  месяцы продолжают работать, расписание ограничениям не")
    print("  соответствует, даже когда агент их не трогал. Нужны строки")
    print("  WELOPEN SHUT на начало и WELOPEN OPEN на конец периода.")

    head("4. Что делать дальше")
    print("  Вариант А (надёжный, ~45 мин): пересчитать цикл с ограничениями")
    print("    python3 rolling_horizon.py run --llm off --candidates 8 \\")
    print("        --constraints ../configs/constraints_case.yaml \\")
    print("        --events ../configs/events_case.txt \\")
    print("        --workdir $DATA_ROOT/case8")
    print("  Вариант Б (быстрый, ~10 мин): добавить остановки в готовое")
    print("    расписание скриптом add_repairs.py и прогнать тест на круг.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
