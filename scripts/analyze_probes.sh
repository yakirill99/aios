#!/usr/bin/env bash
# Разбор возмущающих прогонов -> матрица откликов.
# resdata стоит в контейнере, поэтому анализ идёт там же.
#
#   ./analyze_probes.sh              порог отклика 2%
#   ./analyze_probes.sh 0.01         порог 1% (больше связей, больше шума)
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

MIN="${1:-0.02}"
MINABS="${2:-0.3}"
[ -f "$PROBES_DIR/manifest.json" ] || {
    echo "${R}нет $PROBES_DIR/manifest.json${N}"; exit 1; }

echo "${B}Разбор возмущений${N}  пороги: отн. ${MIN}, абс. ${MINABS} м3/сут"
run_py probe_wells.py "$PROBES_DIR" analyze \
    --runs /out --out /out/response_matrix.csv \
    --min-response "$MIN" --min-abs "$MINABS"

echo
echo "матрица: $PROBES_DIR/response_matrix.csv"
