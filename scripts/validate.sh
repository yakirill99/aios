#!/usr/bin/env bash
# Проверка физической корректности результата прогона.
#   ./validate.sh                       последний прогон расписания
#   ./validate.sh ~/aios-data/runs/baseline
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

DIR="${1:-$RUNS_DIR/baseline}"
SM="$(find "$DIR" -name '*.SMSPEC' | head -1)"
[ -n "$SM" ] || { echo "${R}нет .SMSPEC в $DIR${N}"; exit 1; }

echo "${B}Проверка физики${N}  ${DIR/#$HOME/\~}"
run_py validate_output.py "$DIR" "/out/$(basename "$SM")" \
    --json /out/validation.json
RC=$?
echo
[ "$RC" -eq 0 ] && echo "${G}годен к сдаче${N}" || echo "${R}есть нарушения${N}"
exit $RC
