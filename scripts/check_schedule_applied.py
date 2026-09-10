# -*- coding: utf-8 -*-
"""
ОБРАТНАЯ СВЕРКА РЕЖИМОВ.

Расписание задаёт целевой дебит, но скважина может его не достичь: при
цели выше продуктивности она упирается в нижний предел забойного
давления и отдаёт столько, сколько может. Команда при этом принята
симулятором без ошибки, и по логу цикла отличить одно от другого нельзя.

Так была найдена скважина, дававшая ровно нулевой прирост при любом
множителе: `1_x3` и `1_x1p25` давали один и тот же ЧДД до третьего знака.
Каждый шаг она занимала слот прогона впустую.

Скрипт читает сдаваемое расписание, находит для каждого воздействия
фактический дебит в сводке и печатает, исполнена команда или нет.

Запускается ВНУТРИ контейнера (нужен resdata):

    python3 check_schedule_applied.py /out/MODEL_Y.SMSPEC /out/wells_schedule.inc

Код возврата: 0 — все команды исполнены, 1 — есть недостижимые цели.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

# доля от цели, ниже которой считаем команду неисполненной
TOL = 0.95


def _expand(tokens: list[str]) -> list[str]:
    """Раскрывает сокращение 3* в три значения по умолчанию."""
    out: list[str] = []
    for t in tokens:
        m = re.fullmatch(r"(\d+)\*", t)
        if m:
            out.extend(["*"] * int(m.group(1)))
        elif t == "*":
            out.append("*")
        else:
            out.append(t)
    return out


def _num(tok: str) -> float | None:
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None


def parse_schedule(path: str) -> list[dict]:
    """
    Разбирает .inc: даты и заданные режимы.

    WCONPROD: скважина, статус, управление, ORAT WRAT GRAT LRAT RESV BHP
    WCONINJE: скважина, флюид, статус, управление, RATE RESV BHP
    """
    text = open(path, encoding="utf-8", errors="ignore").read()
    actions: list[dict] = []
    cur: date | None = None
    section = None

    for raw in text.splitlines():
        line = raw.split("--")[0].strip()
        if not line:
            continue
        up = line.upper()

        if up.startswith("DATES"):
            section = "DATES"; continue
        if up.startswith("WCONPROD"):
            section = "PROD"; continue
        if up.startswith("WCONINJE"):
            section = "INJ"; continue
        if line == "/":
            section = None; continue

        body = line.rstrip("/").strip()
        if not body:
            continue
        toks = _expand([t.strip("'\"") for t in body.split()])

        if section == "DATES" and len(toks) >= 3:
            try:
                cur = date(int(toks[2]), MONTHS[toks[1].upper()[:3]], int(toks[0]))
            except (ValueError, KeyError):
                pass
            continue

        if section == "PROD" and len(toks) >= 3 and cur:
            vals = toks[3:]
            target = _num(vals[3]) if len(vals) > 3 else None   # LRAT
            actions.append({"date": cur, "well": toks[0], "kind": "prod",
                            "control": toks[2].upper(), "target": target,
                            "status": toks[1].upper()})
        elif section == "INJ" and len(toks) >= 4 and cur:
            vals = toks[4:]
            target = _num(vals[0]) if vals else None            # RATE
            actions.append({"date": cur, "well": toks[0], "kind": "inj",
                            "control": toks[3].upper(), "target": target,
                            "status": toks[2].upper()})
    return actions


def check(smspec: str, inc: str, tol: float = TOL, max_show: int = 40) -> int:
    from resdata.summary import Summary

    s = Summary(smspec)
    keys = set(s.keys())
    dates = [d.date() if hasattr(d, "date") else d for d in s.dates]

    def series(name: str, well: str):
        k = f"{name}:{well}"
        return list(s.numpy_vector(k)) if k in keys else None

    def at(vec, d: date) -> float | None:
        """Значение на первой точке сводки, наступившей после даты."""
        for i, dd in enumerate(dates):
            if dd > d:
                return vec[i]
        return None

    actions = [a for a in parse_schedule(inc)
               if a["target"] and a["status"] == "OPEN"]
    if not actions:
        print("в расписании нет воздействий с заданным дебитом")
        return 0

    print(f"воздействий с целевым режимом: {len(actions)}")
    print(f"{'дата':>12} {'скв':>5} {'упр':>6} {'цель':>9} {'факт':>9} "
          f"{'доля':>7}  вердикт")
    print("-" * 74)

    bad, unreachable = 0, []
    for a in sorted(actions, key=lambda x: (x["date"], x["well"])):
        vec_name = "WLPR" if a["kind"] == "prod" else "WWIR"
        vec = series(vec_name, a["well"])
        if vec is None:
            print(f"{str(a['date']):>12} {a['well']:>5} {a['control']:>6} "
                  f"{a['target']:>9,.1f} {'нет':>9} {'—':>7}  нет вектора")
            continue
        actual = at(vec, a["date"])
        if actual is None:
            continue
        ratio = actual / a["target"] if a["target"] else 0.0
        ok = ratio >= tol
        if not ok:
            bad += 1
            unreachable.append(a["well"])
        print(f"{str(a['date']):>12} {a['well']:>5} {a['control']:>6} "
              f"{a['target']:>9,.1f} {actual:>9,.1f} {ratio:>6.0%}  "
              f"{'исполнено' if ok else 'НЕ ДОСТИГНУТА'}")

    print("-" * 74)
    if bad:
        uniq = sorted(set(unreachable), key=lambda x: int(x) if x.isdigit() else 0)
        print(f"  недостижимых целей: {bad}, скважины: {', '.join(uniq)}")
        print("  такая скважина упирается в предел по забойному давлению:")
        print("  она отдаёт максимум, и повышение цели ничего не меняет.")
        print("  Слот прогона на неё тратится впустую — стоит исключать")
        print("  из отбора целей после первого нулевого отклика.")
    else:
        print("  все команды исполнены: фактический режим совпал с заданным")
    return 1 if bad else 0


def main() -> None:
    p = argparse.ArgumentParser(description="сверка заданного режима с фактическим")
    p.add_argument("smspec")
    p.add_argument("inc")
    p.add_argument("--tol", type=float, default=TOL,
                   help=f"доля от цели, ниже которой команда не исполнена "
                        f"(по умолчанию {TOL})")
    a = p.parse_args()
    sys.exit(check(a.smspec, a.inc, a.tol))


if __name__ == "__main__":
    main()
