# -*- coding: utf-8 -*-
"""
ГЕНЕРАТОР КАНДИДАТОВ — дискретная сетка управляющих воздействий.

Почему сетка, а не непрерывная оптимизация: ЭЦН делает целевую функцию
разрывной и зависящей от истории. Смена насоса вверх происходит сразу при
выходе за верхнюю границу, вниз — только при падении более чем на 100
м3/сут ниже нижней. Возникает гистерезис: результат зависит от траектории,
а не только от текущего режима. Непрерывные методы на таком ландшафте
строят неверную модель и утыкаются в границы.

Плюс сетка полностью перечислима в трейсе — на защите видно, что именно
рассматривалось и почему выбрано.

    python3 candidates.py plan                 показать план перебора
    python3 candidates.py make                 сгенерировать деки кандидатов
    python3 candidates.py rank                 отранжировать посчитанные

Деструктивных операций нет: уже посчитанные кандидаты не пересоздаются
без --force.
"""
from __future__ import annotations

import argparse
import itertools
import json
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_wells import WellMode, parse_schedule                    # noqa: E402
from schedule_builder import (Action, ScheduleBuilder, build_deck,   # noqa: E402
                              MAX_LIQUID_RATE)

DEFAULT_LEVELS = [0.6, 0.8, 1.0, 1.25, 1.5]


# ==========================================================================
@dataclass
class Candidate:
    tag: str
    actions: list[dict]
    intent: str = ""
    npv: float | None = None
    delta: float | None = None


# ==========================================================================
def pick_wells(modes: dict[str, WellMode], n: int, kind: str,
               matrix_csv: Path | None = None) -> list[WellMode]:
    """
    Кого трогаем. Приоритет по двум признакам:
      1) запас до предела 500 м3/сут — где есть куда форсировать;
      2) число связей в матрице влияния, если она посчитана — скважина,
         которая тянет за собой соседей, ценнее изолированной.
    """
    pool = [m for m in modes.values() if m.kind == kind and m.is_open]
    if not pool:
        return []

    degree: dict[str, int] = {}
    if matrix_csv and matrix_csv.exists():
        import csv
        for r in csv.DictReader(matrix_csv.open(encoding="utf-8-sig")):
            if r.get("metric") == "oil":
                degree[r["source"]] = degree.get(r["source"], 0) + 1

    def score(m: WellMode) -> float:
        headroom = (MAX_LIQUID_RATE - m.value) / MAX_LIQUID_RATE if kind == "prod" else 0.5
        return headroom + 0.1 * degree.get(m.name, 0)

    return sorted(pool, key=score, reverse=True)[:n]


def make_candidates(modes: dict[str, WellMode], cut: date,
                    n_prod: int, n_inj: int, levels: list[float],
                    mode: str, matrix: Path | None,
                    limit: int, only: list[str] | None = None) -> list[Candidate]:
    """
    Три режима перебора:
      single — по одному воздействию за раз. Дёшево, показывает вклад
               каждой скважины по отдельности. Кандидатов: N x L.
      pairs  — пары скважин. Ловит взаимодействия. Кандидатов: C(N,2) x L^2.
      full   — полная сетка по выбранным скважинам. Взрывается быстро,
               поэтому обрезается по limit.
    """
    if only:
        targets = [modes[w] for w in only if w in modes]
        missing = [w for w in only if w not in modes]
        if missing:
            print(f"  нет в модели: {', '.join(missing)}")
    else:
        targets = (pick_wells(modes, n_prod, "prod", matrix)
                   + pick_wells(modes, n_inj, "inj", matrix))
    if not targets:
        sys.exit("нет скважин для перебора")

    d = cut.isoformat()
    cands: list[Candidate] = []

    def act(m: WellMode, lv: float) -> dict | None:
        if abs(lv - 1.0) < 1e-9:
            return None                          # ничего не меняем
        if m.kind == "prod":
            v = min(m.value * lv, MAX_LIQUID_RATE)
            ctl = "LRAT"
        else:
            v = m.value * lv
            ctl = "RATE"
        if abs(v - m.value) < 0.5:
            return None
        return asdict(Action(date=d, well=m.name, control=ctl,
                             value=round(v, 1),
                             reason=f"{m.value:g} -> {v:g} (x{lv})"))

    cands.append(Candidate(tag="noop", actions=[],
                           intent="ничего не менять — опорный вариант шага"))

    if mode == "single":
        for m in targets:
            for lv in levels:
                a = act(m, lv)
                if a:
                    cands.append(Candidate(
                        tag=f"{m.name}_x{lv:g}".replace(".", "p"),
                        actions=[a],
                        intent=f"{m.name} ({m.kind}) x{lv}"))

    elif mode == "pairs":
        for m1, m2 in itertools.combinations(targets, 2):
            for lv1, lv2 in itertools.product(levels, repeat=2):
                a1, a2 = act(m1, lv1), act(m2, lv2)
                acts = [x for x in (a1, a2) if x]
                if len(acts) < 2:
                    continue
                cands.append(Candidate(
                    tag=f"{m1.name}x{lv1:g}_{m2.name}x{lv2:g}".replace(".", "p"),
                    actions=acts,
                    intent=f"{m1.name} x{lv1}, {m2.name} x{lv2}"))

    else:  # full
        for combo in itertools.product(levels, repeat=len(targets)):
            acts = [a for m, lv in zip(targets, combo) if (a := act(m, lv))]
            if not acts:
                continue
            tag = "_".join(f"{m.name}x{lv:g}" for m, lv in zip(targets, combo)
                           if abs(lv - 1) > 1e-9).replace(".", "p")
            cands.append(Candidate(tag=tag[:80], actions=acts,
                                   intent=f"{len(acts)} воздействий"))

    if limit and len(cands) > limit:
        print(f"  кандидатов {len(cands)}, обрезано до {limit}")
        cands = cands[:limit]
    return cands


# ==========================================================================
def build_all(base: Path, out_root: Path, cands: list[Candidate],
              modes: dict[str, WellMode], cut: date, end: date,
              force: bool) -> int:
    out_root.mkdir(parents=True, exist_ok=True)
    built = kept = 0

    for c in cands:
        d = out_root / c.tag
        if d.exists() and not force and any(d.glob("*.UNSMRY")):
            kept += 1
            continue

        b = ScheduleBuilder(modes, cut, end)
        b.add_many([Action(**a) for a in c.actions])
        issues = [i for i in b.validate() if i.severity == "error"]
        if issues:
            print(f"  пропущен {c.tag}: {issues[0].message}")
            continue

        inc = out_root / f"{c.tag}.inc"
        inc.write_text(b.render(header=False), encoding="utf-8")
        build_deck(base, d, cut, inc, force=True)
        inc.unlink()
        built += 1

    (out_root / "manifest.json").write_text(
        json.dumps({"cut": cut.isoformat(), "end": end.isoformat(),
                    "candidates": [asdict(c) for c in cands]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if kept:
        print(f"  сохранено посчитанных: {kept} (не тронуты)")
    print(f"  собрано дек: {built}")
    return built


# ==========================================================================
def rank(runs_root: Path, baseline_json: Path, top: int) -> None:
    """Ранжирование посчитанных кандидатов по ЧДД."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from npv_official import compute_npv_from_csv

    man = json.loads((runs_root / "manifest.json").read_text(encoding="utf-8"))
    cands = {c["tag"]: c for c in man["candidates"]}
    start_year = int(man["cut"][:4])

    base = None
    if baseline_json.exists():
        base = json.loads(baseline_json.read_text(encoding="utf-8"))["npv_mln_rub"]

    results = []
    for tag in cands:
        csv_path = runs_root / tag / "chdd_input.csv"
        if not csv_path.exists():
            continue
        try:
            res = compute_npv_from_csv(str(csv_path), start_year=start_year)
        except Exception as e:
            print(f"  {tag}: ошибка расчёта — {e}")
            continue
        results.append((tag, res.npv, cands[tag]["intent"]))

    if not results:
        sys.exit("нет посчитанных кандидатов — сначала прогон и summary_to_csv")

    results.sort(key=lambda r: -r[1])
    print(f"\nпосчитано кандидатов: {len(results)}")
    if base:
        print(f"планка (baseline): {base:,.1f} млн руб\n")
        print(f"{'#':>3} {'кандидат':<24} {'ЧДД':>12} {'дельта':>12} {'%':>7}  намерение")
        for i, (tag, npv, intent) in enumerate(results[:top], 1):
            d = npv - base
            print(f"{i:>3} {tag:<24} {npv:>12,.1f} {d:>+12,.1f} "
                  f"{d/base*100:>+6.2f}%  {intent}")
        best = results[0]
        print(f"\nлучший: {best[0]} -> {best[1]:,.1f} млн "
              f"({best[1]-base:+,.1f} к планке)")
    else:
        for i, (tag, npv, intent) in enumerate(results[:top], 1):
            print(f"{i:>3} {tag:<24} {npv:>12,.1f}  {intent}")

    out = runs_root / "ranking.json"
    out.write_text(json.dumps(
        [{"tag": t, "npv": n, "delta": (n - base) if base else None,
          "intent": i} for t, n, i in results],
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nранжирование: {out}")


# ==========================================================================
def main() -> None:
    p = argparse.ArgumentParser(description="генератор кандидатов")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("plan", "make"):
        sp = sub.add_parser(name)
        sp.add_argument("--base", default="~/aios/model/Model_Y_opm")
        sp.add_argument("--out", default="~/aios-data/candidates")
        sp.add_argument("--cut", default="2014-01-01")
        sp.add_argument("--end", default="2022-12-01")
        sp.add_argument("--prod", type=int, default=5, help="добывающих в переборе")
        sp.add_argument("--inj", type=int, default=3, help="нагнетательных")
        sp.add_argument("--levels", default="0.6,0.8,1.0,1.25,1.5")
        sp.add_argument("--mode", choices=["single", "pairs", "full"],
                        default="single")
        sp.add_argument("--limit", type=int, default=400)
        sp.add_argument("--matrix", default="~/aios-data/probes/response_matrix.csv")
        sp.add_argument("--only-wells", default="",
                        help="перебирать только эти скважины, через запятую")
        if name == "make":
            sp.add_argument("--force", action="store_true")

    rp = sub.add_parser("rank")
    rp.add_argument("--runs", default="~/aios-data/candidates")
    rp.add_argument("--baseline", default="~/aios-data/runs/baseline/baseline_npv.json")
    rp.add_argument("--top", type=int, default=20)

    a = p.parse_args()

    if a.cmd == "rank":
        rank(Path(a.runs).expanduser(),
             Path(a.baseline).expanduser(), a.top)
        return

    base = Path(a.base).expanduser()
    cut, end = date.fromisoformat(a.cut), date.fromisoformat(a.end)
    sch = next(base.glob("INCLUDE/*sch*.inc"), None)
    if sch is None:
        sys.exit(f"не найден sch.inc в {base}/INCLUDE")

    modes = parse_schedule(sch, cut)
    levels = [float(x) for x in a.levels.split(",")]
    matrix = Path(a.matrix).expanduser() if a.matrix else None

    only = [w.strip() for w in a.only_wells.split(",") if w.strip()] or None
    cands = make_candidates(modes, cut, a.prod, a.inj, levels,
                            a.mode, matrix, a.limit, only)

    print(f"режим перебора: {a.mode}")
    if only:
        print(f"скважины: {', '.join(only)}")
    else:
        print(f"скважин: {a.prod} доб. + {a.inj} нагн.")
    print(f"уровней: {len(levels)}")
    print(f"кандидатов: {len(cands)}")

    if a.cmd == "plan":
        print("\nпервые 15:")
        for c in cands[:15]:
            print(f"  {c.tag:<26} {c.intent}")
        est_min = len(cands) * 170 / 6 / 60
        print(f"\nоценка времени: ~{est_min:.0f} мин на 6 процессах "
              f"({est_min/60:.1f} ч)")
        print(f"место: ~{len(cands) * 25 / 1024:.1f} ГБ")
        return

    out_root = Path(a.out).expanduser()
    print()
    build_all(base, out_root, cands, modes, cut, end, a.force)
    print(f"\nкаталог: {out_root}")
    print("дальше: ./run_candidates.sh")


if __name__ == "__main__":
    main()
