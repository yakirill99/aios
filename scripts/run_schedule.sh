#!/usr/bin/env bash
# =========================================================================
# ТЕСТ НА КРУГ: сгенерировать -> прогнать -> прочитать -> посчитать ЧДД.
#
# Это ровно то, что сделают организаторы с нашим файлом. Если здесь
# проходит — пройдёт и у них.
#
#   ./run_schedule.sh                       прогнать деку с расписанием
#   ./run_schedule.sh ~/aios/model/X out     произвольная дека
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

DECK_DIR="${1:-$MODEL_ROOT/Model_Y_sched}"
TAG="${2:-sched}"
OUT="$RUNS_DIR/$TAG"

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

echo
echo "результаты: $OUT"
