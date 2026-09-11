#!/usr/bin/env python3
"""
Добавляет в расписание обязательные остановки на ремонт.

    python3 add_repairs.py $DATA_ROOT/full34            # посмотреть, что будет
    python3 add_repairs.py $DATA_ROOT/full34 --write    # записать новый .inc

Берёт воздействия из result.json указанного прогона, добавляет
WELOPEN SHUT на начало ремонта и WELOPEN OPEN на его конец для всех
скважин, чьи ремонты попадают в расчётное окно, и пересобирает
wells_schedule.inc через ScheduleBuilder — тот же путь, которым
расписание собирает сам цикл.

Зачем отдельно от цикла: отбраковка кандидата запрещает АГЕНТУ трогать
скважину в ремонте, но в деке она продолжает работать. Остановки по
условию кейса обязательные, их надо внести явно.

После записи обязательны тест на круг и валидатор:
    ./run_schedule.sh --inc <новый .inc>
    ./validate.sh $DATA_ROOT/runs/sched --since <cut>
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schedule_builder import Action, ScheduleBuilder          # noqa: E402
from probe_wells import parse_schedule                         # noqa: E402

DATA = Path(os.environ.get("DATA_ROOT", Path.home() / "aios-data"))
AIOS = Path(os.environ.get("AIOS_ROOT", Path.home() / "aios"))
BASE = Path(os.environ.get("BASE_DECK", AIOS / "model" / "Model_Y_opm"))
EVENTS = Path(os.environ.get("EVENTS", AIOS / "configs" / "events_case.txt"))


def load_repairs(path: Path) -> list[tuple[str, date, date, str]]:
    """Читает файл событий тем же форматом, что constraints.load_events."""
    import re
    out = []
    if not path.exists():
        sys.exit(f"нет файла событий {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        p = re.split(r"[;\t]+|\s+", line)
        if len(p) < 3:
            continue
        try:
            out.append((p[0], date.fromisoformat(p[1]),
                        date.fromisoformat(p[2]),
                        " ".join(p[3:]) or "ремонт"))
        except ValueError:
            continue
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run = Path(sys.argv[1]).expanduser()
    write = "--write" in sys.argv

    res = json.loads((run / "result.json").read_text(encoding="utf-8"))
    cut = date.fromisoformat(res["cut"])
    end = date.fromisoformat(res["end"])
    print(f"прогон:  {run}")
    print(f"окно:    {cut} .. {end}")
    print(f"ЧДД до:  {res['npv']:.3f} млн, воздействий {res['n_actions']}")

    acts = [Action(date=a["date"], well=str(a["well"]), control=a["control"],
                   value=float(a["value"]), reason=a.get("reason", ""))
            for a in res["schedule"]]

    добавлено: list[Action] = []
    for well, s, e, why in load_repairs(EVENTS):
        if s >= end or e <= cut:
            continue
        if s >= cut:
            добавлено.append(Action(date=s.isoformat(), well=well,
                                    control="SHUT", value=0.0,
                                    reason=f"обязательная остановка: {why}"))
        if cut < e < end:
            добавлено.append(Action(date=e.isoformat(), well=well,
                                    control="OPEN", value=0.0,
                                    reason=f"возврат после ремонта: {why}"))

    if not добавлено:
        print("\nремонтов внутри окна нет — расписание менять не нужно")
        return 0

    print(f"\nдобавляется строк: {len(добавлено)}")
    for a in добавлено:
        print(f"  {a.date}  скв. {a.well}  {a.control}  — {a.reason}")

    base = BASE
    if base.is_dir():
        base = next(base.glob("INCLUDE/*sch*.inc"), None) or \
               next(base.glob("**/*sch*.inc"))
    print(f"дека:    {base}")
    modes = parse_schedule(base, cut)
    b = ScheduleBuilder(modes, cut, end)
    b.add_many(acts + добавлено)
    issues = b.validate()
    errs = [i for i in issues if getattr(i, "severity", "") == "error"]
    print(f"\nвалидатор расписания: {len(issues)} замечаний, ошибок {len(errs)}")
    for i in issues[:10]:
        print(f"  {i}")
    if errs:
        print("\nЕСТЬ ОШИБКИ — файл не записан")
        return 1

    out = run / "wells_schedule_repairs.inc"
    if not write:
        print(f"\nпробный запуск. Записать: {sys.argv[0]} {run} --write")
        return 0

    out.write_text(b.render(), encoding="utf-8")
    print(f"\nзаписано: {out}")
    print("\nДальше обязательно:")
    print(f"  ./run_schedule.sh --inc {out}")
    print(f"  ./validate.sh $DATA_ROOT/runs/sched --since {cut}")
    print("  число из прогона -> submission/npv_claimed.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
