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

Сравнение с таблицей организаторов:
    python3 npv_official.py chdd_input.csv --charge-initial-pump \
            --annual-csv our_annual.csv --compare ref_annual.csv

--annual-csv пишет годовую разбивку в той же раскладке, что у
организаторов: показатели строками, годы столбцами. --compare читает
такой же файл и печатает разницу колонка к колонке.
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
# Годовая таблица в раскладке организаторов: показатель — строка,
# год — столбец. Ключ слева, подпись справа.
# Названия и единицы взяты из эталонной таблицы организаторов
# (Etalon_OPM_Flow_Model_Y_final), чтобы строки сопоставлялись один в один.
ANNUAL_ROWS = [
    ("oilKt",              "Добыча нефти",                     "тыс. т"),
    ("liquidKt",           "Добыча жидкости",                  "тыс. т"),
    ("injectionKm3",       "Закачка",                          "тыс. м3"),
    ("activeWellMonths",   "Средний действующий фонд",         "скв.-мес"),
    ("averageActiveWells", "Скважин в среднем за месяц",       "скв."),
    ("months",             "Месяцев в году",                   "мес"),
    ("revenueM",           "Выручка от основной деятельности", "млн руб."),
    ("opexM",              "Полный OPEX",                      "млн руб."),
    ("capexM",             "CAPEX новых ЭЦН",                  "млн руб."),
    ("fcfM",               "FCF",                              "млн руб."),
    ("discountFactor",     "Коэффициент дисконтирования",      "доля"),
    ("chddM",              "ЧДД",                              "млн руб."),
    ("cumChddM",           "Накопленный ЧДД",                  "млн руб."),
    ("startStopCount",     "Количество остановок или запусков", "шт."),
    ("pumpChanges",        "Количество установленных новых ЭЦН", "шт."),
    ("initialPumps",       "из них первичных установок",       "шт."),
    ("conversionCount",    "Количество переводов под закачку", "шт."),
]

# Синонимы для чтения чужой таблицы: сопоставление идёт по подстроке
# в нижнем регистре, потому что точных формулировок мы не знаем.
# (подстрока в названии, требуемая единица или None, ключ).
# Порядок важен: более специфичные строки идут первыми, иначе
# «накопленный ЧДД» матчится на «ЧДД», а «остановки и запуски» в
# миллионах — на счётчик остановок в штуках.
_ALIASES = [
    ("накопленный чдд",                 None,  "cumChddM"),
    ("добыча нефти",                    None,  "oilKt"),
    ("добыча жидкости",                 None,  "liquidKt"),
    ("закачка",                         "м3",  "injectionKm3"),
    # ВАЖНО: их «Средний действующий фонд» = 588 при 49 скважинах, то
    # есть это СКВАЖИНО-МЕСЯЦЫ (49 x 12), а не среднее число скважин.
    # Проверяется деньгами: содержание фонда 49 млн за 588 скв.-мес.
    ("средний действующий фонд",        None,  "activeWellMonths"),
    ("скважино-мес",                    None,  "activeWellMonths"),
    ("месяцев в году",                  None,  "months"),
    ("выручка",                         None,  "revenueM"),
    ("полный opex",                     None,  "opexM"),
    ("capex новых",                     None,  "capexM"),
    ("fcf",                             None,  "fcfM"),
    ("коэффициент дисконтирован",       None,  "discountFactor"),
    ("количество остановок",            "шт",  "startStopCount"),
    ("количество установленных новых",  "шт",  "pumpChanges"),
    ("количество переводов",            "шт",  "conversionCount"),
    ("чдд",                             None,  "chddM"),
]


def _norm(s: str) -> str:
    return " ".join(str(s).lower().replace("\xa0", " ").split())


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ".").replace(" ", "")
                     .replace("\xa0", "") or 0)
    except (TypeError, ValueError):
        return 0.0


def _annual_value(res: "NPVResult", key: str, i: int) -> float | None:
    """Значение показателя за i-й год. cumChddM считается на лету."""
    if key == "cumChddM":
        return sum(float(y.get("chddM", 0) or 0) for y in res.years[:i + 1])
    v = res.years[i].get(key)
    return None if v is None else float(v)


def write_annual_csv(res: "NPVResult", path: str) -> None:
    """
    Годовая таблица в раскладке организаторов: показатель, единица
    измерения, затем годы столбцами. Разделитель «;», десятичная запятая
    — Excel открывает без настройки импорта.
    """
    years = [int(y["year"]) for y in res.years if y.get("year")]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Показатель", "Ед. изм."] + years)
        for key, label, unit in ANNUAL_ROWS:
            if key != "cumChddM" and not any(key in y for y in res.years):
                continue
            row = [label, unit]
            for i in range(len(res.years)):
                v = _annual_value(res, key, i)
                row.append("" if v is None
                           else f"{v:.6f}".rstrip("0").rstrip(".").replace(".", ","))
            w.writerow(row)


def read_annual_csv(path: str) -> dict[str, dict[int, float]]:
    """
    Чужая годовая таблица -> {ключ: {год: значение}}.

    Ожидается раскладка организаторов: первая колонка — название,
    вторая может быть единицей измерения, дальше годы. Колонка единиц
    определяется автоматически по заголовку.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096); f.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        rows = list(csv.reader(f, delimiter=delim))
    if not rows:
        return {}

    # какие колонки заголовка — годы
    year_of: dict[int, int] = {}
    for i, cell in enumerate(rows[0]):
        try:
            v = int(float(str(cell).strip()))
        except ValueError:
            continue
        if 1900 < v < 2200:
            year_of[i] = v
    if not year_of:
        print("  в заголовке эталона не найдено годов"); return {}
    unit_col = 1 if "изм" in _norm(rows[0][1] if len(rows[0]) > 1 else "") else None

    out: dict[str, dict[int, float]] = {}
    unmatched = []
    for r in rows[1:]:
        if not r or not str(r[0]).strip():
            continue
        name = _norm(r[0])
        unit = _norm(r[unit_col]) if unit_col is not None and len(r) > unit_col else ""
        key = None
        for sub, need_unit, k in _ALIASES:
            if sub in name and (need_unit is None or need_unit in unit):
                key = k
                break
        if key is None or key in out:
            if key is None:
                unmatched.append(str(r[0]).strip()[:38])
            continue
        out[key] = {}
        for i, yr in year_of.items():
            if i < len(r) and str(r[i]).strip():
                out[key][yr] = _num(r[i])
    if unmatched:
        print(f"  не сопоставлено строк эталона: {len(unmatched)} "
              f"({', '.join(unmatched[:5])})")
    return out


def compare_annual(res: "NPVResult", ref: dict[str, dict[int, float]],
                   tol_pct: float = 0.5, only_bad: bool = False) -> int:
    """
    Сравнение колонка к колонке: строка эталона, наша строка, расхождение.
    Возвращает число ячеек, где расхождение больше допуска.
    """
    idx = {int(y["year"]): i for i, y in enumerate(res.years) if y.get("year")}
    ref_years: set[int] = set()
    for v in ref.values():
        ref_years |= set(v)
    years = sorted(set(idx) & ref_years)
    if not years:
        print("  нет общих лет для сравнения"); return 1

    W = 32
    hdr = "показатель".ljust(W) + "ед.".ljust(10) + "".join(f"{y:>11}" for y in years)
    print("\n" + "=" * len(hdr)); print(hdr); print("-" * len(hdr))

    bad = 0
    for key, label, unit in ANNUAL_ROWS:
        if key not in ref:
            continue
        o = [(_annual_value(res, key, idx[y]) or 0.0) for y in years]
        r = [ref[key].get(y, 0.0) for y in years]
        dl = [((a - b) / b * 100 if b else (0.0 if not a else 100.0))
              for a, b in zip(o, r)]
        n_bad = sum(abs(d) > tol_pct for d in dl)
        bad += n_bad
        if only_bad and not n_bad:
            continue
        mark = " !" if n_bad else ""
        print(f"{('  эталон · ' + label)[:W]:{W}}{unit[:9]:10}"
              + "".join(f"{v:>11,.2f}" for v in r))
        print(f"{'  наш':{W}}{'':10}" + "".join(f"{v:>11,.2f}" for v in o))
        print(f"{'  расхождение, %' + mark:{W}}{'':10}"
              + "".join(f"{v:>+11.2f}" for v in dl))
        print()
    print("=" * len(hdr))
    print(f"  ячеек с расхождением больше {tol_pct} %: {bad}")
    return bad


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
    p.add_argument("--annual-csv",
                   help="годовая таблица в раскладке организаторов")
    p.add_argument("--compare",
                   help="таблица организаторов для сравнения колонка к колонке")
    p.add_argument("--compare-tol", type=float, default=0.5,
                   help="допуск расхождения в процентах, по умолчанию 0.5")
    p.add_argument("--only-bad", action="store_true",
                   help="печатать только строки с расхождением выше допуска")
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

    if a.annual_csv:
        write_annual_csv(res, a.annual_csv)
        print(f"\nгодовая таблица: {a.annual_csv}")

    if a.compare:
        ref = read_annual_csv(a.compare)
        if ref:
            compare_annual(res, ref, tol_pct=a.compare_tol,
                           only_bad=a.only_bad)

    if a.json:
        Path(a.json).write_text(
            json.dumps({"npv_mln_rub": res.npv, "years": res.years,
                        "diagnostics": res.diagnostics},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nразбивка: {a.json}")


if __name__ == "__main__":
    main()
