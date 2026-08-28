#!/usr/bin/env bash
# Проверка физической корректности результата прогона.
#   ./validate.sh                       последний прогон расписания
#   ./validate.sh ~/aios-data/runs/baseline
#   ./validate.sh ~/aios-data/rolling/runs/FINAL --since 2014-01-01
#
# Без --since проверяется вся дека, включая историю организаторов.
# Нарушение в истории — вопрос к ним, нарушение в управляющем окне —
# наша дисквалификация. Перед сдачей запускать ИМЕННО с --since.
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

DIR="${1:-$RUNS_DIR/baseline}"
shift || true
EXTRA=("$@")
# по умолчанию сверяем и управляющий период тоже
if [ ${#EXTRA[@]} -eq 0 ]; then EXTRA=(--since "$CUT_DATE"); fi
SM="$(find "$DIR" -name '*.SMSPEC' | head -1)"
[ -n "$SM" ] || { echo "${R}нет .SMSPEC в $DIR${N}"; exit 1; }

echo "${B}Проверка физики${N}  ${DIR/#$HOME/\~}"
run_py validate_output.py "$DIR" "/out/$(basename "$SM")" \
    --json /out/validation.json "${EXTRA[@]}"
RC=$?
echo
# ВАЖНО: внутри контейнера каталог прогона смонтирован как /out, поэтому
# скрипт печатает "/out/validation.json". На хосте файл лежит здесь:
DIR_ABS="$(cd "$DIR" && pwd)"
echo "  отчёт на хосте: ${DIR_ABS}/validation.json"
echo "  посмотреть:     python3 -m json.tool ${DIR_ABS}/validation.json | head -40"
[ "$RC" -eq 0 ] && echo "${G}годен к сдаче${N}" || echo "${R}есть нарушения${N}"
exit $RC
