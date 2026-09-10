#!/usr/bin/env bash
# =========================================================================
# ТЕСТ НА КРУГ: сгенерировать -> прогнать -> прочитать -> посчитать ЧДД.
#
# Это ровно то, что сделают организаторы с нашим файлом. Если здесь
# проходит — пройдёт и у них.
#
#   ./run_schedule.sh                      деку взять из MODEL_SCHED
#   ./run_schedule.sh --inc FILE.inc       ★ собрать деку из расписания
#   ./run_schedule.sh ~/aios/model/X out   произвольная готовая дека
#
# Флаг --inc — основной способ. Он собирает деку из базовой (MODEL_DIR)
# и указанного расписания тем же кодом, что и цикл: директива INCLUDE
# прописывается автоматически, горизонт берётся из самого .inc.
#
# Раньше умолчанием была Model_Y_sched — дека, оставшаяся от старых
# экспериментов с горизонтом до 2022 года. Тест на круг на ней давал
# ЧДД за девять лет вместо 23 месяцев, а переменная MODEL_DIR
# игнорировалась.
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

INC=""
if [ "${1:-}" = "--inc" ]; then
    INC="${2:?укажите путь к .inc}"; shift 2
fi

DECK_DIR="${1:-${MODEL_SCHED:-$MODEL_ROOT/Model_Y_sched}}"
TAG="${2:-sched}"
OUT="$RUNS_DIR/$TAG"

# --- сборка деки из расписания ------------------------------------------
if [ -n "$INC" ]; then
    [ -f "$INC" ] || { echo "${R}нет файла расписания: $INC${N}"; exit 1; }
    require_model "$MODEL_DIR" || exit 1
    DECK_DIR="$RUNS_DIR/${TAG}_deck"
    echo "${B}Сборка деки из расписания${N}"
    echo "  базовая дека:  ${MODEL_DIR/#$HOME/\~}"
    echo "  расписание:    ${INC/#$HOME/\~}"
    echo "  последняя дата: $(grep -i -A1 "^DATES" "$INC" | grep -oE "[0-9]{2} .[A-Z]{3}. [0-9]{4}" | tail -1)"
    rm -rf "$DECK_DIR"
    python3 - "$MODEL_DIR" "$DECK_DIR" "$CUT_DATE" "$INC" <<'PY' || exit 1
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, ".")
from schedule_builder import build_deck
base, out, cut, inc = sys.argv[1:5]
build_deck(Path(base), Path(out), date.fromisoformat(cut), Path(inc), force=True)
print(f"  дека собрана: {out}")
PY
fi

require_model "$DECK_DIR" || exit 1
DECK="$(deck_name "$DECK_DIR")"

echo "${B}Прогон расписания${N}"
echo "  дека: ${DECK_DIR/#$HOME/\~}/$DECK"
disk_status
disk_guard || exit 1
mem_guard  || exit 1

# не затираем предыдущий результат молча
if has_result "$OUT"; then
    echo "  ${Y}в $OUT уже есть результат — сохраняю как ${TAG}_prev${N}"
    rm -rf "${OUT}_prev"; mv "$OUT" "${OUT}_prev"
fi
mkdir -p "$OUT"

START=$(date +%s)
run_flow "$DECK_DIR" "$DECK" "$OUT" 4
TOOK=$(( $(date +%s) - START ))

if ! run_ok "$OUT"; then
    echo "  ${R}ПРОГОН УПАЛ${N} — расписание не читается моделью:"
    grep -iE "^Error|Problem with keyword|not formatted" "$OUT"/*.PRT 2>/dev/null | head -8
    tail -6 "$OUT/flow.log"
    exit 1
fi
echo "  ${G}прогон успешен${N}, ${TOOK} с"

echo
echo "${B}ЧДД${N}"
run_py summary_to_csv.py "$OUT" \
    "/out/$(basename "$DECK" .DATA).SMSPEC" -o /out/chdd_input.csv \
    --start "$CUT_DATE"

# ВАЖНО: без --charge-initial-pump. На управляющем окне первая строка
# каждой скважины — начало периода, и флаг начислил бы первичную
# установку всем добывающим. Планка считается так же — допущения совпадают.
python3 "$SCRIPT_DIR/npv_official.py" "$OUT/chdd_input.csv" \
    --start-year "${CUT_DATE%%-*}" --json "$OUT/npv.json" || exit 1

# --- сравнение с планкой -----------------------------------------------
BASE_JSON="$RUNS_DIR/baseline/baseline_npv.json"
if [ -f "$BASE_JSON" ]; then
    echo
    python3 - "$BASE_JSON" "$OUT/npv.json" <<'PY'
import json, sys
b = json.load(open(sys.argv[1], encoding='utf-8'))["npv_mln_rub"]
c = json.load(open(sys.argv[2], encoding='utf-8'))["npv_mln_rub"]
d = c - b
sign = "+" if d >= 0 else ""
print(f"  планка (baseline): {b:>12,.1f} млн руб")
print(f"  это расписание:    {c:>12,.1f} млн руб")
print(f"  дельта:            {sign}{d:>11,.1f} млн руб  ({sign}{d/b*100:.2f}%)")
print()
if d > 0:
    print("  \033[32mрасписание лучше бездействия\033[0m")
else:
    print("  \033[33mхуже планки — оптимизация пока не работает\033[0m")
PY
else
    echo "  (планки нет — сначала ./prepare_baseline.sh)"
fi

# --- сверка с заявляемым числом ----------------------------------------
CLAIMED="$AIOS_ROOT/submission/npv_claimed.txt"
if [ -f "$CLAIMED" ]; then
    echo
    python3 - "$CLAIMED" "$OUT/npv.json" <<'PY'
import json, sys
claimed = float(open(sys.argv[1], encoding='utf-8').read().strip().replace(",", "."))
ours = json.load(open(sys.argv[2], encoding='utf-8'))["npv_mln_rub"]
d = ours - claimed
print(f"  заявлено в submission: {claimed:>12,.3f} млн руб")
print(f"  этот прогон:           {ours:>12,.3f} млн руб")
if abs(d) < 0.01:
    print("  \033[32mТЕСТ НА КРУГ ПРОЙДЕН\033[0m — расписание воспроизводит заявленное число")
else:
    print(f"  \033[31mРАСХОЖДЕНИЕ {d:+,.3f} млн — СДАВАТЬ НЕЛЬЗЯ\033[0m")
    sys.exit(1)
PY
    [ $? -ne 0 ] && exit 1
fi

echo
echo "результаты: $OUT"
