#!/usr/bin/env bash
# =========================================================================
# Проверка, что весь контур работает: образ, симулятор, чтение сводок.
#   ./smoke_test.sh
# =========================================================================
set -uo pipefail
cd "$(dirname "$0")"
source lib_common.sh

WORK="$DATA_ROOT/smoke"
mkdir -p "$WORK/out"

echo "${B}1. Версия симулятора${N}"
docker run --rm $USERFLAG "$IMAGE" flow --version || {
    echo "${R}образ не собран${N}"; exit 1; }

echo
echo "${B}2. Тестовая модель${N}"
if [ ! -d "$WORK/opm-tests" ]; then
    echo "  клонирую тестовые данные (~390 МБ, один раз)"
    git clone --depth 1 https://github.com/OPM/opm-tests.git "$WORK/opm-tests"
else
    echo "  уже есть"
fi

echo
echo "${B}3. Прогон SPE1${N}"
rm -rf "$WORK/out"; mkdir -p "$WORK/out"
run_flow "$WORK/opm-tests/spe1" "SPE1CASE1.DATA" "$WORK/out" 2
if run_ok "$WORK/out"; then
    echo "  ${G}успешно${N}, $(elapsed_of "$WORK/out") с, "\
"память $(awk "BEGIN{printf \"%.0f\", $(maxrss_of "$WORK/out")/1024}") МБ"
else
    echo "  ${R}упал${N}:"
    grep -iE "^Error|Problem with keyword" "$WORK/out"/*.PRT 2>/dev/null | head -5
    tail -5 "$WORK/out/flow.log"; exit 1
fi

echo
echo "${B}4. Файлы${N}"
ls -lhS "$WORK/out" | head -10 | sed 's/^/  /'

echo
echo "${B}5. Чтение сводок${N}"
# -i обязателен: без него stdin не пробрасывается и heredoc уходит в пустоту
docker run --rm -i $USERFLAG -v "$WORK/out":/out "$IMAGE" python3 - <<'PY'
from resdata.summary import Summary
import glob, os, sys
f = glob.glob("/out/*.SMSPEC")
if not f:
    print("  SMSPEC не найден"); sys.exit(1)
s = Summary(f[0]); keys = sorted(s.keys())
print(f"  файл: {os.path.basename(f[0])}")
print(f"  шагов: {len(s)}, векторов: {len(keys)}")
print(f"  первые 12: {keys[:12]}")
for k in ("FOPT", "FOPR", "FGPR", "FPR"):
    if k in s:
        print(f"  {k} в конце: {s.numpy_vector(k)[-1]:.2f}"); break
PY

echo
echo "${G}Контур рабочий.${N} Дальше: ./benchmark.sh"
