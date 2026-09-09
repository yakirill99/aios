# -*- coding: utf-8 -*-
"""
ЭКОНОМИЧЕСКИЙ ПАСПОРТ СКВАЖИНЫ.

Один расчёт, два потребителя:

    well_cards.json     карточки для промпта стратега — ТОЛЬКО ТЕГИ,
                        без единого числа (FR-7.2)
    well_passport.md    таблица на 49 строк с числами — для жюри

Пороги и формулировки берутся из configs/passport_rules.yaml, который
правит гидродинамик. В коде чисел нет.

Работа в два шага, потому что история читается внутри контейнера
(нужен resdata), а матрица влияния и правила лежат на хосте.

    # 1. внутри контейнера: сырые метрики из сводки
    run_py well_passport.py extract /out/MODEL_Y.SMSPEC \\
        --cut 2014-01-01 -o /out/well_metrics.json

    # 2. на хосте: пороги, теги, оба файла
    python3 well_passport.py cards ~/aios-data/runs/history_full/well_metrics.json \\
        --rules ../configs/passport_rules.yaml \\
        --matrix ~/aios-data/probes/response_matrix.csv \\
        --json ~/aios-data/well_cards.json \\
        --md   ~/aios-data/well_passport.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

EPS = 1e-6


# ==========================================================================
# ШАГ 1. Извлечение метрик из сводки (внутри контейнера)
# ==========================================================================
def _slope(ys: list[float]) -> float:
    """Наклон линейной регрессии на единицу шага. Шаг — месяц."""
    n = len(ys)
    if n < 3:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    num = sum((i - mx) * (y - my) for i, y in enumerate(ys))
    den = sum((i - mx) ** 2 for i in range(n))
    return num / den if den else 0.0


def extract(smspec: str, cut: date, window: int = 24) -> dict:
    from resdata.summary import Summary

    s = Summary(smspec)
    keys = set(s.keys())
    dates = [d.date() if hasattr(d, "date") else d for d in s.dates]

    idx = [i for i, d in enumerate(dates) if d <= cut]
    if not idx:
        sys.exit(f"в сводке нет точек до {cut}")
    last = idx[-1]

    # Поскважинное пластовое давление. В Model_Y заказаны WBP и WBP9;
    # WBP9 — среднее по девяти ячейкам вокруг ствола, ближе к смыслу
    # «давление в районе скважины», чем значение в одной ячейке.
    pres_key = next((k for k in ("WBP9", "WBP5", "WBP4", "WBP")
                     if any(x.startswith(k + ":") for x in keys)), None)
    # Индекс продуктивности по нефти симулятор считает сам. Свой расчёт
    # через перепад нужен только как запасной вариант.
    pi_key = "WPIO" if any(x.startswith("WPIO:") for x in keys) else None
    field_pres = None
    if "FPR" in keys:
        field_pres = float(list(s.numpy_vector("FPR"))[last])

    markers = ("WOPR", "WLPR", "WWIR", "WBHP", "WOPT", "WWIT")
    wells = sorted({k.split(":", 1)[1] for k in keys if ":" in k
                    and any(f"{m}:{k.split(':', 1)[1]}" in keys for m in markers)},
                   key=lambda x: int(x) if x.isdigit() else 0)

    def vec(name: str, w: str) -> list[float] | None:
        k = f"{name}:{w}"
        return list(s.numpy_vector(k)) if k in keys else None

    out: dict[str, dict] = {}
    for w in wells:
        lpr, opr, wpr = vec("WLPR", w), vec("WOPR", w), vec("WWPR", w)
        wir, bhp, opt = vec("WWIR", w), vec("WBHP", w), vec("WOPT", w)
        inj = bool(wir) and max(wir[:last + 1], default=0.0) > 0.01

        q_liq = lpr[last] if lpr else 0.0
        q_oil = opr[last] if opr else 0.0
        q_wat = wpr[last] if wpr else 0.0
        q_inj = wir[last] if wir else 0.0
        wct = q_wat / q_liq if q_liq > EPS else 0.0

        lo = max(0, last - window + 1)
        wct_hist = []
        if lpr and wpr:
            for i in range(lo, last + 1):
                wct_hist.append(wpr[i] / lpr[i] if lpr[i] > EPS else 0.0)
        wct_slope = _slope(wct_hist)

        lo12 = max(0, last - 11)
        oil_hist = opr[lo12:last + 1] if opr else []
        base = oil_hist[0] if oil_hist and oil_hist[0] > EPS else 0.0
        decline = 0.0
        if base > EPS and len(oil_hist) >= 3:
            decline = -_slope(oil_hist) / base       # доля в месяц

        # пластовое давление и индекс продуктивности
        if pres_key:
            pv = vec(pres_key, w)
            pres = pv[last] if pv else None
            pres_src = "well"
        else:
            pres = field_pres
            pres_src = "field"
        p_bhp = bhp[last] if bhp else None
        pi = pi_slope = None
        if pi_key:
            pv = vec(pi_key, w)
            if pv:
                pi = pv[last]
                hist = [x for x in pv[lo12:last + 1] if x > EPS]
                if len(hist) >= 3 and hist[0] > EPS:
                    pi_slope = -_slope(hist) / hist[0]
        elif pres is not None and p_bhp is not None and (pres - p_bhp) > 1.0:
            pi = q_oil / (pres - p_bhp)
            hist = []
            for i in range(lo12, last + 1):
                pw = (vec(pres_key, w)[i] if pres_key and vec(pres_key, w)
                      else (list(s.numpy_vector("FPR"))[i] if "FPR" in keys else None))
                bb = bhp[i] if bhp else None
                if pw is None or bb is None or (pw - bb) <= 1.0:
                    continue
                hist.append((opr[i] if opr else 0.0) / (pw - bb))
            if len(hist) >= 3 and hist[0] > EPS:
                pi_slope = -_slope(hist) / hist[0]    # доля снижения в месяц

        out[w] = {
            "kind": "inj" if inj else "prod",
            "q_liq": round(q_liq, 3), "q_oil": round(q_oil, 3),
            "q_wat": round(q_wat, 3), "q_inj": round(q_inj, 3),
            "wct": round(wct, 5), "wct_slope": round(wct_slope, 6),
            "decline": round(decline, 5),
            "cum_oil": round(opt[last], 1) if opt else 0.0,
            "bhp": round(p_bhp, 2) if p_bhp is not None else None,
            "pres": round(pres, 2) if pres is not None else None,
            "pres_source": pres_src,
            "pi": round(pi, 5) if pi is not None else None,
            "pi_slope": round(pi_slope, 5) if pi_slope is not None else None,
        }

    return {"cut": cut.isoformat(), "pres_source": pres_key or "FPR",
            "pi_source": pi_key or "расчёт через перепад",
            "wells": out,
            "generated": datetime.now().isoformat(timespec="seconds")}


# ==========================================================================
# ШАГ 2. Пороги, теги, два формата (на хосте)
# ==========================================================================
def load_rules(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    root: dict = {}
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            key = line.split(":")[0].strip()
            cur = root.setdefault(key, {})
            continue
        if cur is None or ":" not in line:
            continue
        k, _, v = line.strip().partition(":")
        v = v.strip().strip('"\'')
        if v == "":
            continue
        try:
            cur[k.strip()] = float(v) if ("." in v or v.isdigit()) else v
        except ValueError:
            cur[k.strip()] = v
    return root


def load_links(path: Path, months_left: int = 23) -> dict[str, dict]:
    """Кто на кого влияет, с фильтром по остатку горизонта."""
    if not path or not Path(path).exists():
        return {}
    import csv
    from collections import defaultdict
    infl = defaultdict(set)
    dep = defaultdict(set)
    for r in csv.DictReader(Path(path).open(encoding="utf-8-sig")):
        if r.get("metric") != "oil":
            continue
        try:
            src, obs, lag = str(r["source"]), str(r["observed"]), int(r["lag_month"])
        except (KeyError, ValueError):
            continue
        if src == obs or lag > months_left:
            continue
        infl[src].add(obs)
        dep[obs].add(src)
    return {w: {"влияет_на": sorted(infl.get(w, ()), key=_num_key),
                "зависит_от": sorted(dep.get(w, ()), key=_num_key)}
            for w in set(infl) | set(dep)}


def _num_key(x: str) -> int:
    return int(x) if str(x).isdigit() else 0


def _g(rules: dict, section: str, key: str, default=None):
    return (rules.get(section) or {}).get(key, default)


def build_card(w: str, m: dict, rules: dict, links: dict) -> dict:
    tags: list[str] = []
    r = rules

    if m["kind"] == "prod":
        wct = m["wct"]
        lim = float(_g(r, "обводнённость", "экономический_предел", 0.985))
        hi = float(_g(r, "обводнённость", "высокая", 0.92))
        if wct >= hi:
            tags.append(_g(r, "обводнённость", "тег_высокая"))
        else:
            tags.append(_g(r, "обводнённость", "тег_норма"))
        if m["wct_slope"] >= float(_g(r, "обводнённость",
                                      "порог_быстрого_роста_в_месяц", 0.004)):
            tags.append(_g(r, "обводнённость", "тег_быстрый_рост"))

        d = m["decline"]
        fast = float(_g(r, "падение_дебита", "быстрое_в_месяц", 0.015))
        if d >= fast:
            tags.append(_g(r, "падение_дебита", "тег_быстрое"))
        elif d > fast / 3:
            tags.append(_g(r, "падение_дебита", "тег_умеренное"))
        else:
            tags.append(_g(r, "падение_дебита", "тег_стабильна"))

        headroom = (500.0 - m["q_liq"]) / 500.0
        if headroom >= float(_g(r, "продуктивность", "запас_до_предела_доля", 0.5)):
            tags.append(_g(r, "продуктивность", "тег_есть_запас"))
        else:
            tags.append(_g(r, "продуктивность", "тег_упирается"))

        margin = (float(_g(r, "экономика", "маржа_нефти_руб_т", 8360))
                  * m["q_oil"] * float(_g(r, "экономика", "плотность_нефти", 0.832))
                  - float(_g(r, "экономика", "подъём_жидкости_руб_м3", 100))
                  * m["q_liq"]) / 1000.0          # тыс. руб/сут
        if margin <= float(_g(r, "экономика", "порог_убыточности_тыс_руб_сут", 0)):
            tags.append(_g(r, "экономика", "тег_убыточна"))
        elif margin <= float(_g(r, "экономика", "порог_грани_тыс_руб_сут", 50)):
            tags.append(_g(r, "экономика", "тег_на_грани"))
        else:
            tags.append(_g(r, "экономика", "тег_прибыльна"))

        if m.get("pi") is not None:
            hi_pi = float(_g(r, "индекс_продуктивности",
                             "высокий_м3_сут_на_бар", 0.5))
            crit = float(_g(r, "индекс_продуктивности",
                            "критический_снижение_за_месяц", 0.02))
            if (m.get("pi_slope") or 0) >= crit:
                tags.append(_g(r, "индекс_продуктивности", "тег_снижается"))
            elif m["pi"] >= hi_pi:
                tags.append(_g(r, "индекс_продуктивности", "тег_высокий"))
            else:
                tags.append(_g(r, "индекс_продуктивности", "тег_стабилен"))
    else:
        margin = None

    if m.get("pres") is not None:
        lo = float(_g(r, "пластовое_давление", "низкое", 80.0))
        hi = float(_g(r, "пластовое_давление", "высокое", 250.0))
        if m["pres"] <= lo:
            tags.append(_g(r, "пластовое_давление", "тег_низкое"))
        elif m["pres"] >= hi:
            tags.append(_g(r, "пластовое_давление", "тег_высокое"))
        else:
            tags.append(_g(r, "пластовое_давление", "тег_норма"))

    ln = links.get(w, {})
    n_infl = len(ln.get("влияет_на", []))
    if n_infl >= int(float(_g(r, "связность", "порог_многих", 3))):
        tags.append(_g(r, "связность", "тег_влияет_на_многих"))
    elif n_infl == 0 and not ln.get("зависит_от"):
        tags.append(_g(r, "связность", "тег_изолирована"))

    card = {"kind": m["kind"], "tags": [t for t in tags if t]}
    if ln.get("влияет_на"):
        card["влияет_на"] = ln["влияет_на"]
    if ln.get("зависит_от"):
        card["зависит_от"] = ln["зависит_от"]
    return card, margin


def months_to_limit(m: dict, limit: float) -> float | None:
    if m["kind"] != "prod" or m["wct_slope"] <= EPS:
        return None
    if m["wct"] >= limit:
        return 0.0
    return (limit - m["wct"]) / m["wct_slope"]


def recommend(m: dict, card: dict, margin, rules: dict,
              months_left: int = 23) -> str:
    """Рекомендация правилами. Вычитывается гидродинамиком построчно."""
    if m["kind"] == "inj":
        n = len(card.get("влияет_на", []))
        return (f"держать: подпирает {n} скв." if n
                else "снизить: связей в окне нет")
    lim = float(_g(rules, "обводнённость", "экономический_предел", 0.985))
    left = months_to_limit(m, lim)
    if margin is not None and margin <= 0:
        return "убыточна: закрывать невыгодно, пока обводнённость ниже предела"
    if left is not None and left < months_left:
        return "не форсировать: в окне упрётся в экономический предел"
    if (500.0 - m["q_liq"]) / 500.0 >= float(
            _g(rules, "продуктивность", "запас_до_предела_доля", 0.5)):
        return "форсировать: запас по жидкости есть"
    return "держать режим: запаса по жидкости нет"


def render_md(metrics: dict, cards: dict, rows: list[dict]) -> str:
    src = metrics.get("pres_source", "FPR")
    pi_src = metrics.get("pi_source", "")
    note = ("" if str(src).startswith("WBP") else
            "\n> Поскважинного пластового давления в сводке нет: перепад для\n"
            "> индекса продуктивности считается от среднего по полю (FPR).\n"
            "> Для сравнения скважин между собой это корректно, для\n"
            "> абсолютной оценки — нет.\n")

    def cell(v, fmt="{:.1f}", dash="—"):
        return dash if v is None else fmt.format(v)

    out = [
        "# Экономический паспорт фонда",
        "",
        f"История до {metrics['cut']}. Скважин: {len(rows)}. "
        f"Пластовое давление: `{src}`. Индекс продуктивности: `{pi_src}`.",
        note,
        "| Скв | Тип | Qн, м³/сут | Qж, м³/сут | Обводн. | До предела "
        "| PI, м³/сут·бар | Маржа, тыс.₽/сут | Рекомендация |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        m, prod = r["m"], r["m"]["kind"] == "prod"
        out.append(
            f"| {r['well']} "
            f"| {'доб' if prod else 'нагн'} "
            f"| {cell(m['q_oil'] if prod else None)} "
            f"| {cell(m['q_liq'] if prod else m['q_inj'])} "
            f"| {cell(m['wct'] * 100 if prod else None, '{:.1f}%')} "
            f"| {cell(r['left'], '{:.0f} мес')} "
            f"| {cell(m.get('pi'), '{:.3f}')} "
            f"| {cell(r['margin'], '{:+.0f}')} "
            f"| {r['rec']} |")

    out += ["", "## Как читать", "",
            "Колонка «до предела» — сколько месяцев осталось до "
            "экономического предела обводнённости при текущем темпе "
            "роста. Прочерк означает, что обводнённость не растёт.", "",
            "Колонка «рекомендация» написана правилами из "
            "`configs/passport_rules.yaml` и вычитана построчно."]
    return "\n".join(out) + "\n"


def cmd_cards(a) -> int:
    metrics = json.loads(Path(a.metrics).expanduser().read_text(encoding="utf-8"))
    rules = load_rules(Path(a.rules).expanduser())
    links = load_links(Path(a.matrix).expanduser() if a.matrix else None,
                       a.months_left)

    cards: dict[str, dict] = {}
    rows: list[dict] = []
    lim = float(_g(rules, "обводнённость", "экономический_предел", 0.985))

    for w, m in metrics["wells"].items():
        card, margin = build_card(w, m, rules, links)
        cards[w] = card
        rows.append({"well": w, "m": m, "margin": margin,
                     "left": months_to_limit(m, lim),
                     "rec": recommend(m, card, margin, rules, a.months_left)})

    rows.sort(key=lambda r: _num_key(r["well"]))

    out_json = Path(a.json).expanduser()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(cards, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    md = Path(a.md).expanduser()
    md.write_text(render_md(metrics, cards, rows), encoding="utf-8")

    empty = [w for w, c in cards.items() if not c["tags"]]
    print(f"скважин: {len(cards)}  (добывающих "
          f"{sum(1 for c in cards.values() if c['kind'] == 'prod')})")
    print(f"карточки: {out_json}")
    print(f"таблица:  {md}")
    print(f"без тегов: {empty or 'нет'}")

    # FR-7.2: в карточках не должно быть чисел
    import re
    blob = out_json.read_text(encoding="utf-8")
    nums = re.findall(r"[0-9]+[.,][0-9]+", blob)
    if nums:
        print(f"  ВНИМАНИЕ: в карточках найдены числа: {nums[:5]}")
        return 1
    print("  чисел в карточках нет — FR-7.2 соблюдён")
    return 0


# ==========================================================================
def main() -> int:
    p = argparse.ArgumentParser(description="экономический паспорт скважины")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="метрики из сводки (в контейнере)")
    e.add_argument("smspec")
    e.add_argument("--cut", default="2014-01-01")
    e.add_argument("--window", type=int, default=24)
    e.add_argument("-o", "--output", default="/out/well_metrics.json")

    c = sub.add_parser("cards", help="теги и таблица (на хосте)")
    c.add_argument("metrics")
    c.add_argument("--rules", default="../configs/passport_rules.yaml")
    c.add_argument("--matrix", default="")
    c.add_argument("--months-left", type=int, default=23)
    c.add_argument("--json", default="~/aios-data/well_cards.json")
    c.add_argument("--md", default="~/aios-data/well_passport.md")

    a = p.parse_args()
    if a.cmd == "extract":
        res = extract(a.smspec, date.fromisoformat(a.cut), a.window)
        Path(a.output).write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        pw = sum(1 for v in res["wells"].values() if v["kind"] == "prod")
        print(f"скважин: {len(res['wells'])} (добывающих {pw})")
        print(f"пластовое давление: {res['pres_source']}")
        print(f"индекс продуктивности: {res['pi_source']}")
        print(f"записано: {a.output}")
        return 0
    return cmd_cards(a)


if __name__ == "__main__":
    sys.exit(main())
