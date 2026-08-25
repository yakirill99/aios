# -*- coding: utf-8 -*-
"""
ВОЗМУЩАЮЩИЕ ПРОГОНЫ — данные для матрицы влияния между скважинами.

Идея: из состояния на дату отсечки меняем режим ОДНОЙ скважины ступенькой
и смотрим, кто из остальных отозвался, насколько и через сколько месяцев.
Это управляемый эксперимент — он даёт причинность, а не корреляцию.

Почему это важнее истории: 80 месяцев наблюдений против 49x49 = 2401 пары.
История недоопределена на порядок и содержит одну траекторию управления;
что в ней не шевелили, из неё не идентифицируется в принципе.

    python3 probe_wells.py make                 сгенерировать деки возмущений
    python3 probe_wells.py analyze              собрать матрицу откликов

Побочная польза: генерация блоков WCONPROD/WCONINJE здесь — это прототип
будущего ScheduleBuilder. Если возмущения читаются симулятором, значит и
итоговый wells_schedule.inc будет читаться.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ==========================================================================
# 1. РАЗБОР РАСПИСАНИЯ: какие режимы у скважин на дату отсечки
# ==========================================================================
@dataclass
class WellMode:
    name: str
    kind: str            # "prod" | "inj"
    control: str         # LRAT / ORAT / BHP / RATE
    value: float         # целевое значение
    bhp: float = 0.0     # ограничение по забойному
    raw: str = ""        # исходная строка — для отладки

    @property
    def is_open(self) -> bool:
        return self.value > 0


def parse_schedule(sch_path: Path, cut: date) -> dict[str, WellMode]:
    """
    Идём по расписанию до даты отсечки и запоминаем ПОСЛЕДНИЙ режим
    каждой скважины. Режимы в деке накапливаются: заданное однажды
    действует, пока не переопределено.
    """
    text = sch_path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")

    modes: dict[str, WellMode] = {}
    cur = None                     # текущее ключевое слово
    now = None                     # текущая дата в расписании

    date_re = re.compile(r"^\s*(\d+)\s+'?([A-Z]{3})'?\s+(\d{4})\s*/")
    quote_re = re.compile(r"'([^']+)'|(\S+)")

    for line in lines:
        s = line.strip()
        if not s or s.startswith("--"):
            continue

        m = date_re.match(s)
        if m and cur == "DATES":
            now = date(int(m.group(3)), MONTHS.index(m.group(2)) + 1,
                       int(m.group(1)))
            if now >= cut:
                break
            continue

        if s.split()[0] in ("DATES", "WCONPROD", "WCONINJE", "WCONHIST",
                            "WCONINJH", "WELSPECS", "COMPDAT", "WELOPEN",
                            "RPTRST", "RPTSCHED", "TSTEP"):
            cur = s.split()[0]
            continue

        if s == "/":
            cur = None
            continue

        if cur not in ("WCONPROD", "WCONINJE", "WCONHIST", "WCONINJH"):
            continue

        toks = [a or b for a, b in quote_re.findall(s.rstrip("/").strip())]
        if len(toks) < 3:
            continue
        name = toks[0]

        try:
            if cur in ("WCONPROD", "WCONHIST"):
                # name status control orat wrat grat lrat resv bhp
                status, control = toks[1], toks[2]
                val, bhp = 0.0, 0.0
                if status.upper() == "OPEN":
                    idx = {"ORAT": 3, "WRAT": 4, "GRAT": 5,
                           "LRAT": 6, "RESV": 7, "BHP": 8}.get(control.upper())
                    if idx is not None and len(toks) > idx:
                        val = _num(toks[idx])
                    if len(toks) > 8:
                        bhp = _num(toks[8])
                modes[name] = WellMode(name, "prod", control.upper(),
                                       val, bhp, s)
            else:
                # name phase status control rate bhp
                status, control = toks[2], toks[3] if len(toks) > 3 else "RATE"
                val = _num(toks[4]) if len(toks) > 4 and status.upper() == "OPEN" else 0.0
                bhp = _num(toks[6]) if len(toks) > 6 else 0.0
                modes[name] = WellMode(name, "inj", control.upper(),
                                       val, bhp, s)
        except (IndexError, ValueError):
            continue

    return modes


def _num(tok: str) -> float:
    tok = tok.strip().strip("'")
    if tok in ("1*", "*", "") or tok.endswith("*"):
        return 0.0
    try:
        return float(tok)
    except ValueError:
        return 0.0


# ==========================================================================
# 2. ГЕНЕРАЦИЯ ВОЗМУЩЕНИЙ
#    Это же — прототип ScheduleBuilder.
# ==========================================================================
def build_action_block(mode: WellMode, new_value: float) -> str:
    """Один блок управляющего воздействия в формате SCHEDULE."""
    if mode.kind == "prod":
        ctl = mode.control if mode.control in ("LRAT", "ORAT", "BHP") else "LRAT"
        slots = {"ORAT": "1*", "WRAT": "1*", "GRAT": "1*", "LRAT": "1*"}
        if ctl in slots:
            slots[ctl] = f"{new_value:g}"
        bhp = f"{mode.bhp:g}" if mode.bhp else "1*"
        return (f"WCONPROD\n '{mode.name}' 'OPEN' '{ctl}' "
                f"{slots['ORAT']} {slots['WRAT']} {slots['GRAT']} "
                f"{slots['LRAT']} 1* {bhp} 1* 1* /\n/")
    bhp = f"{mode.bhp:g}" if mode.bhp else "1*"
    return (f"WCONINJE\n '{mode.name}' 'WATER' 'OPEN' 'RATE' "
            f"{new_value:g} 1* {bhp} 1* 1* /\n/")


def month_range(start: date, months: int) -> list[str]:
    out, cur = [], start
    for _ in range(months):
        y, m = (cur.year + 1, 1) if cur.month == 12 else (cur.year, cur.month + 1)
        cur = date(y, m, 1)
        out.append(f"DATES\n 01 '{MONTHS[m-1]}' {y} /\n/")
    return out


def make_probes(base_dir: Path, out_root: Path, cut: date,
                months: int, levels: list[float],
                only: list[str] | None = None,
                force: bool = False) -> list[dict]:
    sch = next(base_dir.glob("INCLUDE/*sch*.inc"), None)
    if sch is None:
        sys.exit(f"не найден sch.inc в {base_dir}/INCLUDE")

    modes = parse_schedule(sch, cut)
    if not modes:
        sys.exit("не удалось разобрать режимы скважин — проверьте дату отсечки")

    active = {n: m for n, m in modes.items() if m.is_open}
    if only:
        active = {n: m for n, m in active.items() if n in only}

    print(f"скважин с заданным режимом: {len(active)} "
          f"({sum(1 for m in active.values() if m.kind=='prod')} доб., "
          f"{sum(1 for m in active.values() if m.kind=='inj')} нагн.)")

    text = sch.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    date_re = re.compile(
        rf"^\s*{cut.day:02d}\s+'?{MONTHS[cut.month-1]}'?\s+{cut.year}\s*/")
    idx = next((i for i, l in enumerate(lines) if date_re.match(l)), None)
    if idx is None:
        sys.exit(f"дата отсечки {cut} в расписании не найдена")
    head = "\n".join(lines[:idx + 2])
    tail = "\n".join(month_range(cut, months))

    out_root.mkdir(parents=True, exist_ok=True)
    manifest = []

    # опорный вариант — без возмущения, с ним и сравниваем
    variants = [("base", None, 1.0)]
    for name, m in sorted(active.items()):
        for lv in levels:
            variants.append((f"{name}_{lv:g}".replace(".", "p"), name, lv))

    kept = 0
    for tag, well, lv in variants:
        d = out_root / tag
        # ЗАЩИТА: не затирать уже посчитанные варианты. Повторный make
        # раньше стирал результаты многочасовых прогонов.
        if d.exists():
            if not force and any(d.glob("*.UNSMRY")):
                kept += 1
                manifest.append({
                    "tag": tag, "well": well, "level": lv,
                    "kind": active[well].kind if well else None,
                    "base_value": active[well].value if well else None,
                    "new_value": active[well].value * lv if well else None,
                })
                continue
            shutil.rmtree(d)
        shutil.copytree(base_dir, d, symlinks=True)
        sch_new = next(d.glob("INCLUDE/*sch*.inc"))

        block = ""
        if well:
            m = active[well]
            block = build_action_block(m, m.value * lv) + "\n"

        body = head + "\n" + block + tail + "\n"
        # снимки не нужны — экономим место и время
        body = re.sub(r"^RPTRST\s*\n\s*'BASIC=\d'[^\n]*\n",
                      "-- RPTRST отключён для возмущений\n",
                      body, count=1, flags=re.M)
        sch_new.write_text(body, encoding="utf-8")

        manifest.append({
            "tag": tag, "well": well, "level": lv,
            "kind": active[well].kind if well else None,
            "base_value": active[well].value if well else None,
            "new_value": active[well].value * lv if well else None,
        })

    (out_root / "manifest.json").write_text(
        json.dumps({"cut": cut.isoformat(), "months": months,
                    "levels": levels, "variants": manifest},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    if kept:
        print(f"сохранено уже посчитанных: {kept} (не тронуты)")
        print(f"пересоздано: {len(manifest) - kept}")
    print(f"вариантов всего: {len(manifest)} (включая опорный)")
    print(f"каталог: {out_root}")
    if kept and not force:
        print("  чтобы пересоздать всё заново: --force")
    return manifest


# ==========================================================================
# 3. АНАЛИЗ ОТКЛИКОВ
# ==========================================================================
def analyze(runs_root: Path, out_csv: Path, min_response: float = 0.02,
            min_abs: float = 0.3):
    """
    min_response — порог ОТНОСИТЕЛЬНОГО отклика (доля от уровня скважины)
    min_abs      — порог АБСОЛЮТНОГО отклика, м3/сут. Нужен, чтобы шум на
                   почти остановленной скважине не давал ложные +300%.
    """
    try:
        from resdata.summary import Summary
    except ImportError:
        sys.exit(
            "resdata не установлен на хосте — он живёт в контейнере.\n"
            "Запускайте:  ./analyze_probes.sh")

    man = json.loads((runs_root / "manifest.json").read_text(encoding="utf-8"))
    variants = {v["tag"]: v for v in man["variants"]}
    cut = date.fromisoformat(man["cut"])

    def load(tag: str):
        d = runs_root / tag
        f = next(d.glob("*.SMSPEC"), None)
        if f is None:
            return None
        s = Summary(str(f))
        keys = set(s.keys())

        # КЛЮЧЕВОЕ: сводка содержит ВСЮ историю с 2007. Возмущение начинается
        # только с даты отсечки, поэтому всё, что раньше, надо отбросить —
        # иначе индексы (а с ними и лаги) съезжают на длину истории.
        dates = [d.date() if hasattr(d, "date") else d for d in s.dates]
        i0 = next((i for i, dd in enumerate(dates) if dd >= cut), 0)

        wells = sorted({k.split(":", 1)[1] for k in keys
                        if ":" in k and any(f"{m}:{k.split(':',1)[1]}" in keys
                                            for m in ("WOPR", "WLPR", "WWIR"))})
        out = {}
        for w in wells:
            out[w] = {
                "oil": list(s.numpy_vector(f"WOPR:{w}"))[i0:] if f"WOPR:{w}" in keys else [],
                "liq": list(s.numpy_vector(f"WLPR:{w}"))[i0:] if f"WLPR:{w}" in keys else [],
            }
        return out, dates[i0:]

    loaded = load("base")
    if loaded is None:
        sys.exit("нет результата опорного варианта (base)")
    base, base_dates = loaded
    print(f"  окно возмущения: {base_dates[0]} .. {base_dates[-1]} "
          f"({len(base_dates)} шагов)")

    rows, skipped_noise = [], 0
    for tag, meta in variants.items():
        if tag == "base":
            continue
        got = load(tag)
        if got is None:
            print(f"  пропущен {tag}: нет результата")
            continue
        cur, _ = got

        for observed, series in cur.items():
            if observed == meta["well"]:
                continue                       # сама на себя не влияет

            for what in ("oil", "liq"):
                b = base.get(observed, {}).get(what, [])
                c = series.get(what, [])
                n = min(len(b), len(c))
                if n < 6:
                    continue

                diffs = [c[i] - b[i] for i in range(n)]
                # нормируем на СРЕДНИЙ уровень скважины в окне возмущения,
                # а не на максимум по всей истории
                level = sum(b[:n]) / n
                if level < 1e-6:
                    continue

                peak_i = max(range(n), key=lambda i: abs(diffs[i]))
                peak_abs = diffs[peak_i]
                peak_rel = peak_abs / level

                if abs(peak_rel) < min_response or abs(peak_abs) < min_abs:
                    skipped_noise += 1
                    continue

                # лаг: первый шаг, где отклик достиг половины пика
                half = abs(peak_abs) / 2
                lag = next((i for i in range(n) if abs(diffs[i]) >= half), peak_i)

                rows.append({
                    "source": meta["well"],
                    "source_kind": meta["kind"],
                    "level": meta["level"],
                    "observed": observed,
                    "metric": what,
                    "peak_rel": round(peak_rel, 4),
                    "peak_abs": round(peak_abs, 4),
                    "base_level": round(level, 3),
                    "lag_month": lag,
                    "peak_month": peak_i,
                    "sign": "+" if peak_abs > 0 else "-",
                })

    if not rows:
        print(f"откликов выше порогов не найдено "
              f"(отн. {min_response:.0%}, абс. {min_abs} м3/сут)")
        return

    import csv
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"записано: {out_csv}")
    print(f"  связей: {len(rows)} (отсеяно как шум: {skipped_noise})")
    pairs = {(r["source"], r["observed"]) for r in rows}
    print(f"  уникальных пар: {len(pairs)} из возможных {len(variants)-1}x48")

    lags = sorted(r["lag_month"] for r in rows)
    print(f"  лаг отклика: медиана {lags[len(lags)//2]} мес, "
          f"диапазон {lags[0]}-{lags[-1]}")

    # --- проверка ФИЗИКИ: антисимметрия по уровням ---------------------
    # Знак сам по себе ничего не говорит. Все добывающие управляются по
    # LRAT — целевому дебиту ЖИДКОСТИ. Симулятор держит его, пока хватает
    # пластового давления, поэтому рост закачки не увеличивает отбор
    # жидкости: он меняет только обводнённость и поддержку давления.
    # Что перевесит — зависит от геометрии, знак может быть любым.
    #
    # А вот антисимметрия обязана выполняться: если x1.3 дал плюс, то
    # x0.7 для той же пары должен дать минус. Если нет — это шум.
    by_pair: dict[tuple, dict] = {}
    for r in rows:
        if r["metric"] != "oil":
            continue
        key = (r["source"], r["observed"])
        by_pair.setdefault(key, {})[r["level"]] = r["peak_abs"]

    both = {k: v for k, v in by_pair.items() if len(v) >= 2}
    if both:
        anti = sum(1 for v in both.values()
                   if list(v.values())[0] * list(v.values())[1] < 0)
        share = anti / len(both)
        mark = "ok" if share > 0.7 else ("??" if share > 0.5 else "ПЛОХО")
        print(f"\n  проверка физики (антисимметрия по уровням):")
        print(f"    {mark} пар с откликом на оба уровня: {len(both)}, "
              f"из них противоположный знак: {anti} ({share:.0%})")
        if share > 0.7:
            print("       связи физичны: усиление и ослабление дают "
                  "зеркальный отклик")
        else:
            print("       много пар с одинаковым знаком на обоих уровнях —"
                  " похоже на шум, поднимите пороги")

    # распределение по типам источника — справочно, без ожиданий по знаку
    print("\n  по типам источника:")
    for kind in ("inj", "prod"):
        sub = [r for r in rows if r["source_kind"] == kind and r["metric"] == "oil"]
        if not sub:
            continue
        pos = sum(1 for r in sub if r["sign"] == "+")
        med = sorted(r["lag_month"] for r in sub)[len(sub)//2]
        print(f"    {kind}: {len(sub)} связей, положительных {pos} "
              f"({pos/len(sub):.0%}), медиана лага {med} мес")

    strong = sorted([r for r in rows if r["metric"] == "oil"],
                    key=lambda r: -abs(r["peak_abs"]))[:10]
    print("\n  сильнейшие связи по нефти:")
    for r in strong:
        print(f"    {r['source']:>4} ({r['source_kind']}, x{r['level']}) "
              f"-> {r['observed']:>4}: {r['peak_abs']:+.2f} м3/сут "
              f"({r['peak_rel']:+.0%} от {r['base_level']:.1f}) "
              f"лаг {r['lag_month']} мес")


# ==========================================================================
def main() -> None:
    p = argparse.ArgumentParser(description="возмущающие прогоны для матрицы влияния")
    sub = p.add_subparsers(dest="cmd", required=True)

    mk = sub.add_parser("make", help="сгенерировать деки возмущений")
    mk.add_argument("--base", default="~/aios/model/Model_Y_base",
                    help="дека опорного сценария (prepare_baseline.sh)")
    mk.add_argument("--out", default="~/aios-data/probes")
    mk.add_argument("--cut", default="2014-01-01",
                    help="дата передачи управления системе")
    mk.add_argument("--months", type=int, default=23,
                    help="горизонт возмущения; для Model_Y ограничен декой (23 мес от 2014-01)")
    mk.add_argument("--levels", default="0.7,1.3",
                    help="множители режима через запятую")
    mk.add_argument("--only", help="только эти скважины, через запятую")
    mk.add_argument("--force", action="store_true",
                    help="пересоздать варианты, УДАЛИВ уже посчитанные результаты")

    an = sub.add_parser("analyze", help="собрать матрицу откликов")
    an.add_argument("--runs", default="~/aios-data/probes")
    an.add_argument("--out", default="~/aios-data/probes/response_matrix.csv")
    an.add_argument("--min-response", type=float, default=0.02,
                    help="порог относительного отклика")
    an.add_argument("--min-abs", type=float, default=0.3,
                    help="порог абсолютного отклика, м3/сут")

    a = p.parse_args()
    if a.cmd == "make":
        make_probes(
            Path(a.base).expanduser(), Path(a.out).expanduser(),
            date.fromisoformat(a.cut), a.months,
            [float(x) for x in a.levels.split(",")],
            a.only.split(",") if a.only else None, a.force)
    else:
        analyze(Path(a.runs).expanduser(), Path(a.out).expanduser(),
                a.min_response, a.min_abs)


if __name__ == "__main__":
    main()
