# -*- coding: utf-8 -*-
"""
OutputValidator — проверка физической корректности результатов на ВСЁМ
горизонте.

Зачем: организаторы прямо указали, что физически некорректные значения в
выходных файлах расцениваются как баг и грозят дисквалификацией. Проверка
идёт по каждому шагу и каждой скважине, а не по итоговым суммам.

Запускается ВНУТРИ контейнера (нужен resdata):

    python3 validate_output.py /out/MODEL_Y.SMSPEC
    python3 validate_output.py /out/MODEL_Y.SMSPEC --since 2014-01-01
    python3 validate_output.py /out/MODEL_Y.SMSPEC --json /out/validation.json

Код возврата: 0 — чисто, 1 — есть нарушения.

Д8. Флаг --since печатает ОТДЕЛЬНУЮ сводку по управляющему периоду.
Нарушение внутри истории — вопрос к деке организаторов, нарушение внутри
нашего окна — наша дисквалификация. Разница существенная, и её видно
только если считать отдельно.

Д9. Проверка компенсации закачки. Лектор предупредил, что оптимизатор
«в лоб» разгоняет закачку в максимум. Порог задаётся, потому что точная
формула — зона ответственности гидродинамика; по умолчанию нарушение
даётся предупреждением, а не ошибкой.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict, field

# --- пределы --------------------------------------------------------------
# Значения по умолчанию совпадают с configs/constraints.yaml. Если модуль
# constraints доступен, пределы читаются оттуда: правка конфигурации
# должна влиять на поведение, иначе файл декоративен.
MAX_LIQUID_RATE = 500.0     # м3/сут — выше методика ЧДД не считает
MIN_BHP_PROD = 50.0         # бар — техминимум добывающих (из WCONPROD)
MAX_BHP_INJ = 300.0         # бар — предел нагнетательных (из WCONINJE)
MAX_PRESSURE = 500.0        # бар — здравый смысл для пластового
MIN_PRESSURE = 1.0
TOL = 1e-6                  # допуск на численный шум

# компенсация: отношение накопленной закачки к накопленному отбору
# жидкости за проверяемый период
COMP_MIN = 0.80
COMP_MAX = 1.20


@dataclass
class Issue:
    severity: str           # "error" | "warning"
    check: str
    well: str
    date: str
    value: float
    limit: float
    message: str


@dataclass
class Report:
    total_steps: int = 0
    total_wells: int = 0
    period: str = ""
    scope: str = "весь горизонт"
    compensation: float | None = None
    peak_liquid_rate: float | None = None
    peak_injection_rate: float | None = None
    oil_period_kt: float | None = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    checks_run: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_limits(path: str | None = None):
    """Пределы из configs/constraints.yaml, если модуль доступен."""
    try:
        from constraints import Constraints
    except ImportError:
        return None
    try:
        return Constraints.load(path)
    except Exception as e:                       # noqa: BLE001
        print(f"  ! не удалось прочитать ограничения: {e}")
        return None


def validate(smspec: str, max_report: int = 8, since=None,
             comp_min: float = COMP_MIN, comp_max: float = COMP_MAX,
             comp_hard: bool = False, limits=None) -> Report:
    from resdata.summary import Summary

    # Пределы уровня скважины — из конфигурации, если она прочиталась.
    max_liq = limits.max_liquid_rate if limits else MAX_LIQUID_RATE
    min_bhp = limits.min_bhp_producer if limits else MIN_BHP_PROD
    max_bhp = limits.max_bhp_injector if limits else MAX_BHP_INJ
    if limits:
        comp_min = limits.compensation_min
        comp_max = limits.compensation_max

    s = Summary(smspec)
    keys = set(s.keys())
    all_dates = [d.date() if hasattr(d, "date") else d for d in s.dates]

    # Д8. Окно проверки. Индексы срезаются от даты передачи управления,
    # а не от начала сводки: нумерация шагов обязана совпадать с той,
    # по которой считается ЧДД.
    if since:
        idx = [i for i, d in enumerate(all_dates) if d >= since]
        if not idx:
            raise SystemExit(f"в сводке нет точек начиная с {since}")
        i0 = idx[0]
    else:
        i0 = 0
    dates = all_dates[i0:]

    wells = sorted({k.split(":", 1)[1] for k in keys if ":" in k
                    and any(f"{m}:{k.split(':',1)[1]}" in keys
                            for m in ("WOPR", "WLPR", "WWIR"))})

    rep = Report(total_steps=len(dates), total_wells=len(wells),
                 period=f"{dates[0]} .. {dates[-1]}" if dates else "")

    rep.scope = (f"управляющий период с {since}" if since
                 else "весь горизонт")

    def vec(name: str, w: str):
        k = f"{name}:{w}"
        return list(s.numpy_vector(k))[i0:] if k in keys else None

    def err(check, well, i, val, lim, msg):
        rep.errors.append(asdict(Issue("error", check, well,
                                       dates[i].isoformat(), val, lim, msg)))

    def warn(check, well, i, val, lim, msg):
        rep.warnings.append(asdict(Issue("warning", check, well,
                                         dates[i].isoformat(), val, lim, msg)))

    # ------------------------------------------------------------------
    rep.checks_run.append("дебит жидкости <= 500 м3/сут")
    rep.checks_run.append("забойное добывающих >= 50 бар")
    rep.checks_run.append("забойное нагнетательных <= 300 бар")
    rep.checks_run.append("обводнённость в [0, 1]")
    rep.checks_run.append("накопленные не убывают")
    rep.checks_run.append("дебиты не отрицательны")
    rep.checks_run.append("нет NaN и бесконечностей")
    rep.checks_run.append("жидкость = нефть + вода")

    for w in wells:
        wlpr, wopr, wwpr = vec("WLPR", w), vec("WOPR", w), vec("WWPR", w)
        wwir, wbhp, wwct = vec("WWIR", w), vec("WBHP", w), vec("WWCT", w)
        wopt, wwpt, wwit = vec("WOPT", w), vec("WWPT", w), vec("WWIT", w)
        is_inj = wwir is not None and max(wwir, default=0) > 0.01

        # --- NaN и бесконечности --------------------------------------
        for name, v in (("WLPR", wlpr), ("WOPR", wopr), ("WBHP", wbhp)):
            if v is None:
                continue
            for i, x in enumerate(v):
                if math.isnan(x) or math.isinf(x):
                    err("nan_inf", w, i, 0.0, 0.0,
                        f"{name} = {x} — численный сбой решателя")
                    break

        # --- потолок по жидкости --------------------------------------
        if wlpr:
            for i, x in enumerate(wlpr):
                if x > max_liq + TOL:
                    err("max_liquid", w, i, x, max_liq,
                        f"дебит жидкости {x:.1f} > {max_liq:.0f} — "
                        f"методика ЧДД такое не считает")
                if x < -TOL:
                    err("negative_rate", w, i, x, 0.0,
                        f"отрицательный дебит жидкости {x:.3f}")

        if wopr:
            for i, x in enumerate(wopr):
                if x < -TOL:
                    err("negative_rate", w, i, x, 0.0,
                        f"отрицательный дебит нефти {x:.3f}")

        # --- забойное давление ----------------------------------------
        if wbhp:
            for i, x in enumerate(wbhp):
                active = (wwir[i] > 0.01 if is_inj and wwir
                          else (wlpr[i] > 0.01 if wlpr else False))
                if not active:
                    continue          # закрытая скважина: BHP не показатель
                if is_inj and x > max_bhp + TOL:
                    err("max_bhp_inj", w, i, x, max_bhp,
                        f"забойное нагнетательной {x:.1f} > {max_bhp:.0f} бар "
                        f"— риск разрыва пласта")
                if not is_inj and 0 < x < min_bhp - TOL:
                    err("min_bhp_prod", w, i, x, min_bhp,
                        f"забойное добывающей {x:.1f} < {min_bhp:.0f} бар "
                        f"— ниже техминимума")
                if x > MAX_PRESSURE or (0 < x < MIN_PRESSURE):
                    warn("pressure_range", w, i, x, MAX_PRESSURE,
                         f"давление {x:.1f} вне разумного диапазона")

        # --- обводнённость --------------------------------------------
        if wwct:
            for i, x in enumerate(wwct):
                if x < -TOL or x > 1.0 + TOL:
                    err("watercut_range", w, i, x, 1.0,
                        f"обводнённость {x:.4f} вне [0, 1]")

        # --- баланс: жидкость = нефть + вода ---------------------------
        if wlpr and wopr and wwpr:
            for i in range(len(wlpr)):
                total = wopr[i] + wwpr[i]
                if wlpr[i] > 1.0 and abs(total - wlpr[i]) / wlpr[i] > 0.02:
                    warn("phase_balance", w, i, wlpr[i], total,
                         f"жидкость {wlpr[i]:.2f} != нефть+вода {total:.2f}")
                    break

        # --- накопленные не убывают -----------------------------------
        for name, v in (("WOPT", wopt), ("WWPT", wwpt), ("WWIT", wwit)):
            if not v:
                continue
            for i in range(1, len(v)):
                if v[i] < v[i - 1] - max(TOL, abs(v[i - 1]) * 1e-9):
                    err("cumulative_drop", w, i, v[i], v[i - 1],
                        f"{name} убыла: {v[i-1]:.2f} -> {v[i]:.2f} — "
                        f"технический сброс счётчика или сбой")
                    break

    # --- Д9. КОМПЕНСАЦИЯ ЗАКАЧКИ ---------------------------------------
    # Считается по накопленным на границах окна: закачано за период
    # против поднято жидкости за период. Оптимизатор, максимизирующий
    # ЧДД без этого ограничения, уводит закачку в потолок.
    rep.checks_run.append(f"компенсация закачки в [{comp_min}, {comp_max}]")
    tot_inj = tot_liq = 0.0
    for w in wells:
        wwit, wlpt = vec("WWIT", w), vec("WLPT", w)
        if wwit:
            tot_inj += wwit[-1] - wwit[0]
        if wlpt:
            tot_liq += wlpt[-1] - wlpt[0]
    if tot_liq > 1.0:
        comp = tot_inj / tot_liq
        rep.compensation = round(comp, 4)
        if comp < comp_min or comp > comp_max:
            msg = (f"компенсация {comp:.3f} вне [{comp_min}, {comp_max}]: "
                   f"закачано {tot_inj/1000:,.1f} тыс.м3 против "
                   f"{tot_liq/1000:,.1f} тыс.м3 отбора")
            (err if comp_hard else warn)(
                "compensation", "FIELD", 0, comp, comp_max, msg)

    # --- ГРУППОВЫЕ ОГРАНИЧЕНИЯ ПО ФОНДУ ---------------------------------
    # Раньше механизма не существовало: проверялись только пределы по
    # каждой скважине, а квота на суммарную добычу — это не другое
    # значение существующего предела, а отсутствовавшая проверка.
    if limits is not None:
        rep.checks_run.append("групповые ограничения по фонду")
        per_step = []
        for i, d in enumerate(dates):
            liq = inj = 0.0
            for w in wells:
                v = vec("WLPR", w)
                if v:
                    liq += max(0.0, v[i])
                v = vec("WWIR", w)
                if v:
                    inj += max(0.0, v[i])
            per_step.append({"date": d, "liquid_rate": liq,
                             "injection_rate": inj})
        rep.peak_liquid_rate = round(max(p["liquid_rate"] for p in per_step), 1)
        rep.peak_injection_rate = round(max(p["injection_rate"] for p in per_step), 1)

        oil_kt = 0.0
        for w in wells:
            v = vec("WOPT", w)
            if v:
                oil_kt += (v[-1] - v[0]) * 0.832 / 1000.0
        rep.oil_period_kt = round(oil_kt, 1)

        for it in (limits.check_field_rates(per_step)
                   + limits.check_field_totals(oil_kt)):
            err("field_limit", "FIELD", 0, 0.0, 0.0, it.message)

        # --- НЕДОСТУПНОСТЬ СКВАЖИН -------------------------------------
        if limits.unavailable:
            rep.checks_run.append("скважина не работает в период недоступности")
            for u in limits.unavailable:
                if u.well not in wells:
                    continue
                wlpr, wwir = vec("WLPR", u.well), vec("WWIR", u.well)
                for i, d in enumerate(dates):
                    if not u.covers(d):
                        continue
                    active = ((wlpr[i] if wlpr else 0) > 0.01
                              or (wwir[i] if wwir else 0) > 0.01)
                    if active:
                        err("unavailable", u.well, i, 0.0, 0.0,
                            f"работает в период недоступности "
                            f"({u.reason}, {u.start}..{u.end})")
                        break

    # --- поле в целом --------------------------------------------------
    if "FPR" in keys:
        fpr = list(s.numpy_vector("FPR"))[i0:]
        for i, x in enumerate(fpr):
            if math.isnan(x) or x < MIN_PRESSURE or x > MAX_PRESSURE:
                err("field_pressure", "FIELD", i, x, MAX_PRESSURE,
                    f"пластовое давление {x:.1f} вне диапазона")
                break

    return rep


def print_report(rep: Report, max_show: int = 8) -> None:
    print(f"проверено: {rep.total_wells} скважин x {rep.total_steps} шагов")
    print(f"область:  {rep.scope}")
    print(f"период:   {rep.period}")
    print(f"проверок: {len(rep.checks_run)}")
    if rep.compensation is not None:
        print(f"компенсация закачки: {rep.compensation:.3f}")
    if rep.peak_liquid_rate is not None:
        print(f"пик по фонду: жидкость {rep.peak_liquid_rate:,.0f} м3/сут, "
              f"закачка {rep.peak_injection_rate:,.0f} м3/сут")
    if rep.oil_period_kt is not None:
        print(f"добыча нефти за период: {rep.oil_period_kt:,.1f} тыс. т")

    if rep.errors:
        print(f"\n  ОШИБОК: {len(rep.errors)}")
        by_check: dict[str, int] = {}
        for e in rep.errors:
            by_check[e["check"]] = by_check.get(e["check"], 0) + 1
        for c, n in sorted(by_check.items(), key=lambda x: -x[1]):
            print(f"    {c}: {n}")
        print("\n  первые нарушения:")
        for e in rep.errors[:max_show]:
            print(f"    {e['well']:>5} {e['date']}: {e['message']}")

    if rep.warnings:
        print(f"\n  предупреждений: {len(rep.warnings)}")
        for wn in rep.warnings[:4]:
            print(f"    {wn['well']:>5} {wn['date']}: {wn['message']}")

    print()
    if rep.ok:
        print("  РЕЗУЛЬТАТ ФИЗИЧЕСКИ КОРРЕКТЕН")
    else:
        print("  ЕСТЬ НАРУШЕНИЯ — сдавать нельзя")


def main() -> None:
    p = argparse.ArgumentParser(description="проверка физики результата")
    p.add_argument("smspec")
    p.add_argument("--json", help="сохранить отчёт")
    p.add_argument("--max-show", type=int, default=8)
    p.add_argument("--since", help="дата передачи управления, напр. 2014-01-01: "
                                   "печатается отдельная сводка по окну")
    p.add_argument("--comp-min", type=float, default=COMP_MIN)
    p.add_argument("--comp-max", type=float, default=COMP_MAX)
    p.add_argument("--constraints",
                   help="путь к configs/constraints.yaml")
    p.add_argument("--events",
                   help="файл недоступности скважин: скважина, начало, конец")
    p.add_argument("--comp-hard", action="store_true",
                   help="считать нарушение компенсации ошибкой, а не "
                        "предупреждением")
    a = p.parse_args()

    limits = load_limits(a.constraints)
    if limits and a.events:
        n = limits.load_events(a.events)
        print(f"  загружено ограничений по времени: {n}")
    if limits:
        active = [x for x in (limits.max_total_liquid_rate,
                              limits.max_total_injection_rate,
                              limits.max_total_oil_period,
                              limits.max_actions_per_step) if x is not None]
        print(f"  ограничения: {limits.source}, "
              f"групповых задано {len(active)}, "
              f"недоступностей {len(limits.unavailable)}")

    reports = [validate(a.smspec, comp_min=a.comp_min, comp_max=a.comp_max,
                        comp_hard=a.comp_hard, limits=limits)]
    if a.since:
        from datetime import date as _date
        reports.append(validate(a.smspec, since=_date.fromisoformat(a.since),
                                comp_min=a.comp_min, comp_max=a.comp_max,
                                comp_hard=a.comp_hard, limits=limits))

    for i, rep in enumerate(reports):
        if i:
            print("\n" + "=" * 60)
        print_report(rep, a.max_show)

    if a.json:
        payload = ([asdict(r) for r in reports] if len(reports) > 1
                   else asdict(reports[0]))
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nотчёт: {a.json}")

    sys.exit(0 if all(r.ok for r in reports) else 1)


if __name__ == "__main__":
    main()
