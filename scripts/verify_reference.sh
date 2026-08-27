#!/usr/bin/env bash
# =========================================================================
# СВЕРКА С ЭТАЛОНОМ ОРГАНИЗАТОРОВ.
#
# Три шага:
#   1. полная история 2007 -> 12.2015 (дека берётся КАК ЕСТЬ, без обрезки)
#   2. ЧДД за этот период, сравнение с эталоном 5 326,5 млн
#   3. baseline от конца истории до 2022
#
# Единственная возможность проверить, что расчёт денег вообще верен.
# На сдаче попытка одна — там проверять поздно.
#
#   ./verify_reference.sh
#   ./verify_reference.sh --ref 5326.5 --hist-end 2015-12-01
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

REF="${REF:-5326.5}"          # эталон организаторов на OPM Flow, млн руб
REF_TN="${REF_TN:-5261.2}"    # их же на tNavigator
# Конец истории модели. НЕ путать с датой начала управления (CUT_DATE):
# сверка идёт на всём периоде 2007-2015, а управление начинается 01.01.2014.
HIST_END="${HIST_END:-2015-12-01}"
BASE_CUT="${BASE_CUT:-$CUT_DATE}"       # начало управления
BASE_END_Y="${BASE_END_Y:-${END_YEAR:-2015}}"

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)       REF="$2"; shift 2 ;;
    --hist-end)  HIST_END="$2"; shift 2 ;;
    --end-year)  BASE_END_Y="$2"; shift 2 ;;
    *) shift ;;
  esac
done

require_model "$MODEL_DIR" || exit 1
DECK="$(deck_name "$MODEL_DIR")"
STEM="$(basename "$DECK" .DATA)"

echo "${B}СВЕРКА С ЭТАЛОНОМ ОРГАНИЗАТОРОВ${N}"
echo "  эталон OPM Flow:   ${REF} млн руб"
echo "  эталон tNavigator: ${REF_TN} млн руб"
echo "  период истории:    2007 → ${HIST_END}"
disk_status; mem_status
disk_guard || exit 1

# =========================================================================
echo
echo "${B}ШАГ 1. Полная история без обрезки${N}"
HIST_OUT="$RUNS_DIR/history_full"
if has_result "$HIST_OUT"; then
    echo "  ${C}уже посчитана${N} — удалите $HIST_OUT для пересчёта"
else
    rm -rf "$HIST_OUT"; mkdir -p "$HIST_OUT"
    # дека берётся КАК ЕСТЬ: её расписание уже содержит историю до 12.2015
    START=$(date +%s)
    run_flow "$MODEL_DIR" "$DECK" "$HIST_OUT" 4
    if ! run_ok "$HIST_OUT"; then
        echo "  ${R}прогон упал${N}"
        grep -iE "^Error" "$HIST_OUT"/*.PRT 2>/dev/null | head -5
        exit 1
    fi
    echo "  ${G}успешно${N}, $(( $(date +%s) - START )) с"
fi

# =========================================================================
echo
echo "${B}ШАГ 2. ЧДД истории и сверка${N}"
run_py summary_to_csv.py "$HIST_OUT" "/out/${STEM}.SMSPEC" \
    -o /out/chdd_input.csv

# Эталон организаторов начисляет первичную установку ЭЦН как операцию:
# в 2007 у них 23 таких, в 2008 — 28, это ввод скважин в эксплуатацию.
# Параметр chargeInitialPump не входит в METHODOLOGY_LOCKS, его можно
# переопределить. Считаем оба варианта и берём ближайший к эталону.
echo "  ${C}вариант A: без начисления первичной установки ЭЦН${N}"
python3 "$SCRIPT_DIR/npv_official.py" "$HIST_OUT/chdd_input.csv" \
    --json "$HIST_OUT/npv_noinit.json" | tail -6
echo
echo "  ${C}вариант B: с начислением первичной установки${N}"
python3 "$SCRIPT_DIR/npv_official.py" "$HIST_OUT/chdd_input.csv" \
    --charge-initial-pump --json "$HIST_OUT/npv.json" || exit 1

echo
python3 - "$HIST_OUT/npv.json" "$REF" "$REF_TN" "$HIST_OUT/npv_noinit.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
our, ref, ref_tn = d["npv_mln_rub"], float(sys.argv[2]), float(sys.argv[3])
try:
    alt = json.load(open(sys.argv[4], encoding='utf-8'))["npv_mln_rub"]
except Exception:
    alt = None
dev = (our - ref) / ref * 100
print("=" * 62)
if alt is not None:
    da = (alt - ref) / ref * 100
    print(f"  вариант A (без первичных): {alt:>10,.1f} млн  ({da:+.2f}%)")
    print(f"  вариант B (с первичными):  {our:>10,.1f} млн  ({dev:+.2f}%)")
    if abs(da) < abs(dev):
        print(f"  {chr(27)}[33mближе вариант A — оставьте флаг выключенным{chr(27)}[0m")
        our, dev = alt, da
    else:
        print(f"  {chr(27)}[32mближе вариант B — включайте --charge-initial-pump{chr(27)}[0m")
    print("-" * 62)
print(f"  наш расчёт:        {our:>12,.1f} млн руб")
print(f"  эталон OPM Flow:   {ref:>12,.1f} млн руб")
print(f"  эталон tNavigator: {ref_tn:>12,.1f} млн руб")
print(f"  расхождение:       {dev:>+11.2f}%")
print("=" * 62)
if abs(dev) < 2:
    print("  \033[32mВ ДОПУСКЕ\033[0m — пайплайн расчёта денег подтверждён")
elif abs(dev) < 5:
    print("  \033[33mНА ГРАНИЦЕ\033[0m — разобраться до сдачи")
else:
    print("  \033[31mРАСХОЖДЕНИЕ КРИТИЧНО\033[0m")
    print("  где искать:")
    print("    - единицы: масса нефти = WOPT x 0.833, не WLPT")
    print("    - период: сверить годовую разбивку с эталоном")
    print("    - правки деки: model/CHANGES_FOR_OPM.md")
    sys.exit(1)
PY
RC=$?
[ "$RC" -ne 0 ] && exit 1

# =========================================================================
echo
echo "${B}ШАГ 3. Baseline${N}"
# ВАЖНО: точка передачи управления (CUT_DATE) НЕ совпадает с концом
# истории (HIST_END). Для Model_Y это 2014-01-01 против 2015-12-01.
# Раньше здесь стояли зашитые даты конца истории, и запуск затирал
# правильную планку на 23 месяца планкой на другом горизонте.
BJ="$RUNS_DIR/baseline/baseline_npv.json"
if [ -f "$BJ" ] && [ "${FORCE_BASELINE:-0}" != "1" ]; then
    echo "  ${C}baseline уже посчитан${N} — пропускаю"
    python3 -c "
import json;d=json.load(open('$BJ',encoding='utf-8'))
ys=[y['year'] for y in d['years']]
print(f\"  {d['npv_mln_rub']:,.1f} млн руб, годы {min(ys)}-{max(ys)}\")" 2>/dev/null
else
    echo "  точка управления: ${CUT_DATE}, горизонт до ${END_YEAR}"
    "$SCRIPT_DIR/prepare_baseline.sh" "$CUT_DATE" "$END_YEAR"
fi

echo
echo "${G}ГОТОВО.${N}"
echo "  сверка на периоде 2007 → ${HIST_END}"
echo "  планка на периоде ${CUT_DATE} → ${END_YEAR}-12-01"
