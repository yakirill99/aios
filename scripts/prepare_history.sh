#!/usr/bin/env bash
# =========================================================================
# Готовит и прогоняет ИСТОРИЮ: 01.05.2007 -> 01.01.2014
#
# Результат — два артефакта, ради которых всё и делается:
#   1) снимок пласта на 01.01.2014 (UNRST) — стартовая точка вашей зоны
#   2) помесячные ряды за 80 месяцев (UNSMRY) — единственный датасет
#
# Прогон ОДИН: симулятор детерминирован, повторы дадут то же самое.
#
#   ./prepare_history.sh            подготовить и прогнать
#   ./prepare_history.sh prepare    только подготовить деку
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

MODE="${1:-run}"
require_model "$MODEL_DIR" || exit 1
SRC="$MODEL_DIR"
DST="$MODEL_HIST"
OUT="$RUNS_DIR/history"

echo "${B}Подготовка деки истории${N}"

rm -rf "$DST"; cp -r "$SRC" "$DST"
SCH="$(find "$DST/INCLUDE" -iname '*sch*.inc' | head -1)"
[ -n "$SCH" ] || { echo "${R}не найден sch.inc${N}"; exit 1; }

# --- обрезаем расписание на дате отсечки -------------------------------
# дата отсечки из CUT_DATE (ISO) -> формат деки
CUT_PAT="$(date -d "$CUT_DATE" "+%d '%b' %Y" | tr '[:lower:]' '[:upper:]')"
CUT_LINE="$(grep -n "$CUT_PAT" "$SCH" | head -1 | cut -d: -f1)"
if [ -z "$CUT_LINE" ]; then
    echo "${R}дата $CUT_PAT в расписании не найдена${N}"; exit 1
fi
# +1 строка: терминатор '/' после даты
END_LINE=$((CUT_LINE + 1))
TOTAL="$(wc -l < "$SCH")"
head -n "$END_LINE" "$SCH" > "$SCH.tmp" && mv "$SCH.tmp" "$SCH"
echo "  расписание обрезано: строка ${END_LINE} из ${TOTAL}"

# --- снимок нужен только на последнюю дату ------------------------------
# BASIC=4 -> раз в год (7 снимков вместо 34). Последний и есть 01.01.2014
python3 - "$SCH" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8", errors="replace").read()
s = re.sub(r"^RPTRST\s*\n\s*'BASIC=5'\s*'FREQ=3'\s*/",
           "RPTRST\n 'BASIC=4' /", s, count=1, flags=re.M)
open(p, "w", encoding="utf-8").write(s)
PY
echo "  RPTRST -> BASIC=4 (снимок раз в год, последний = 01.01.2014)"

# --- векторы для ЧДД ---------------------------------------------------
# ВНИМАНИЕ: WOMT/WOMR/WWMT — ключевые слова tNavigator, OPM Flow их НЕ знает.
# Масса считается из объёма: WOPT * плотность (DENSITY в pvt.inc, ~0.833 т/м3).
# Здесь добавляем только то, что OPM реально поддерживает.
SM="$(find "$DST" -iname 'SUMMARY*.INC' | head -1)"
if [ -n "$SM" ]; then
    # чистим ошибочно добавленные ранее массовые векторы
    python3 - "$SM" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p, encoding='utf-8', errors='replace').read()
for kw in ("WOMT", "WOMR", "WWMT", "WWMR"):
    s = re.sub(r"^" + kw + r"\s*\r?\n\s*/\s*\r?\n", "", s, flags=re.M)
open(p, "w", encoding='utf-8').write(s)
PYEOF
    for kw in WOPT WOPR WWPT WWPR WTHP; do
        grep -qE "^${kw}\b" "$SM" || printf '%s\n/\n' "$kw" >> "$SM"
    done
    echo "  SUMMARY: массовые векторы убраны, объёмные проверены"
fi

DECK="$(basename "$(find "$DST" -maxdepth 1 -iname '*.DATA' | head -1)")"
echo "  дека готова: $DST/$DECK"
[ "$MODE" = "prepare" ] && exit 0

# =========================================================================
echo
echo "${B}Прогон истории${N}"
disk_status
mem_status
disk_guard || exit 1
mem_guard || exit 1

rm -rf "$OUT"; mkdir -p "$OUT"
START=$(date +%s)
run_flow "$DST" "$DECK" "$OUT" 4
TOOK=$(( $(date +%s) - START ))

if run_ok "$OUT"; then
    echo "  ${G}прогон успешен${N}, ${TOOK} с"
else
    echo "  ${R}прогон упал${N}. Причина:"
    grep -iE "^Error|Problem with keyword" "$OUT"/*.PRT 2>/dev/null | head -5
    tail -5 "$OUT/flow.log"
    exit 1
fi

echo
echo "${B}Что получилось${N}"
ls -lhS "$OUT" | head -10
echo
disk_status

echo
echo "${B}Проверка данных${N}"
docker run --rm -i $USERFLAG -v "$OUT":/out "$IMAGE" python3 - <<'PY'
from resdata.summary import Summary
import glob
f = glob.glob("/out/*.SMSPEC")
if not f:
    print("  SMSPEC не найден"); raise SystemExit(1)
s = Summary(f[0])
keys = sorted(s.keys())
print(f"  шагов по времени: {len(s)}")
print(f"  векторов: {len(keys)}")
print(f"  период: {s.get_start_time()} -> {s.get_end_time()}")
wells = sorted({k.split(':')[1] for k in keys if ':' in k})
print(f"  скважин в сводке: {len(wells)}")
for v in ("WOMT", "WWMT", "WOPR", "WWPR", "WLPR", "WWIR", "WBHP", "WWCT"):
    have = [k for k in keys if k.split(':')[0] == v]
    print(f"    {'есть' if have else 'НЕТ '}  {v}  ({len(have)} скв)")
PY

echo
echo "${G}История посчитана.${N} Дальше:"
echo "  снимок на 01.01.2014 -> $OUT/*.UNRST  (старт вашей зоны)"
echo "  датасет за 80 мес.   -> $OUT/*.UNSMRY (входные данные агентов)"
