#!/usr/bin/env bash
# =========================================================================
# Прогон кандидатов + расчёт ЧДД по каждому.
#
#   ./run_candidates.sh              все, что сгенерированы
#   ./run_candidates.sh 1 200        варианты 1..200 (первая машина)
#   ./run_candidates.sh 201 400      варианты 201..400 (вторая машина)
#
# Пропускает уже посчитанные — можно прерывать и продолжать.
# Ничего не удаляет.
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

CANDS="${CANDS_DIR:-$DATA_ROOT/candidates}"
mkdir -p "$CANDS"
FROM="${1:-1}"; TO="${2:-999999}"
LOG="$LOGS_DIR/candidates_$(date +%Y%m%d_%H%M%S).log"

[ -f "$CANDS/manifest.json" ] || {
    echo "${R}нет $CANDS/manifest.json — сначала candidates.py make${N}"; exit 1; }

mapfile -t TAGS < <(python3 -c "
import json
m=json.load(open('$CANDS/manifest.json',encoding='utf-8'))
for c in m['candidates']: print(c['tag'])
")
TOTAL=${#TAGS[@]}
[ "$TO" -gt "$TOTAL" ] && TO=$TOTAL

{
echo "${B}Прогон кандидатов${N}"
echo "  всего: $TOTAL, обрабатываем ${FROM}..${TO}"
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
        if [ -f "$CANDS/$tag/chdd_input.csv" ]; then
            SKIP=$((SKIP+1)); i=$((i+1)); continue
        fi
        [ -d "$CANDS/$tag" ] || { i=$((i+1)); continue; }
        batch+=("$i:$tag"); i=$((i+1))
    done
    [ "${#batch[@]}" -eq 0 ] && continue

    for item in "${batch[@]}"; do
        tag="${item#*:}"; d="$CANDS/$tag"
        run_flow "$d" "$(deck_name "$d")" "$d" 2 &
    done
    wait

    for item in "${batch[@]}"; do
        n="${item%%:*}"; tag="${item#*:}"; d="$CANDS/$tag"
        if run_ok "$d"; then
            # сразу считаем CSV: сводки потом можно удалить, а он лёгкий
            run_py summary_to_csv.py "$d" \
                "/out/$(basename "$(deck_name "$d")" .DATA).SMSPEC" \
                -o /out/chdd_input.csv --start "$CUT_DATE" > /dev/null 2>&1
            printf "  %4d/%d %-26s ok  %6s c\n" "$n" "$TOTAL" "$tag" \
                   "$(elapsed_of "$d")"
            DONE=$((DONE+1))
        else
            printf "  %4d/%d %-26s ${R}УПАЛ${N}" "$n" "$TOTAL" "$tag"
            if [ ! -f "$d/time.log" ]; then
                echo "  (контейнер убит — не хватило памяти?)"
            else
                echo "  $(grep -iE '^Error' "$d"/*.PRT 2>/dev/null | head -1 | cut -c1-60)"
            fi
            FAIL=$((FAIL+1))
            # три падения подряд в начале — что-то системное, дальше нет смысла
            if [ "$DONE" -eq 0 ] && [ "$FAIL" -ge 3 ]; then
                echo "${R}три падения подряд без единого успеха — останавливаюсь.${N}"
                echo "  посмотрите: cat $d/flow.log | tail -20"
                exit 1
            fi
        fi
        strip_static "$d"
        rm -f "$d"/*.UNRST 2>/dev/null
    done

    el=$(( $(date +%s) - START ))
    printf "  --- готово %d, пропущено %d, прошло %d мин\n" \
           "$DONE" "$SKIP" $((el/60))
done

EL=$(( $(date +%s) - START ))
echo
echo "${B}Итог${N}"
echo "  посчитано: $DONE, пропущено: $SKIP, упало: $FAIL"
echo "  время: $((EL/60)) мин"
disk_status
} 2>&1 | tee "$LOG"

echo
echo "Ранжирование:  python3 $SCRIPT_DIR/candidates.py rank"
