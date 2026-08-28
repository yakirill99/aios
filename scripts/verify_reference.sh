#!/usr/bin/env bash
# =========================================================================
# СВЕРКА С ЭТАЛОНОМ ОРГАНИЗАТОРОВ.  ТОЛЬКО ЧТЕНИЕ.
#
# Скрипт НИЧЕГО не пересобирает без явного флага. Он:
#   ШАГ 1. считает полную историю 2007 -> 01.12.2015 (дека КАК ЕСТЬ);
#   ШАГ 2. считает по ней ЧДД эталонным кодом и сверяет с 5 326,5 млн;
#   ШАГ 3. ПРОВЕРЯЕТ существующую планку: 23 месяца, 2014=12, 2015=11.
#
# Почему шаг 3 больше не пересчитывает планку:
#   точка передачи управления (CUT_DATE = 2014-01-01) НЕ совпадает с концом
#   истории (2015-12-01). Прежняя версия собирала baseline от конца истории
#   и затирала правильную планку на 23 месяца планкой на другом горизонте.
#   Пересборка теперь только по явному --with-baseline (+ --force при
#   существующей планке).
#
# Использование:
#   ./verify_reference.sh                     сверка + проверка планки
#   ./verify_reference.sh --check-only        без прогона симулятора
#   ./verify_reference.sh --with-baseline     ещё и собрать планку, если её нет
#   ./verify_reference.sh --with-baseline --force   пересобрать поверх
#   ./verify_reference.sh --rerun-history     пересчитать историю заново
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

REF="${REF:-5326.5}"            # эталон организаторов на OPM Flow, млн руб
REF_TN="${REF_TN:-5261.2}"      # их же на tNavigator
TOL_PCT="${TOL_PCT:-2}"         # допуск расхождения, %

# Конец истории модели. НЕ путать с CUT_DATE: сверка идёт на всём периоде
# 2007-2015, а управление начинается 01.01.2014.
HIST_END="${HIST_END:-$END_DATE}"

# Ожидаемая раскладка управляющего периода (подтверждено организаторами:
# «23 управляющих шага: 12 месяцев 2014 г. + 11 месяцев 2015 г.», декабрь
# 2015 разности не образует).
EXPECT_MONTHS="${EXPECT_MONTHS:-23}"
EXPECT_LAST_YEAR_MONTHS="${EXPECT_LAST_YEAR_MONTHS:-11}"

WITH_BASELINE=0
FORCE_BASELINE="${FORCE_BASELINE:-0}"
CHECK_ONLY=0
RERUN_HISTORY=0

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)            REF="$2"; shift 2 ;;
    --ref-tn)         REF_TN="$2"; shift 2 ;;
    --hist-end)       HIST_END="$2"; shift 2 ;;
    --tol)            TOL_PCT="$2"; shift 2 ;;
    --with-baseline)  WITH_BASELINE=1; shift ;;
    --force)          FORCE_BASELINE=1; shift ;;
    --check-only)     CHECK_ONLY=1; shift ;;
    --rerun-history)  RERUN_HISTORY=1; shift ;;
    -h|--help)        usage ;;
    # раньше здесь стоял молчаливый `shift` — опечатка в флаге проглатывалась
    # и скрипт считал не то, что просили. Теперь падаем сразу.
    *) echo "${R}неизвестный аргумент: $1${N}"; echo "  ./verify_reference.sh --help"; exit 2 ;;
  esac
done

# --- один экземпляр за раз ----------------------------------------------
# Два одновременных прогона пишут в один и тот же RUNS_DIR/history_full и
# затирают результаты друг друга. Уже теряли на этом часы.
LOCK="$LOGS_DIR/verify_reference.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "${R}сверка уже запущена${N} (${LOCK/#$HOME/\~}, PID $(cat "$LOCK/pid" 2>/dev/null || echo '?'))"
    echo "  если процесс мёртв:  rm -rf $LOCK"
    exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM

# --- тайминги -----------------------------------------------------------
T0=$(date +%s)
ts() { printf '[%s +%4ss] %s\n' "$(date '+%F %T')" "$(( $(date +%s) - T0 ))" "$*"; }

RUN_LOG="$LOGS_DIR/verify_$(date '+%Y%m%d_%H%M%S').log"

require_model "$MODEL_DIR" || exit 1
DECK="$(deck_name "$MODEL_DIR")"
STEM="$(basename "$DECK" .DATA)"
HIST_OUT="$RUNS_DIR/history_full"
BJ="$RUNS_DIR/baseline/baseline_npv.json"

echo "${B}СВЕРКА С ЭТАЛОНОМ ОРГАНИЗАТОРОВ${N}"
ts "старт, лог: ${RUN_LOG/#$HOME/\~}"
echo "  эталон OPM Flow:   ${REF} млн руб (допуск ±${TOL_PCT}%)"
echo "  эталон tNavigator: ${REF_TN} млн руб"
echo "  период истории:    2007 → ${HIST_END}"
echo "  точка управления:  ${CUT_DATE} → ${END_DATE} (${EXPECT_MONTHS} мес)"
echo "  дека:              ${MODEL_DIR/#$HOME/\~}/${DECK}"
echo "  результат:         ${HIST_OUT/#$HOME/\~}"
disk_status; mem_status
disk_guard || exit 1

# =========================================================================
echo
echo "${B}ШАГ 1. Полная история без обрезки${N}"
if [ "$RERUN_HISTORY" = "1" ] && has_result "$HIST_OUT"; then
    # единственное место, где что-то удаляется, и только по явному флагу
    echo "  ${Y}--rerun-history: удаляю ${HIST_OUT/#$HOME/\~}${N}"
    read -r -p "  подтвердите (yes): " ans
    [ "$ans" = "yes" ] || { echo "  отменено"; exit 1; }
    rm -rf "$HIST_OUT"
fi

if has_result "$HIST_OUT"; then
    ts "история уже посчитана — переиспользую (--rerun-history для пересчёта)"
elif [ "$CHECK_ONLY" = "1" ]; then
    echo "  ${R}--check-only, а истории нет${N}: ${HIST_OUT/#$HOME/\~}"
    exit 1
else
    mkdir -p "$HIST_OUT"
    ts "запускаю OPM Flow, ожидание ~90-230 с"
    START=$(date +%s)
    # дека берётся КАК ЕСТЬ: её расписание уже содержит историю до 12.2015
    run_flow "$MODEL_DIR" "$DECK" "$HIST_OUT" 4
    if ! run_ok "$HIST_OUT"; then
        echo "  ${R}прогон упал${N}"
        grep -iE "^Error" "$HIST_OUT"/*.PRT 2>/dev/null | head -5
        exit 1
    fi
    ts "${G}прогон успешен${N}, $(( $(date +%s) - START )) с, пик $(maxrss_of "$HIST_OUT") КБ"
fi

# =========================================================================
echo
echo "${B}ШАГ 2. ЧДД истории и сверка${N}"
ts "строю CSV для эталонного расчёта"
run_py summary_to_csv.py "$HIST_OUT" "/out/${STEM}.SMSPEC" \
    -o /out/chdd_input.csv || { echo "${R}мост UNSMRY→CSV упал${N}"; exit 1; }

# Эталон организаторов начисляет первичную установку ЭЦН как операцию:
# в 2007 у них 23 таких, в 2008 — 28, это ввод скважин в эксплуатацию.
# Параметр chargeInitialPump не входит в METHODOLOGY_LOCKS, его можно
# переопределить. Считаем оба варианта и берём ближайший к эталону.
ts "вариант A: без начисления первичной установки ЭЦН"
python3 "$SCRIPT_DIR/npv_official.py" "$HIST_OUT/chdd_input.csv" \
    --json "$HIST_OUT/npv_noinit.json" | tail -6
echo
ts "вариант B: с начислением первичной установки"
python3 "$SCRIPT_DIR/npv_official.py" "$HIST_OUT/chdd_input.csv" \
    --charge-initial-pump --json "$HIST_OUT/npv.json" || exit 1

echo
python3 - "$HIST_OUT/npv.json" "$REF" "$REF_TN" "$HIST_OUT/npv_noinit.json" "$TOL_PCT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
our, ref, ref_tn, tol = d["npv_mln_rub"], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[5])
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
        print("  \033[33mближе вариант A — оставьте флаг выключенным\033[0m")
        our, dev = alt, da
    else:
        print("  \033[32mближе вариант B — включайте --charge-initial-pump\033[0m")
    print("-" * 62)
print(f"  наш расчёт:        {our:>12,.1f} млн руб")
print(f"  эталон OPM Flow:   {ref:>12,.1f} млн руб")
print(f"  эталон tNavigator: {ref_tn:>12,.1f} млн руб")
print(f"  расхождение:       {dev:>+11.2f}%")
print("=" * 62)
if abs(dev) < tol:
    print("  \033[32mВ ДОПУСКЕ\033[0m — пайплайн расчёта денег подтверждён")
elif abs(dev) < tol * 2.5:
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
if [ "$RC" -ne 0 ]; then exit 1; fi

# =========================================================================
echo
echo "${B}ШАГ 3. Планка (baseline)${N}"
# ВАЖНО: сверка идёт на истории 2007-2015, а планка — на управляющем
# периоде ${CUT_DATE}..${END_DATE}. Это РАЗНЫЕ горизонты. Сам скрипт
# планку не пересобирает: только читает и проверяет раскладку месяцев.
echo "  файл: ${BJ/#$HOME/\~}"

if [ ! -f "$BJ" ]; then
    echo "  ${Y}планки нет${N}"
    if [ "$WITH_BASELINE" = "1" ]; then
        ts "--with-baseline: собираю планку на ${CUT_DATE} → ${END_YEAR}"
        "$SCRIPT_DIR/prepare_baseline.sh" "$CUT_DATE" "$END_YEAR" || exit 1
    else
        echo "  собрать: ${C}$SCRIPT_DIR/prepare_baseline.sh $CUT_DATE $END_YEAR${N}"
        echo "  или запустить сверку с флагом --with-baseline"
    fi
elif [ "$WITH_BASELINE" = "1" ] && [ "$FORCE_BASELINE" = "1" ]; then
    echo "  ${Y}--with-baseline --force: пересобираю поверх существующей${N}"
    read -r -p "  подтвердите (yes): " ans
    if [ "$ans" = "yes" ]; then
        "$SCRIPT_DIR/prepare_baseline.sh" "$CUT_DATE" "$END_YEAR" || exit 1
    else
        echo "  отменено, оставляю как есть"
    fi
elif [ "$WITH_BASELINE" = "1" ]; then
    echo "  ${C}планка уже есть — не трогаю${N} (пересобрать: --with-baseline --force)"
fi

if [ -f "$BJ" ]; then
    echo
    python3 - "$BJ" "$EXPECT_MONTHS" "$EXPECT_LAST_YEAR_MONTHS" "$END_YEAR" <<'PY'
import json, sys
path, exp_total, exp_last, end_year = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
d = json.load(open(path, encoding='utf-8'))
years = d.get("years", [])
print(f"  ЧДД планки: {d['npv_mln_rub']:,.3f} млн руб")
print(f"  {'год':>6} {'мес':>4} {'скв-мес':>8} {'Qн,тыс.т':>10} {'ЧДД':>10}")
total = 0
bad = []
for y in years:
    m = int(y.get("months") or 0)
    total += m
    awm = y.get("activeWellMonths") or 0
    print(f"  {y.get('year',''):>6} {m:>4} {awm:>8,.0f} "
          f"{y.get('oilKt',0):>10,.1f} {y.get('chddM',0):>10,.1f}")
    if int(y.get("year", 0)) == end_year and m != exp_last:
        bad.append(f"{end_year}: месяцев {m}, ожидается {exp_last} "
                   f"(декабрь {end_year} разности не образует)")
if total != exp_total:
    bad.append(f"всего месяцев {total}, ожидается {exp_total}")
print()
if bad:
    print("  \033[31mПЛАНКА НЕВЕРНА\033[0m")
    for b in bad:
        print(f"    - {b}")
    print("    пересчитать: prepare_baseline.sh с текущим summary_to_csv.py")
    print("    (метка даты = месяц ДОБЫЧИ, а не точки сводки)")
    sys.exit(1)
print(f"  \033[32mраскладка верна\033[0m: всего {total} мес, "
      f"{end_year} — {exp_last} мес")
PY
    BRC=$?
    if [ "$BRC" -ne 0 ]; then
        echo
        echo "${R}ГОТОВО СО СВЕРКОЙ, НО ПЛАНКА ТРЕБУЕТ ПЕРЕСЧЁТА.${N}"
        exit 1
    fi
fi

echo
ts "${G}ГОТОВО${N}"
echo "  сверка:  2007 → ${HIST_END}, эталон ${REF} млн"
echo "  планка:  ${CUT_DATE} → ${END_DATE}, ${EXPECT_MONTHS} мес"
echo "  файлы:"
echo "    $HIST_OUT/chdd_input.csv"
echo "    $HIST_OUT/npv.json"
echo "    $BJ"
