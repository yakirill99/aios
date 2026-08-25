# -*- coding: utf-8 -*-
"""
ЕДИНСТВЕННЫЙ ИСТОЧНИК ПРАВДЫ ПО ДЕНЬГАМ (FR-6.1).

Тонкая обёртка над эталонным скриптом организаторов (CHDD_PYTHON).
Своей арифметики здесь НЕТ и быть не должно: любая собственная формула —
это второй источник правды и прямой путь к расхождению на пересчёте.

Почему обёртка, а не вызов РАСЧЕТ_ЧДД.py:
  · Excel-пайплайн медленный, а внутри цикла оптимизации расчёт вызывается
    сотни раз;
  · РАСЧЕТ_ЧДД.py требует ВХОДНОЙ_ФАЙЛ.txt, у которого в архиве имя записано
    в macOS-нормализации (NFD) и на Linux не находится;
  · compute_calculation() — чистая функция, её можно звать напрямую.

Использование из кода:
    from npv_official import compute_npv_from_csv
    res = compute_npv_from_csv("chdd_input.csv")
    print(res.npv)          # млн руб

Использование из терминала:
    python3 npv_official.py chdd_input.csv
    python3 npv_official.py chdd_input.csv --start-year 2014 --json out.json
"""
from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Подключение эталонного модуля. Путь можно переопределить переменной
# окружения CHDD_PATH.
# --------------------------------------------------------------------------
_CANDIDATES = [
    os.environ.get("CHDD_PATH", ""),
    "vendor/chdd",
    "../vendor/chdd",
    str(Path.home() / "aios/vendor/chdd"),
    str(Path(__file__).resolve().parent.parent / "vendor/chdd"),
    "CHDD_PYTHON",
]


def _load_chdd():
    for p in _CANDIDATES:
        if p and (Path(p) / "chdd_model.py").exists():
            sys.path.insert(0, str(Path(p).resolve()))
            import chdd_model  # noqa
            return chdd_model, str(Path(p).resolve())
    raise ImportError(
        "не найден chdd_model.py. Положите CHDD_PYTHON в vendor/chdd "
        "или задайте CHDD_PATH=/путь/к/CHDD_PYTHON"
    )


_M, CHDD_DIR = _load_chdd()

REQUIRED_COLUMNS = _M.REQUIRED_COLUMNS
VERSION = _M.VERSION


# --------------------------------------------------------------------------
@dataclass
class NPVResult:
    npv: float                          # млн руб — то самое заявляемое число
    start_year: int
    end_year: int
    years: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def summary(self) -> str:
        w = 112
        out = [f"ЧДД = {self.npv:,.3f} млн руб   "
               f"({self.start_year}\u2013{self.end_year}, эталон {VERSION})",
               "-" * w,
               f"{'год':>5} {'Qн,тыс.т':>10} {'Qж,тыс.т':>10} {'Qзак,тыс.м3':>12} "
               f"{'выручка':>10} {'OPEX':>9} {'CAPEX':>7} {'скв':>5} "
               f"{'ЭЦН':>4} {'ст/пу':>6} {'FCF':>10} {'DF':>6} {'ЧДД':>10}"]
        for y in self.years:
            out.append(
                f"{y.get('year', ''):>5} "
                f"{y.get('oilKt', 0):>10,.1f} "
                f"{y.get('liquidKt', 0):>10,.1f} "
                f"{y.get('injectionKm3', 0):>12,.1f} "
                f"{y.get('revenueM', 0):>10,.0f} "
                f"{y.get('opexM', 0):>9,.1f} "
                f"{y.get('capexM', 0):>7,.2f} "
                f"{y.get('averageActiveWells', 0):>5,.0f} "
                f"{y.get('pumpChanges', 0):>4,.0f} "
                f"{y.get('startStopCount', 0):>6,.0f} "
                f"{y.get('fcfM', 0):>10,.0f} "
                f"{y.get('discountFactor', 0):>6.3f} "
                f"{y.get('chddM', 0):>10,.1f}")
        out.append("-" * w)

        tot_oil = sum(y.get('oilKt', 0) for y in self.years)
        tot_liq = sum(y.get('liquidKt', 0) for y in self.years)
        if tot_liq:
            out.append(f"итого: нефть {tot_oil:,.1f} тыс.т, "
                       f"жидкость {tot_liq:,.1f} тыс.т, "
                       f"обводнённость {(1 - tot_oil/tot_liq)*100:.1f}%")
        init = sum(y.get('initialPumps', 0) for y in self.years)
        ev = (sum(y.get('pumpChanges', 0) for y in self.years),
              sum(y.get('startStopCount', 0) for y in self.years),
              sum(y.get('conversionCount', 0) for y in self.years))
        out.append(f"событий: смен ЭЦН {ev[0]:.0f}"
                   + (f" (из них первичных {init:.0f})" if init else "")
                   + f", остановок/пусков {ev[1]:.0f}, "
                   f"переводов под закачку {ev[2]:.0f}")

        d = self.diagnostics
        for key, label in (("negativeDiffRows", "строк с отрицательными разностями"),
                           ("duplicateWellMonths", "дублей скважина-месяц"),
                           ("invalidDates", "нераспознанных дат"),
                           ("outOfRangeRates", "режимов вне диапазона")):
            if d.get(key):
                out.append(f"! {label}: {d[key]}")
        for msg in self.warnings[:8]:
            out.append(f"! {msg}")
        return "\n".join(out)


# --------------------------------------------------------------------------
def compute_npv(records: list[dict[str, Any]],
                start_year: int | None = None,
                assumptions: dict[str, Any] | None = None,
                name: str = "run") -> NPVResult:
    """
    records — список словарей с ключами REQUIRED_COLUMNS.
    start_year — год начала экономического расчёта (для трека 1 обычно тот,
                 с которого система принимает управление). None = из данных.
    """
    if not records:
        raise ValueError("пустой набор записей")

    start_date = f"{start_year}-01-01" if start_year else None
    calc = _M.compute_calculation(
        records,
        headers=list(records[0].keys()),
        assumptions=assumptions,
        start_date=start_date,
        name=name,
    )

    years = calc.get("annual") or calc.get("years") or []
    totals = calc.get("totals", {})
    npv = totals.get("chddM")
    if npv is None:
        npv = sum(y.get("chddM", 0.0) for y in years)

    yrs = [int(y["year"]) for y in years if y.get("year")]
    return NPVResult(
        npv=float(npv),
        start_year=min(yrs) if yrs else 0,
        end_year=max(yrs) if yrs else 0,
        years=years,
        warnings=list(calc.get("warnings", [])),
        diagnostics=dict(calc.get("diagnostics", {})),
        raw=calc,
    )


def load_csv(path: str) -> list[dict[str, Any]]:
    """CSV с техническими (DATA, well, WLPT...) или русскими заголовками."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path}: нет строк")

    # приводим заголовки к каноническим через таблицу псевдонимов эталона
    try:
        sys.path.insert(0, CHDD_DIR)
        from excel_io import HEADER_ALIASES, _normalized_header  # type: ignore
        mapped = []
        for r in rows:
            mapped.append({
                HEADER_ALIASES.get(_normalized_header(k), k): v
                for k, v in r.items()
            })
        rows = mapped
    except Exception:
        pass                      # заголовки уже канонические

    # эталон не понимает даты с временем ("2014-01-01 00:00:00" -> None),
    # поэтому срезаем время: он ждёт либо date-объект, либо чистый ISO
    for r in rows:
        d = r.get("DATA")
        if isinstance(d, str) and " " in d:
            r["DATA"] = d.split(" ", 1)[0]
        elif isinstance(d, str) and "T" in d:
            r["DATA"] = d.split("T", 1)[0]
    return rows


def compute_npv_from_csv(path: str, start_year: int | None = None,
                         **kw) -> NPVResult:
    return compute_npv(load_csv(path), start_year=start_year,
                       name=Path(path).stem, **kw)


# --------------------------------------------------------------------------
def check_before_compute(records: list[dict[str, Any]]) -> list[str]:
    """
    Быстрая проверка ДО расчёта. Эталон при нарушениях бросает исключение,
    а внутри цикла оптимизации это дороже, чем отбраковать кандидата.
    """
    problems = []
    missing = [c for c in REQUIRED_COLUMNS if c not in records[0]]
    if missing:
        problems.append(f"нет колонок: {', '.join(missing)}")

    over = [r for r in records if _num(r.get("WLPR")) > 500]
    if over:
        ex = ", ".join(f"{r['well']}@{r['DATA']}={_num(r['WLPR']):.0f}"
                       for r in over[:3])
        problems.append(f"WLPR > 500 в {len(over)} точках ({ex}) — "
                        f"методика такое не считает")

    neg = [r for r in records
           if _num(r.get("WLPT_Diff")) < 0 or _num(r.get("WOMT_Diff")) < 0
           or _num(r.get("WWIT_Diff")) < 0]
    if neg:
        problems.append(f"{len(neg)} строк с отрицательными разностями — "
                        f"будут исключены из расчёта целиком")
    return problems


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ".").replace(" ", "") or 0)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Расчёт ЧДД по эталону организаторов")
    p.add_argument("csv", help="входной CSV (см. summary_to_csv.py)")
    p.add_argument("--start-year", type=int, default=None)
    p.add_argument("--charge-initial-pump", action="store_true",
                   help="начислять первичную установку ЭЦН как операцию "
                        "(эталон организаторов делает именно так: 23 операции "
                        "в 2007 и 28 в 2008 — это ввод скважин)")
    p.add_argument("--json", help="сохранить полную разбивку")
    a = p.parse_args()

    print(f"эталон: {CHDD_DIR}  ({VERSION})")
    recs = load_csv(a.csv)
    print(f"строк: {len(recs)}, скважин: {len({r['well'] for r in recs})}")

    for msg in check_before_compute(recs):
        print(f"! {msg}")

    assumptions = ({"chargeInitialPump": True}
                   if a.charge_initial_pump else None)
    res = compute_npv_from_csv(a.csv, start_year=a.start_year,
                               assumptions=assumptions)
    print()
    print(res.summary())

    if a.json:
        Path(a.json).write_text(
            json.dumps({"npv_mln_rub": res.npv, "years": res.years,
                        "diagnostics": res.diagnostics},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nразбивка: {a.json}")


if __name__ == "__main__":
    main()
