# -*- coding: utf-8 -*-
"""
Чтение состояния фонда из сводок. Запускается ВНУТРИ контейнера, где стоит
resdata, и отдаёт JSON на stdout.

    python3 read_state.py /out/MODEL_Y.SMSPEC --at 2014-01-01
"""
import argparse, json, sys
from datetime import date


def main():
    p = argparse.ArgumentParser()
    p.add_argument("smspec")
    p.add_argument("--at", required=True, help="дата среза, ISO")
    p.add_argument("--back", type=int, default=6, help="шагов назад для тренда")
    a = p.parse_args()

    from resdata.summary import Summary
    s = Summary(a.smspec)
    keys = set(s.keys())
    dates = [d.date() if hasattr(d, "date") else d for d in s.dates]
    at = date.fromisoformat(a.at)

    idx = max((i for i, d in enumerate(dates) if d <= at), default=0)
    idx_prev = max(0, idx - a.back)

    names = sorted({k.split(":", 1)[1] for k in keys if ":" in k
                    and any(f"{m}:{k.split(':',1)[1]}" in keys
                            for m in ("WOPR", "WLPR", "WWIR"))})

    out = {}
    for n in names:
        def v(vec, i=idx):
            k = f"{vec}:{n}"
            return float(s.numpy_vector(k)[i]) if k in keys else 0.0
        inj = v("WWIR")
        old = v("WOPR", idx_prev)
        cur = v("WOPR")
        out[n] = {
            "kind": "inj" if inj > 0.01 else "prod",
            "q_oil": cur, "q_liq": v("WLPR"), "q_inj": inj,
            "wct": v("WWCT"), "bhp": v("WBHP"),
            "decline": (old - cur) / old if old > 0.01 else 0.0,
        }

    json.dump({"date": dates[idx].isoformat(), "wells": out},
              sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
