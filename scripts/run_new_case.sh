#!/usr/bin/env bash
# ==========================================================================
# Прогон решения на второй части валидационного кейса.
#
#   ./run_new_case.sh <каталог новой деки> <отсечка> <конец> [кандидатов]
#
# Пример:
#   ./run_new_case.sh ~/aios/model/Model_Y2_opm 2016-01-01 2018-12-01 8
#
# Делает всё по порядку и останавливается на первой ошибке:
#   1. проверка деки              2. планка
#   3. метрики фонда              4. цикл оптимизации
#   5. тест на круг               6. валидатор физики
#
# Коммитится ДО заморозки кода: после 17:00 логику менять нельзя, а этот
# скрипт — часть решения, а не правка на ходу.
# ==========================================================================
set -euo pipefail

AIOS_ROOT="${AIOS_ROOT:-$HOME/aios}"
DATA_ROOT="${DATA_ROOT:-$HOME/aios-data}"
IMAGE="${IMAGE:-aios/opm:2026.04}"

DECK="${1:?укажите каталог деки}"
CUT="${2:?укажите дату отсечки, ГГГГ-ММ-ДД}"
END="${3:?укажите конец горизонта, ГГГГ-ММ-ДД}"
CAND="${4:-8}"
WD="$DATA_ROOT/case_$(date +%H%M)"

say()  { printf '\n\033[1m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

say "0. Вход"
echo "  дека:       $DECK"
echo "  окно:       $CUT .. $END"
echo "  кандидатов: $CAND"
echo "  каталог:    $WD"
[ -d "$DECK" ] || die "нет каталога деки $DECK"
SCH="$(find "$DECK" -iname '*sch*.inc' | head -1)"
[ -n "$SCH" ] || die "в деке не найден файл расписания *sch*.inc"
echo "  расписание: $SCH"

# Дека приходит из tNavigator и без правок OPM Flow не читает.
# Журнал правок — model/CHANGES_FOR_OPM.md. Проверяем самое частое.
say "1. Проверка деки на конструкции tNavigator"
for kw in '#if' 'IMPLICIT' 'WOMT' 'WOMR'; do
    if grep -rqi -- "$kw" "$DECK" 2>/dev/null; then
        echo "  НАЙДЕНО: $kw — см. model/CHANGES_FOR_OPM.md"
    fi
done
grep -q "$(date -d "$CUT" '+%d' 2>/dev/null || echo 01)" "$SCH" \
    || echo "  предупреждение: дату отсечки в расписании глазами не видно"

say "2. Планка: режимы заморожены"
cd "$AIOS_ROOT/scripts"
MODEL_DIR="$DECK" ./prepare_baseline.sh "$CUT" "${END:0:4}" \
    || die "планка не посчиталась"

say "3. Метрики фонда на новую отсечку"
docker run --rm \
    -v "$DATA_ROOT/runs/history_full":/out \
    -v "$AIOS_ROOT/scripts":/scripts \
    -v "$AIOS_ROOT/configs":/configs \
    -w /scripts "$IMAGE" \
    python3 well_passport.py extract /out/MODEL_Y.SMSPEC \
        --cut "$CUT" -o /out/well_metrics.json \
    || echo "  метрики не собрались — цикл отработает и без паспорта"

python3 well_passport.py cards "$DATA_ROOT/runs/history_full/well_metrics.json" \
    --rules ../configs/passport_rules.yaml \
    --json "$DATA_ROOT/well_cards.json" \
    --md "$DATA_ROOT/well_passport.md" \
    --policy "$DATA_ROOT/well_policy.json" || true

say "4. Цикл оптимизации"
echo "  ожидаемое время: около $((CAND * 5)) минут"
MODEL_DIR="$DECK" python3 -u rolling_horizon.py run \
    --llm off --candidates "$CAND" \
    --cut "$CUT" --end "$END" \
    --constraints ../configs/constraints_case.yaml \
    --events ../configs/events_case.txt \
    --workdir "$WD" 2>&1 | tee "$DATA_ROOT/logs/case_$(date +%H%M).log"

say "5. Обязательные остановки на ремонт"
python3 add_repairs.py "$WD" --write || echo "  ремонтов в окне нет"
INC="$WD/wells_schedule_repairs.inc"
[ -f "$INC" ] || INC="$WD/wells_schedule.inc"
echo "  расписание: $INC"

say "6. Тест на круг"
./run_schedule.sh --inc "$INC" || die "тест на круг не прошёл"

say "7. Валидатор физики"
./validate.sh "$DATA_ROOT/runs/sched" --since "$CUT"

say "ГОТОВО"
echo "  расписание: $INC"
echo "  число берите из строки «ЧДД = … млн руб» выше"
echo "  и положите его в submission/npv_claimed.txt"
