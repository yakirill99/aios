# -*- coding: utf-8 -*-
"""
Мост между OPM Flow и официальным скриптом расчёта ЧДД.

    UNSMRY (сводки симулятора)  ->  CSV с 14 колонками, которые требует
                                    CHDD_PYTHON/chdd_model.py

Запуск (внутри контейнера, где стоит resdata):
    python3 summary_to_csv.py /out/MODEL_Y.SMSPEC -o data.csv

--------------------------------------------------------------------------
ПОЧЕМУ ЭТОТ СКРИПТ ВООБЩЕ НУЖЕН

1. OPM Flow НЕ поддерживает WOMT/WOMR — это ключевые слова tNavigator.
   Масса нефти считается из объёма через плотность из секции DENSITY.
   В Model_Y: 832 и 834 кг/м3 по двум PVT-регионам, разница 0.24%.

2. Скрипту ЧДД нужны месячные РАЗНОСТИ накопленных величин (*_Diff),
   а симулятор пишет накопленные значения. Разности считаем здесь.

3. THP и WEFF в формулы ЧДД не входят (проверено по коду: используются
   только для средних в отчёте). Заполняем нулём и единицей.

4. Скрипт ЧДД ОТКАЗЫВАЕТСЯ считать, если WLPR > 500 м3/сут.
   Проверяем здесь, чтобы не узнать об этом на последнем шаге.
--------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime

OIL_DENSITY = 0.833      # т/м3, среднее по DENSITY из pvt.inc (832/834 кг/м3)
MAX_WLPR = 500.0         # жёсткий предел методики

COLUMNS = ["DATA", "well", "WLPT", "WLPR", "WOMT", "WOMR", "WWIR", "WWIT",
           "THP", "BHP", "WEFF", "WLPT_Diff", "WOMT_Diff", "WWIT_Diff"]


def _prev_month(d: date) -> date:
    """Первое число предыдущего месяца."""
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def read_summary(path: str, oil_density: float, start: str | None = None):
    from resdata.summary import Summary

    s = Summary(path)
    keys = set(s.keys())

    # ВАЖНО: в сводке есть не только скважины, но и группы (GOPR:GROUP и т.п.).
    # Берём только те имена, у которых есть хотя бы один скважинный вектор
    # добычи или закачки — иначе в CSV попадут группы и число "скважин"
    # окажется завышенным.
    WELL_MARKERS = ("WOPR", "WLPR", "WWIR", "WBHP", "WOPT", "WWIT")
    candidates = {k.split(":", 1)[1] for k in keys if ":" in k}
    wells = sorted(w for w in candidates
                   if any(f"{m}:{w}" in keys for m in WELL_MARKERS))
    if not wells:
        sys.exit("в сводке нет скважинных векторов")
    dropped = sorted(candidates - set(wells))
    if dropped:
        print(f"  пропущено не-скважинных имён: {len(dropped)} "
              f"({', '.join(dropped[:5])})")

    dates = [d.date() if hasattr(d, "date") else d for d in s.dates]
    start_d = datetime.fromisoformat(start).date() if start else None

    def vec(name: str, well: str):
        key = f"{name}:{well}"
        if key not in keys:
            return [0.0] * len(dates)
        return list(s.numpy_vector(key))

    rows, warnings = [], []
    prev = {w: {"WLPT": 0.0, "WOMT": 0.0, "WWIT": 0.0} for w in wells}
    # накопленные на момент отсечки — база для первой разности
    base_cum: dict[str, dict] = {}

    for w in wells:
        wlpt, wlpr = vec("WLPT", w), vec("WLPR", w)
        wopt, wopr = vec("WOPT", w), vec("WOPR", w)
        wwit, wwir = vec("WWIT", w), vec("WWIR", w)
        bhp, thp = vec("WBHP", w), vec("WTHP", w)

        for i, d in enumerate(dates):
            if start_d and d < start_d:
                # до отсечки в выборку не пишем, но запоминаем накопленные:
                # от них будет считаться первая разность после отсечки
                base_cum[w] = {"WLPT": wlpt[i],
                               "WOMT": wopt[i] * oil_density,
                               "WWIT": wwit[i]}
                continue

            womt = wopt[i] * oil_density        # объём -> масса
            womr = wopr[i] * oil_density

            if wlpr[i] > MAX_WLPR:
                warnings.append(f"{w} {d}: WLPR={wlpr[i]:.1f} > {MAX_WLPR}")

            rows.append({
                # ВАЖНО: разность в точке T — это добыча за ПРЕДЫДУЩИЙ
                # месяц (накопленное на 01.02 минус на 01.01 = январь).
                # Помечаем месяцем добычи, иначе период съезжает на месяц
                # и декабрь 2015 «появляется», хотя разности для него нет.
                "DATA": _prev_month(d).isoformat(),
                "well": w,
                "WLPT": round(wlpt[i], 4),
                "WLPR": round(wlpr[i], 4),
                "WOMT": round(womt, 4),
                "WOMR": round(womr, 4),
                "WWIR": round(wwir[i], 4),
                "WWIT": round(wwit[i], 4),
                "THP": round(thp[i], 4),
                "BHP": round(bhp[i], 4),
                "WEFF": 1,
                "WLPT_Diff": round(wlpt[i] - prev[w]["WLPT"], 4),
                "WOMT_Diff": round(womt - prev[w]["WOMT"], 4),
                "WWIT_Diff": round(wwit[i] - prev[w]["WWIT"], 4),
            })
            prev[w] = {"WLPT": wlpt[i], "WOMT": womt, "WWIT": wwit[i]}

    # первая дата сводки — это момент старта расчёта (нулевые накопленные),
    # разности в ней не имеют смысла и только мусорят выборку
    if rows:
        first = min(r["DATA"] for r in rows)
        rows = [r for r in rows if r["DATA"] != first or
                (r["WLPT_Diff"] or r["WOMT_Diff"] or r["WWIT_Diff"])]

    # --- АГРЕГАЦИЯ ПО МЕСЯЦАМ ------------------------------------------
    # Симулятор пишет не только первые числа месяца: при дроблении шага
    # появляются промежуточные точки. Эталонный расчёт ЧДД ждёт ровно одну
    # запись на пару (скважина, месяц).
    #
    # Берём ПОСЛЕДНЮЮ точку месяца: накопленные величины на конец месяца —
    # это то, что нужно для разностей.
    by_key: dict[tuple, dict] = {}
    for r in rows:
        y, m, _ = r["DATA"].split("-")
        by_key[(r["well"], y, m)] = r          # последняя перезаписывает
    dropped = len(rows) - len(by_key)
    rows = list(by_key.values())

    # Разности пересчитываем ПОСЛЕ агрегации. Важно: отсчёт ведётся не от
    # нуля, а от накопленных НА МОМЕНТ ОТСЕЧКИ (base_cum). Иначе первая
    # строка после --start получит разность, равную всей истории с 2007 г.
    per_well: dict[str, list] = {}
    for r in rows:
        per_well.setdefault(r["well"], []).append(r)

    for w, rs in per_well.items():
        rs.sort(key=lambda r: r["DATA"])
        prev = dict(base_cum.get(w, {"WLPT": 0.0, "WOMT": 0.0, "WWIT": 0.0}))
        for r in rs:
            r["WLPT_Diff"] = round(r["WLPT"] - prev["WLPT"], 4)
            r["WOMT_Diff"] = round(r["WOMT"] - prev["WOMT"], 4)
            r["WWIT_Diff"] = round(r["WWIT"] - prev["WWIT"], 4)
            prev = {"WLPT": r["WLPT"], "WOMT": r["WOMT"], "WWIT": r["WWIT"]}

    # Строки с нулевыми разностями НЕ удаляем: нулевой дебит означает
    # остановленную скважину, а по этим записям эталон считает события
    # остановки и пуска. Раньше их отсев терял 21 событие за 2007 год.
    # Первая точка периода даёт разность за месяц ДО начала управления —
    # это история, в наш расчёт она не входит.
    if start_d:
        before = [r for r in rows if r["DATA"] < start_d.isoformat()]
        if before:
            rows = [r for r in rows if r["DATA"] >= start_d.isoformat()]
            print(f"  отброшено {len(before)} строк за месяц до начала "
                  f"управления")

    if dropped:
        print(f"  свёрнуто внутримесячных точек: {dropped}")

    if rows:
        months = sorted({r["DATA"][:7] for r in rows})
        print(f"  месяцев в расчёте: {len(months)} "
              f"({months[0]} .. {months[-1]})")

    rows.sort(key=lambda r: (r["DATA"], r["well"]))
    return rows, wells, warnings


def main() -> None:
    p = argparse.ArgumentParser(description="UNSMRY -> CSV для расчёта ЧДД")
    p.add_argument("smspec", help="путь к .SMSPEC")
    p.add_argument("-o", "--output", default="chdd_input.csv")
    p.add_argument("--start", help="начальная дата, напр. 2014-01-01")
    p.add_argument("--oil-density", type=float, default=OIL_DENSITY,
                   help=f"т/м3, по умолчанию {OIL_DENSITY}")
    a = p.parse_args()

    rows, wells, warnings = read_summary(a.smspec, a.oil_density, a.start)
    if not rows:
        sys.exit("нет строк — проверьте --start и содержимое сводки")

    with open(a.output, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=COLUMNS)
        wr.writeheader()
        wr.writerows(rows)

    print(f"записано: {a.output}")
    print(f"  строк: {len(rows)}, скважин: {len(wells)}")
    print(f"  период: {rows[0]['DATA']} -> {rows[-1]['DATA']}")
    print(f"  плотность нефти: {a.oil_density} т/м3")

    neg = sum(1 for r in rows if r["WLPT_Diff"] < 0
              or r["WOMT_Diff"] < 0 or r["WWIT_Diff"] < 0)
    if neg:
        print(f"  ! {neg} строк с отрицательными разностями — "
              f"скрипт ЧДД их исключит целиком")

    if warnings:
        print(f"\n  ! ПРЕВЫШЕНИЕ WLPR > {MAX_WLPR} в {len(warnings)} точках.")
        print("    Скрипт ЧДД откажется считать. Первые пять:")
        for w in warnings[:5]:
            print(f"      {w}")

    tot_oil = sum(r["WOMT_Diff"] for r in rows)
    tot_liq = sum(r["WLPT_Diff"] for r in rows)
    tot_inj = sum(r["WWIT_Diff"] for r in rows)
    print(f"\n  всего за период: нефть {tot_oil/1000:,.1f} тыс.т, "
          f"жидкость {tot_liq/1000:,.1f} тыс.т, закачка {tot_inj/1000:,.1f} тыс.м3")
    if tot_liq > 0:
        print(f"  обводнённость в среднем: {(1-tot_oil/tot_liq)*100:.1f}%")


if __name__ == "__main__":
    main()
