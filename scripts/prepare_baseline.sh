#!/usr/bin/env bash
# =========================================================================
# ОПОРНЫЙ СЦЕНАРИЙ (baseline).
#
# Что делает: берёт состояние на дату отсечки, ЗАМОРАЖИВАЕТ режимы скважин
# как есть и прогоняет вперёд до конца горизонта. Полученный ЧДД — планка,
# относительно которой меряется вся оптимизация.
#
# Отличие от prepare_history.sh: тот считает прошлое (даёт снимок и датасет),
# этот считает будущее без вмешательств.
#
#   ./prepare_baseline.sh                 отсечка 2014-01-01, горизонт из деки
#   ./prepare_baseline.sh 2014-01-01 2025 отсечка и год окончания
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

CUT="${1:-$CUT_DATE}"
END_Y="${2:-$END_YEAR}"
require_model "$MODEL_DIR" || exit 1
SRC="$MODEL_DIR"
DST="$MODEL_BASE"
OUT="$RUNS_DIR/baseline"

echo "${B}Опорный сценарий: режимы заморожены с ${CUT}${N}"

rm -rf "$DST"; cp -r "$SRC" "$DST"
SCH="$(find "$DST/INCLUDE" -iname '*sch*.inc' | head -1)"
[ -n "$SCH" ] || { echo "${R}не найден sch.inc${N}"; exit 1; }

python3 - "$SCH" "$CUT" "$END_Y" <<'PY'
import re, sys
from datetime import date

sch, cut, end_year = sys.argv[1], sys.argv[2], sys.argv[3]
MON = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
cut_d = date.fromisoformat(cut)

text = open(sch, encoding="utf-8", errors="replace").read()
lines = text.split("\n")

# ищем строку с датой отсечки
pat = re.compile(rf"^\s*{cut_d.day:02d}\s+'?{MON[cut_d.month-1]}'?\s+{cut_d.year}\s*/")
idx = next((i for i, l in enumerate(lines) if pat.match(l)), None)
if idx is None:
    sys.exit(f"дата {cut} в расписании не найдена")

# всё до отсечки — история, как есть. После — только продвижение времени
# без единого управляющего воздействия: это и есть заморозка режимов.
head = lines[:idx + 2]          # + строка с датой и её терминатор

# дальше собираем помесячные DATES до конца горизонта
last = None
for l in lines[idx:]:
    m = re.match(r"^\s*(\d+)\s+'?([A-Z]{3})'?\s+(\d{4})\s*/", l)
    if m:
        last = date(int(m.group(3)), MON.index(m.group(2)) + 1, 1)
stop = date(int(end_year), 12, 1) if end_year else last

tail, cur = [], cut_d
while cur < stop:
    y, mth = (cur.year + 1, 1) if cur.month == 12 else (cur.year, cur.month + 1)
    cur = date(y, mth, 1)
    tail.append(f"DATES\n 01 '{MON[mth-1]}' {y} /\n/")

# снимок только на последнем шаге — экономим место
out = "\n".join(head) + "\n" + "\n".join(tail) + "\n"
out = re.sub(r"^RPTRST\s*\n\s*'BASIC=\d'.*?/\s*$", "RPTRST\n 'BASIC=2' /",
             out, count=1, flags=re.M | re.S)
open(sch, "w", encoding="utf-8").write(out)
print(f"  расписание: история до {cut}, далее {len(tail)} месяцев без вмешательств")
print(f"  горизонт до {stop}")
PY
[ $? -ne 0 ] && exit 1

DECK="$(basename "$(find "$DST" -maxdepth 1 -iname '*.DATA' | head -1)")"
echo "  дека готова: $DST/$DECK"

echo
echo "${B}Прогон${N}"
disk_status; mem_status
disk_guard || exit 1
rm -rf "$OUT"; mkdir -p "$OUT"
START=$(date +%s)
run_flow "$DST" "$DECK" "$OUT" 4
echo "  время: $(( $(date +%s) - START )) с"

if ! run_ok "$OUT"; then
    echo "  ${R}прогон упал${N}:"
    grep -iE "^Error|Problem with keyword" "$OUT"/*.PRT 2>/dev/null | head -5
    exit 1
fi
echo "  ${G}успешно${N}"

echo
echo "${B}ЧДД опорного сценария${N}"
run_py summary_to_csv.py "$OUT" \
    "/out/$(basename "$DECK" .DATA).SMSPEC" -o /out/chdd_input.csv --start "$CUT"

python3 "$SCRIPT_DIR/npv_official.py" "$OUT/chdd_input.csv" \
    --start-year "${CUT%%-*}" --json "$OUT/baseline_npv.json"

echo
echo "${G}Планка зафиксирована.${N} Всё дальнейшее меряется относительно неё."
echo "  $OUT/baseline_npv.json"
