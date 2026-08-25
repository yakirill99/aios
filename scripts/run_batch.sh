#!/usr/bin/env bash
# =========================================================================
# Пакетный прогон N кандидатов со стражем диска.
#
#   ./run_batch.sh 5            5 прогонов, проверка что всё работает
#   ./run_batch.sh 200 6        200 прогонов, 6 параллельно (на ночь)
#
# Страж проверяет место ПЕРЕД каждой пачкой. При достижении порога
# останавливается и пишет отчёт в logs/disk_stop.log.
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

TOTAL="${1:-5}"
PAR="${2:-auto}"
START_IDX="${3:-1}"      # с какого номера начинать — для второй машины
OUT="$RUNS_DIR/candidates"
LOG="$LOGS_DIR/batch_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$OUT"

# автоподбор по свободной памяти, если не задано явно
if [ "$PAR" = "auto" ]; then
    PAR="$(safe_parallel 8)"
    echo "параллельность подобрана по памяти: $PAR"
fi

require_model "$MODEL_DIR" || exit 1
DECK="$(deck_name "$MODEL_DIR")"

# пробные прогоны без снимков
WORK_DECK="$DATA_ROOT/batch_deck"
rm -rf "$WORK_DECK"; cp -r "$MODEL_DIR" "$WORK_DECK"
SCH="$(find "$WORK_DECK/INCLUDE" -iname '*sch*.inc' | head -1)"
[ -n "$SCH" ] && sed -i -E "s/^(RPTRST)/--\1/; s/^( *'BASIC=[0-9]'.*)/--\1/" "$SCH"

{
echo "${B}Пакет: ${TOTAL} прогонов, ${PAR} параллельно${N}"
echo "старт: $(date '+%F %T'), нумерация с ${START_IDX}"
disk_status
mem_status
echo

DONE=0; FAILED=0; START=$(date +%s)
while [ "$DONE" -lt "$TOTAL" ]; do
    if ! disk_guard; then
        echo "${R}ОСТАНОВЛЕНО ПО МЕСТУ на прогоне ${DONE}/${TOTAL}${N}"; break
    fi
    if ! mem_guard; then
        echo "${R}ОСТАНОВЛЕНО ПО ПАМЯТИ на прогоне ${DONE}/${TOTAL}${N}"; break
    fi
    # память могла измениться (браузер, IDE) — пересчитываем каждую пачку
    NOW_PAR="$(safe_parallel $PAR)"
    if [ "$NOW_PAR" -lt "$PAR" ]; then
        echo "  ${Y}память просела -> снижаю параллельность до $NOW_PAR${N}"
    fi
    batch=$(( TOTAL - DONE )); [ "$batch" -gt "$NOW_PAR" ] && batch=$NOW_PAR
    for i in $(seq 1 "$batch"); do
        idx=$(( START_IDX + DONE + i - 1 ))
        d="$OUT/run_$(printf '%04d' "$idx")"
        # уже посчитан — не перезапускаем
        if has_result "$d"; then continue; fi
        rm -rf "$d"
        run_flow "$WORK_DECK" "$DECK" "$d" 2 &
    done
    wait
    for i in $(seq 1 "$batch"); do
        idx=$(( START_IDX + DONE + i - 1 ))
        d="$OUT/run_$(printf '%04d' "$idx")"
        if run_ok "$d"; then
            printf "  %04d ok   %6s c\n" "$idx" "$(elapsed_of "$d")"
        else
            printf "  %04d ${R}УПАЛ${N}\n" "$idx"; FAILED=$((FAILED+1))
        fi
    done
    DONE=$(( DONE + batch ))
    el=$(( $(date +%s) - START ))
    printf "  --- %d/%d, прошло %d мин, %s\n" "$DONE" "$TOTAL" $((el/60)) \
           "$(disk_status | tr -d ' ')"
done

EL=$(( $(date +%s) - START ))
echo
echo "${B}Итог${N}"
echo "  выполнено: ${DONE}/${TOTAL}, упало: ${FAILED}"
echo "  время: $((EL/60)) мин, скорость: $(awk "BEGIN{printf \"%.0f\", $DONE*3600/($EL>0?$EL:1)}") прогонов/час"
disk_status
} 2>&1 | tee "$LOG"

rm -rf "$WORK_DECK"
echo "лог: $LOG"
