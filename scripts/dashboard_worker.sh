#!/usr/bin/env bash
# Воркер очереди прогонов.
#
#   ./scripts/dashboard_worker.sh
#
# Держите его открытым: пока он не работает, задания из интерфейса
# просто лежат в очереди. Дашборд сам расчёт не запускает — в его
# контейнере нет ни OPM Flow, ни прав на запись в результаты.
#
# Остановка — Ctrl+C. Текущий шаг симулятора при этом досчитывается.

set -euo pipefail

cd "$(dirname "$0")/.."
AIOS_ROOT="$(pwd)"
export AIOS_ROOT
export DATA_ROOT="${AIOS_DATA_DIR:-$HOME/aios-data}"
export AIOS_QUEUE_DIR="$DATA_ROOT/queue"
export PYTHONPATH="$AIOS_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SCRIPT="${AIOS_RUN_SCRIPT:-$AIOS_ROOT/scripts/rolling_horizon.py}"
export AIOS_RUN_SCRIPT="$SCRIPT"

echo "Репозиторий: $AIOS_ROOT"
echo "Данные:      $DATA_ROOT"
echo "Очередь:     $AIOS_QUEUE_DIR"
echo "Цикл:        $SCRIPT"
echo

[[ -d "$AIOS_QUEUE_DIR" ]] || { echo "Нет каталога очереди. Запустите ./scripts/dashboard_preflight.sh --fix"; exit 1; }
[[ -w "$AIOS_QUEUE_DIR" ]] || { echo "Каталог очереди недоступен на запись. Запустите ./scripts/dashboard_preflight.sh --fix"; exit 1; }

if [[ ! -f "$SCRIPT" ]]; then
    echo "ВНИМАНИЕ: не найден $SCRIPT"
    echo "Пойдут только задания в режиме проигрывания записи."
    echo
fi

case "${1:-}" in
    --dry-run|--once)
        exec python3 -m aios.dashboard.worker "$1"
        ;;
    "")
        echo "Жду задания. Ctrl+C для остановки."
        echo
        exec python3 -m aios.dashboard.worker
        ;;
    *)
        echo "Использование: $0 [--once|--dry-run]" >&2
        exit 1
        ;;
esac
