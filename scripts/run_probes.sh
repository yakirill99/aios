#!/usr/bin/env bash
# =========================================================================
# Прогон возмущений для матрицы влияния.
#
#   ./run_probes.sh                 всё, что сгенерировано
#   ./run_probes.sh 1 50            варианты с 1-го по 50-й (для двух машин)
#
# Пропускает уже посчитанные — можно прерывать и продолжать.
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

# --- ЗАЩИТА ОТ ПОВТОРНОГО ЗАПУСКА --------------------------------------
# Два экземпляра поднимут вдвое больше контейнеров, память кончится и
# прогоны начнут падать по OOM.
LOCK="$DATA_ROOT/.$(basename "$0").lock"
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    echo "${R}уже запущен: PID $(cat "$LOCK")${N}"
    echo "  остановить:  pkill -f $(basename "$0")"
    exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

PROBES="$PROBES_DIR"
FROM="${1:-1}"; TO="${2:-999999}"
LOG="$LOGS_DIR/probes_$(date +%Y%m%d_%H%M%S).log"

[ -f "$PROBES/manifest.json" ] || {
    echo "${R}нет $PROBES/manifest.json — сначала probe_wells.py make${N}"; exit 1; }

mapfile -t TAGS < <(python3 -c "
import json,sys
m=json.load(open('$PROBES/manifest.json',encoding='utf-8'))
for v in m['variants']: print(v['tag'])
")
TOTAL=${#TAGS[@]}
[ "$TO" -gt "$TOTAL" ] && TO=$TOTAL

{
echo "${B}Возмущающие прогоны${N}"
echo "  вариантов всего: $TOTAL, обрабатываем ${FROM}..${TO}"
echo "  старт: $(date '+%F %T')"
disk_status; mem_status
echo

PAR="$(safe_parallel 8)"
echo "  параллельность: $PAR"
echo

DONE=0; SKIP=0; FAIL=0; START=$(date +%s)
i=$FROM
while [ "$i" -le "$TO" ]; do
    disk_guard || { echo "${R}СТОП по месту на варианте $i${N}"; break; }
    mem_guard  || { echo "${R}СТОП по памяти на варианте $i${N}"; break; }
    NOW_PAR="$(safe_parallel $PAR)"

    batch=()
    while [ "${#batch[@]}" -lt "$NOW_PAR" ] && [ "$i" -le "$TO" ]; do
        tag="${TAGS[$((i-1))]}"
        d="$PROBES/$tag"
        # уже посчитан — пропускаем
        if has_result "$d"; then
            SKIP=$((SKIP+1)); i=$((i+1)); continue
        fi
        batch+=("$i:$tag"); i=$((i+1))
    done
    [ "${#batch[@]}" -eq 0 ] && continue

    for item in "${batch[@]}"; do
        tag="${item#*:}"
        d="$PROBES/$tag"
        deck="$(basename "$(find "$d" -maxdepth 1 -iname '*.DATA' | head -1)")"
        run_flow "$d" "$deck" "$d" 2 &
    done
    wait

    for item in "${batch[@]}"; do
        n="${item%%:*}"; tag="${item#*:}"
        if run_ok "$PROBES/$tag"; then
            printf "  %4d/%d %-18s ok  %6s c\n" "$n" "$TOTAL" "$tag" \
                   "$(elapsed_of "$PROBES/$tag")"
            DONE=$((DONE+1))
        else
            printf "  %4d/%d %-18s ${R}УПАЛ${N}\n" "$n" "$TOTAL" "$tag"
            FAIL=$((FAIL+1))
        fi
        # чистим тяжёлую статику сразу: она одинакова во всех вариантах
        strip_static "$PROBES/$tag"; rm -f "$PROBES/$tag"/*.UNRST 2>/dev/null
    done

    el=$(( $(date +%s) - START ))
    printf "  --- посчитано %d, пропущено %d, прошло %d мин\n" \
           "$DONE" "$SKIP" $((el/60))
done

EL=$(( $(date +%s) - START ))
echo
echo "${B}Итог${N}"
echo "  посчитано: $DONE, пропущено (уже были): $SKIP, упало: $FAIL"
echo "  время: $((EL/60)) мин"
disk_status
} 2>&1 | tee "$LOG"

echo
echo "${B}Матрица откликов${N}"
run_py probe_wells.py "$PROBES" analyze --runs /out --out /out/response_matrix.csv
